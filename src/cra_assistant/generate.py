"""Calling the model, and refusing to believe it uncritically.

Two things happen here that are not "send prompt, print reply":

* **Citations are checked against what was actually retrieved.** A model that
  cites a segment id it was never shown has invented a source, and an invented
  citation is worse than no answer — it looks exactly like a real one.
* **Abstention is enforced, not requested.** An answer with no usable citation
  is converted into an abstention rather than shown. Asking the model nicely to
  abstain is a prompt; turning an uncited answer into an abstention is a rule.
"""

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from cra_assistant.models import Segment, TrustTier
from cra_assistant.prompt import build_messages
from cra_assistant.retrieve import Retriever
from cra_assistant.telemetry import (
    CallRecord,
    estimate_cost,
    new_request_id,
    timed,
    utc_now,
)

DEFAULT_MODEL = "gpt-4o-mini"
"""Overridable with ``CRA_MODEL``. Deliberately a cheap model: this step is
about whether the path works, not about answer quality."""

MAX_COMPLETION_TOKENS = 900
MAX_CALLS_PER_INVOCATION = 2
"""A hard ceiling on model calls per process, so a retry loop cannot run up a
bill. There is no retry today; the cap exists before the loop does."""

API_KEY_VARIABLE = "OPENAI_API_KEY"


class MissingApiKeyError(RuntimeError):
    """Raised when the key is absent.

    The message names the variable and never its value — not even a prefix or a
    length, both of which have leaked keys into logs before.
    """

    def __init__(self) -> None:
        super().__init__(
            f"{API_KEY_VARIABLE} is not set. Export it, or put it in .env at the "
            "repository root (gitignored, and read on startup). An exported value "
            "wins over .env. It is never read from anywhere else and never logged."
        )


class CallBudgetExceededError(RuntimeError):
    pass


PROVIDER_HINTS = {
    "insufficient_quota": (
        "the account has no credit left — this is a billing state, not a rate limit. "
        "Add credit at platform.openai.com/settings/organization/billing."
    ),
    "credit_balance_exhausted": (
        "the account's credit balance is exhausted. Add credit at "
        "platform.openai.com/settings/organization/billing."
    ),
    "invalid_api_key": "the key was rejected. Check OPENAI_API_KEY.",
    "model_not_found": "the model id is unknown to this account. Set CRA_MODEL to one it has.",
    "context_length_exceeded": "the prompt was too long. Retry with a smaller -k.",
}
"""Actionable guidance keyed by the provider's structured error code.

Only the code is read, never the provider's message. `RateLimitError` on its own
sent us hunting for a throttle when the real state was an empty balance, so the
distinction is worth surfacing — but the message body may echo request data and
stays unread.
"""


def provider_error_code(error: Exception) -> str | None:
    """The provider's short error code, if it exposes one.

    Reads only `code` and `type` from a structured body. Both are enum-like
    identifiers; neither is free-form text.
    """
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        for field in ("code", "type"):
            value = body.get(field)
            if isinstance(value, str) and value:
                return value
    return None


class ChatClient(Protocol):
    """The slice of the OpenAI client this module uses.

    Narrow on purpose: it makes the fake in the tests three lines long, and it
    is the seam a different provider would be swapped in at.
    """

    def complete(
        self, *, model: str, messages: Sequence[dict[str, str]], max_tokens: int
    ) -> Any: ...


@dataclass
class OpenAiChatClient:
    """Thin adapter over the OpenAI SDK."""

    api_key: str
    _client: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=self.api_key)

    def complete(self, *, model: str, messages: Sequence[dict[str, str]], max_tokens: int) -> Any:
        return self._client.chat.completions.create(
            model=model,
            messages=list(messages),
            max_tokens=max_tokens,
            temperature=0,
            response_format={"type": "json_object"},
        )

    def __repr__(self) -> str:
        # Never let a key reach a traceback or a debugger session.
        return "OpenAiChatClient(api_key=<redacted>)"


def client_from_environment() -> OpenAiChatClient:
    api_key = os.environ.get(API_KEY_VARIABLE, "").strip()
    if not api_key:
        raise MissingApiKeyError
    return OpenAiChatClient(api_key=api_key)


@dataclass(frozen=True, slots=True)
class Answer:
    """What `ask` produces, whether or not the model was useful."""

    question: str
    text: str
    citations: tuple[Segment, ...]
    abstained: bool
    reason: str
    retrieved: tuple[Segment, ...]
    request_id: str
    model: str

    @property
    def cited_untrusted(self) -> tuple[Segment, ...]:
        return tuple(segment for segment in self.citations if segment.tier is not TrustTier.TRUSTED)


class CallBudget:
    def __init__(self, limit: int = MAX_CALLS_PER_INVOCATION) -> None:
        self.limit = limit
        self.used = 0

    def spend(self) -> None:
        if self.used >= self.limit:
            raise CallBudgetExceededError(f"refusing to exceed {self.limit} model calls")
        self.used += 1


