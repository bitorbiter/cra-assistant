"""Calling the model, and refusing to believe it uncritically.

Two things happen here that are not "send prompt, print reply":

* **Citations are checked against what the model was actually shown.** A model
  that cites a segment id it was never shown has invented a source, and an
  invented citation is worse than no answer — it looks exactly like a real one.
  "Shown" means the delivered text, clipped as the prompt clipped it, never the
  full stored segment.
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
from cra_assistant.prompt import DeliveredSegment, assemble_prompt
from cra_assistant.retrieve import Retriever
from cra_assistant.telemetry import (
    CallRecord,
    estimate_cost,
    new_request_id,
    timed,
    utc_now,
)

DEFAULT_TEMPERATURE = 0.0
"""Production temperature.

At 0 the provider is *near*-deterministic, not guaranteed deterministic, so a
repeated attack run measures reproducibility within this harness rather than
stability of the model's behaviour. Reports must say which of those they mean.
"""

DEFAULT_MODEL = "gpt-4o-mini-2024-07-18"
"""A dated snapshot, never the floating ``gpt-4o-mini`` alias (ADR-0010).

The alias is repointed by the provider without notice, which would make it the
one input to this system that changes underneath a committed baseline while
every source it reads is checksummed and pinned. Overridable with ``CRA_MODEL``;
deliberately a cheap model, because the question is whether the path works."""

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
    temperature: float = DEFAULT_TEMPERATURE
    _client: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=self.api_key)

    def complete(self, *, model: str, messages: Sequence[dict[str, str]], max_tokens: int) -> Any:
        return self._client.chat.completions.create(
            model=model,
            messages=list(messages),
            max_tokens=max_tokens,
            temperature=self.temperature,
            response_format={"type": "json_object"},
        )

    def __repr__(self) -> str:
        # Never let a key reach a traceback or a debugger session.
        return "OpenAiChatClient(api_key=<redacted>)"


def client_from_environment(temperature: float = DEFAULT_TEMPERATURE) -> OpenAiChatClient:
    api_key = os.environ.get(API_KEY_VARIABLE, "").strip()
    if not api_key:
        raise MissingApiKeyError
    return OpenAiChatClient(api_key=api_key, temperature=temperature)


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
    spans: tuple[str, ...] = ()
    """The validated quotation behind each citation, in the same order.

    An answer that cannot show its supporting quotation forces the reader back
    into the corpus to check it, which is the work this project exists to save.
    """

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


MINIMUM_SPAN_CHARACTERS = 20
"""Below this a span is too short to support anything.

