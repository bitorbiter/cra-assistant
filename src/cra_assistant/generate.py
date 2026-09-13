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
import re
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


def span_supports(span: str, segment: Segment) -> bool:
    """Is this span verbatim in this segment?

    Deterministic substring match, and deliberately so. Asking a model whether a
    span supports a claim would put a second model in reach of the same
    untrusted content, and replace a check that cannot be argued with by one
    that can (ADR-0015).
    """
    cleaned = normalise_span(span)
    if len(cleaned) < MINIMUM_SPAN_CHARACTERS:
        return False
    return cleaned.casefold() in normalise_span(segment.text).casefold()


@dataclass(frozen=True, slots=True)
class CitationCheck:
    """Why each claimed citation was kept or dropped."""

    claimed: int
    kept: tuple[Segment, ...]
    not_retrieved: tuple[str, ...]
    unsupported: tuple[str, ...]
    """Cited a retrieved segment, but the span was not in it."""
    span_missing: tuple[str, ...]
    """Cited a retrieved segment with no span at all, or one too short."""

    def failure_note(self) -> str:
        parts = []
        if self.not_retrieved:
            parts.append(f"cited segments that were not retrieved: {', '.join(self.not_retrieved)}")
        if self.unsupported:
            parts.append(
                "cited segments whose quoted span is not in them: " + ", ".join(self.unsupported)
            )
        if self.span_missing:
            parts.append(
                "cited segments with no usable supporting span: " + ", ".join(self.span_missing)
            )
        return "; ".join(parts)


def check_citations(payload: dict[str, Any], retrieved: Sequence[Segment]) -> CitationCheck:
    """Keep only citations whose span is verbatim in the segment they name.

    Two independent reasons to drop one, reported separately because they mean
    different things: an id that was never retrieved is a fabricated source, and
    a span that is not in a real segment is a fabricated *claim about* a real
    source. The second is the one that got through before this check existed.
    """
    by_id = {segment.id: segment for segment in retrieved}
    claimed = payload.get("citations") or []
    if not isinstance(claimed, list):
        claimed = []

    kept: list[Segment] = []
    not_retrieved: list[str] = []
    unsupported: list[str] = []
    span_missing: list[str] = []

    for entry in claimed:
        if isinstance(entry, dict):
            identifier, span = str(entry.get("id", "")), str(entry.get("span", ""))
        else:
            # A bare id, from a model that ignored the contract.
            identifier, span = str(entry), ""
        segment = by_id.get(identifier)
        if segment is None:
            not_retrieved.append(identifier)
        elif not normalise_span(span) or len(normalise_span(span)) < MINIMUM_SPAN_CHARACTERS:
            span_missing.append(identifier)
        elif not span_supports(span, segment):
            unsupported.append(identifier)
        elif segment not in kept:
            kept.append(segment)

    return CitationCheck(
        claimed=len(claimed),
        kept=tuple(kept),
        not_retrieved=tuple(sorted(set(not_retrieved))),
        unsupported=tuple(sorted(set(unsupported))),
        span_missing=tuple(sorted(set(span_missing))),
    )


STATUTORY_ASSERTION = re.compile(
    r"""
      \b(?:the\s+)?(?:Regulation|CRA)\b[^.]{0,60}?
        \b(?:requires?|provides?|states?|establishes?|mandates?|prohibits?|exempts?|applies)\b
    | \b(?:Article|Annex|Recital)\s+[IVXLC0-9][^.]{0,60}?
        \b(?:requires?|provides?|states?|establishes?|mandates?|exempts?|says?|sets\s+out)\b
    | \b(?:manufacturers?|importers?|distributors?|stewards?)\b[^.]{0,60}?
        \b(?:shall|must|are\s+required\s+to|are\s+exempt|is\s+exempt)\b
    | \b(?:is|are)\s+exempt\s+from\b
    | \bunder\s+(?:the\s+)?(?:Regulation|CRA)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
"""Sentences asserting what the law requires.

A keyword heuristic, and the weakest part of ADR-0016. It is deterministic and
inspectable, which an LLM classifier would not be — a classifier would be
reachable by the same untrusted content it was judging.
"""

ATTRIBUTION = re.compile(
    r"""
      \baccording\s+to\b | \bthe\s+community\b
    | \bcommunity\s+(?:note|FAQ|interpretation|guidance)\b
    | \bpractitioners?\b | \bcommentary\b | \bworking\s+group\b | \bORC\s*WG\b
    | \bnot\s+(?:settled|confirmed)\b | \bunconfirmed\b | \bsome\s+(?:argue|read|say)\b
    | \bis\s+interpreted\b | \breads?\s+this\s+as\b | \bthird[- ]party\b
    | \buntrusted\s+(?:content|source)\b | \bforum\b | \bissue\s+tracker\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
"""Markers that a sentence is reporting somebody's claim rather than the law.

The escape the rule depends on. A claim about what a community document says is
a claim about a document, and untrusted support is the right support for it.
"""

SENTENCE = re.compile(r"[^.!?]+[.!?]?")


def unattributed_statutory_claims(text: str) -> tuple[str, ...]:
    """Sentences asserting what the law requires without naming a source."""
    claims = []
    for match in SENTENCE.finditer(text):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        if STATUTORY_ASSERTION.search(sentence) and not ATTRIBUTION.search(sentence):
            claims.append(sentence)
    return tuple(claims)


def enforce_citations(
    payload: dict[str, Any],
    retrieved: Sequence[Segment],
    *,
    tier_rule: bool = True,
) -> tuple[str, tuple[Segment, ...], bool, str]:
    """Turn a model reply into a grounded answer or an abstention.

    Returns ``(text, cited segments, abstained, reason)``. A citation survives
    only if the segment was retrieved **and** the model quoted a span that is
    verbatim in it. Checking retrieval alone let three of five successful
    attacks through by asserting a claim beside a real citation that did not
    support it (ADR-0015).
    """
    check = check_citations(payload, retrieved)
    text = str(payload.get("answer") or "").strip()
    reason = str(payload.get("reason") or "").strip()

    if payload.get("abstained"):
        return "", (), True, reason or "the model abstained"

    note = check.failure_note()
    if note:
        reason = f"{reason} ({note})" if reason else note

    if not text:
        return "", (), True, reason or "the model returned an empty answer"

    if not check.kept:
        grounded = "the answer has no citation supported by a verbatim span, so it is not grounded"
        return "", (), True, f"{reason}; {grounded}" if reason else grounded

    # ADR-0016: a claim about what the Regulation REQUIRES needs a trusted
    # segment behind it. Deliberately narrow — claims about community practice,
    # open questions or disagreement stay answerable from untrusted support,
    # because a rule that demanded trusted support for everything would empty
    # the untrusted tier of purpose and score well for doing so.
    statutory = unattributed_statutory_claims(text) if tier_rule else ()
    if statutory and not any(one.tier is TrustTier.TRUSTED for one in check.kept):
        note = (
            "states what the Regulation requires but is supported only by untrusted "
            f"sources: {statutory[0][:120]!r}. Attribute the claim to its source, or "
            "cite the regulation"
        )
        return "", (), True, f"{reason}; {note}" if reason else note

    return text, check.kept, False, reason


def ask(
    question: str,
    retriever: Retriever,
    *,
    client: ChatClient,
    k: int = 8,
    model: str | None = None,
    budget: CallBudget | None = None,
    tier_rule: bool = True,
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
    text, cited, abstained, reason = enforce_citations(payload, retrieved, tier_rule=tier_rule)

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