def _parse_reply(content: str) -> dict[str, Any]:
    """Read the model's JSON. A malformed reply is an abstention, not a crash."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {
            "answer": "",
            "citations": [],
            "abstained": True,
            "reason": "the model did not return usable JSON",
        }
    if not isinstance(payload, dict):
        return {
            "answer": "",
            "citations": [],
            "abstained": True,
            "reason": "unexpected reply shape",
        }
    return payload


def enforce_citations(
    payload: dict[str, Any], retrieved: Sequence[Segment]
) -> tuple[str, tuple[Segment, ...], bool, str]:
    """Turn a model reply into a grounded answer or an abstention.

    Returns ``(text, cited segments, abstained, reason)``. Citations that were
    not in the retrieved set are dropped and reported: the model cannot cite
    what it was not shown, so such an id is fabricated whether or not the
    segment exists elsewhere in the corpus.
    """
    by_id = {segment.id: segment for segment in retrieved}
    claimed = payload.get("citations") or []
    if not isinstance(claimed, list):
        claimed = []

    cited = tuple(by_id[str(one)] for one in claimed if str(one) in by_id)
    invented = [str(one) for one in claimed if str(one) not in by_id]
    text = str(payload.get("answer") or "").strip()
    reason = str(payload.get("reason") or "").strip()

    if payload.get("abstained"):
        return "", (), True, reason or "the model abstained"

    if invented:
        note = f"cited segments that were not retrieved: {', '.join(sorted(invented))}"
        reason = f"{reason} ({note})" if reason else note

    if not text:
        return "", (), True, reason or "the model returned an empty answer"

    if not cited:
        # The rule that makes citation mandatory rather than encouraged.
        note = "the answer cited no retrieved segment, so it is not grounded"
        return "", (), True, f"{reason}; {note}" if reason else note

    return text, cited, False, reason


def ask(
    question: str,
    retriever: Retriever,
    *,
    client: ChatClient,
    k: int = 8,
    model: str | None = None,
    budget: CallBudget | None = None,
) -> tuple[Answer, CallRecord]:
    """Retrieve, prompt, generate, and check the result before returning it."""
    model = model or os.environ.get("CRA_MODEL") or DEFAULT_MODEL
    budget = budget or CallBudget()
    request_id = new_request_id()
    started_at = utc_now()

    retrieved = tuple(retriever.retrieve(question, k))
    if not retrieved:
        # No call, no cost: there is nothing to ground an answer in.
        answer = Answer(
            question=question,
            text="",
            citations=(),
            abstained=True,
            reason="retrieval returned no segments for this question",
            retrieved=(),
            request_id=request_id,
            model=model,
        )
        record = CallRecord(
            request_id=request_id,
            started_at=started_at,
            operation="ask",
            model=model,
            latency_ms=0,
            outcome="abstained",
            retrieved=0,
            citations=0,
        )
        return answer, record

    messages = build_messages(question, retrieved)
    budget.spend()

    with timed() as elapsed:
        try:
            response = client.complete(
                model=model, messages=messages, max_tokens=MAX_COMPLETION_TOKENS
            )
        except Exception as error:
            record = CallRecord(
                request_id=request_id,
                started_at=started_at,
                operation="ask",
                model=model,
                latency_ms=elapsed["latency_ms"],
                outcome="error",
                # Class name and structured code only: provider error *messages*
                # can echo request data, so they are never read.
                error_type=type(error).__name__,
                error_code=provider_error_code(error),
                retrieved=len(retrieved),
            )
            raise GenerationError(record) from error

    payload = _parse_reply(response.choices[0].message.content or "")
    text, cited, abstained, reason = enforce_citations(payload, retrieved)

    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

    record = CallRecord(
        request_id=request_id,
        started_at=started_at,
        operation="ask",
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=elapsed["latency_ms"],
        estimated_cost_usd=estimate_cost(model, prompt_tokens, completion_tokens),
        outcome="abstained" if abstained else "answered",
        retrieved=len(retrieved),
        citations=len(cited),
    )
    answer = Answer(
        question=question,
        text=text,
        citations=cited,
        abstained=abstained,
        reason=reason,
        retrieved=retrieved,
        request_id=request_id,
        model=model,
    )
    return answer, record


class GenerationError(RuntimeError):
    """The provider call failed. Carries the telemetry record so the caller can
    still log the attempt; the underlying exception is chained, not swallowed."""

    def __init__(self, record: CallRecord) -> None:
        detail = record.error_type or "unknown error"
        if record.error_code:
            detail = f"{detail}: {record.error_code}"
        hint = PROVIDER_HINTS.get(record.error_code or "")
        super().__init__(f"model call failed ({detail})" + (f"\n{hint}" if hint else ""))
        self.record = record