A three-word quotation appears in almost any document, so accepting one would
make the check pass on coincidence. Twenty characters is not a tuned number; it
is a floor below which the match stops meaning anything.
"""


def normalise_span(text: str) -> str:
    """Collapse whitespace so a reflowed quotation still matches.

    Whitespace only. Nothing else is normalised: lowercasing or stripping
    punctuation would let a paraphrase through, and the point of the check is
    that the model copied rather than reconstructed.
    """
    return " ".join(text.split())


def span_supports(span: str, delivered: DeliveredSegment) -> bool:
    """Is this span verbatim in the text the model received for this segment?

    The delivered text, not the stored segment: a span from past the prompt's
    cutoff quotes something the model was never shown, so it can only have come
    from memory or from an attacker's own quotation of the full document.

    Deterministic substring match, and deliberately so. Asking a model whether a
    span supports a claim would put a second model in reach of the same
    untrusted content, and replace a check that cannot be argued with by one
    that can (ADR-0015).
    """
    cleaned = normalise_span(span)
    if len(cleaned) < MINIMUM_SPAN_CHARACTERS:
        return False
    return _contains(delivered.text, cleaned)


def _contains(text: str, cleaned_span: str) -> bool:
    return cleaned_span.casefold() in normalise_span(text).casefold()


@dataclass(frozen=True, slots=True)
class CitationCheck:
    """Why each claimed citation was kept or dropped."""

    claimed: int
    kept: tuple[Segment, ...]
    not_retrieved: tuple[str, ...]
    unsupported: tuple[str, ...]
    """Cited a retrieved segment, but the span was not in what was delivered."""
    undelivered: tuple[str, ...]
    """Subset of ``unsupported``: the span is in the full stored segment, past
    the cutoff. The model quoted text it was not shown."""
    span_missing: tuple[str, ...]
    """Cited a retrieved segment with no span at all, or one too short."""
    spans: tuple[str, ...] = ()
    """The validated quotation for each kept citation, in the same order.

    Kept so a reader can see *why* a citation counts as support without
    re-reading the segment. Dropping it was why the CLI could only print ids."""

    def failure_note(self) -> str:
        parts = []
        if self.not_retrieved:
            parts.append(f"cited segments that were not retrieved: {', '.join(self.not_retrieved)}")
        if self.unsupported:
            parts.append(
                "cited segments whose quoted span is not in them: " + ", ".join(self.unsupported)
            )
        if self.undelivered:
            parts.append(
                "of which quoted text past the point the segment was clipped, which "
                "the model was not shown: " + ", ".join(self.undelivered)
            )
        if self.span_missing:
            parts.append(
                "cited segments with no usable supporting span: " + ", ".join(self.span_missing)
            )
        return "; ".join(parts)


def check_citations(
    payload: dict[str, Any], delivered: Sequence[DeliveredSegment]
) -> CitationCheck:
    """Keep only citations whose span is verbatim in the delivered text they name.

    Two independent reasons to drop one, reported separately because they mean
    different things: an id that was never retrieved is a fabricated source, and
    a span that is not in a real segment is a fabricated *claim about* a real
    source. The second is the one that got through before this check existed.
    """
    # Several delivered passages can share one segment id, because retrieval
    # scores paragraphs and answers cite articles (ADR-0018). A span counts if it
    # is verbatim in any passage of the segment the model named.
    by_id: dict[str, list[DeliveredSegment]] = {}
    for one in delivered:
        by_id.setdefault(one.segment.id, []).append(one)
    claimed = payload.get("citations") or []
    if not isinstance(claimed, list):
        claimed = []

    kept: list[Segment] = []
    spans: list[str] = []
    not_retrieved: list[str] = []
    unsupported: list[str] = []
    undelivered: list[str] = []
    span_missing: list[str] = []

    for entry in claimed:
        if isinstance(entry, dict):
            identifier, span = str(entry.get("id", "")), str(entry.get("span", ""))
        else:
            # A bare id, from a model that ignored the contract.
            identifier, span = str(entry), ""
        shown_all = by_id.get(identifier)
        if shown_all is None:
            not_retrieved.append(identifier)
        elif not normalise_span(span) or len(normalise_span(span)) < MINIMUM_SPAN_CHARACTERS:
            span_missing.append(identifier)
        elif not any(span_supports(span, shown) for shown in shown_all):
            unsupported.append(identifier)
            # In the segment but not in any passage of it that was delivered:
            # quoted from text the model was never shown, whether clipped away
            # or in a paragraph that did not match.
            if _contains(shown_all[0].passage.full_text, normalise_span(span)):
                undelivered.append(identifier)
        elif shown_all[0].segment not in kept:
            kept.append(shown_all[0].segment)
            spans.append(normalise_span(span))

    return CitationCheck(
        claimed=len(claimed),
        kept=tuple(kept),
        spans=tuple(spans),
        not_retrieved=tuple(sorted(set(not_retrieved))),
        unsupported=tuple(sorted(set(unsupported))),
        undelivered=tuple(sorted(set(undelivered))),
        span_missing=tuple(sorted(set(span_missing))),
    )


def enforce_citations(
    payload: dict[str, Any],
    delivered: Sequence[DeliveredSegment],
) -> tuple[str, tuple[Segment, ...], bool, str, tuple[str, ...]]:
    """Turn a model reply into a grounded answer or an abstention.

    Returns ``(text, cited segments, abstained, reason)``. A citation survives
    only if the segment was delivered **and** the model quoted a span that is
    verbatim in the delivered text. Checking retrieval alone let three of five successful
    attacks through by asserting a claim beside a real citation that did not
    support it (ADR-0015).
    """
    check = check_citations(payload, delivered)
    text = str(payload.get("answer") or "").strip()
    reason = str(payload.get("reason") or "").strip()

    if payload.get("abstained"):
        return "", (), True, reason or "the model abstained", ()

    note = check.failure_note()
    if note:
        reason = f"{reason} ({note})" if reason else note

    if not text:
        return "", (), True, reason or "the model returned an empty answer", ()

    if not check.kept:
        grounded = "the answer has no citation supported by a verbatim span, so it is not grounded"
        return "", (), True, (f"{reason}; {grounded}" if reason else grounded), ()

    # ADR-0016 required trusted support for statements of what the Regulation
    # requires. Deleted on 2026-09-14: breaches did not move. Do not restore it
    # without a new measurement.
    return text, check.kept, False, reason, check.spans


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

    prompt = assemble_prompt(question, retrieved)
    messages = prompt.messages
    budget.spend()

    failure: Exception | None = None
    with timed() as elapsed:
        try:
            response = client.complete(
                model=model, messages=messages, max_tokens=MAX_COMPLETION_TOKENS
            )
        except Exception as error:
            failure = error

    # Built after the timer's context has exited. Inside it, `latency_ms` is
    # still 0, so every failed call used to be recorded as instantaneous — the
    # slow failures, the ones worth seeing, most of all.
    if failure is not None:
        record = CallRecord(
            request_id=request_id,
            started_at=started_at,
            operation="ask",
            model=model,
            latency_ms=elapsed["latency_ms"],
            outcome="error",
            # Class name and structured code only: provider error *messages*
            # can echo request data, so they are never read.
            error_type=type(failure).__name__,
            error_code=provider_error_code(failure),
            retrieved=len(retrieved),
            segments_truncated=prompt.segments_truncated,
            characters_dropped=prompt.characters_dropped,
        )
        raise GenerationError(record) from failure

    payload = _parse_reply(response.choices[0].message.content or "")
    text, cited, abstained, reason, spans = enforce_citations(payload, prompt.delivered)

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
        segments_truncated=prompt.segments_truncated,
        characters_dropped=prompt.characters_dropped,
        citations=len(cited),
    )
    answer = Answer(
        question=question,
        text=text,
        citations=cited,
        spans=spans,
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
