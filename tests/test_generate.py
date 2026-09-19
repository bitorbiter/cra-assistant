"""Generation, citation enforcement and abstention. The model is a fake."""

import json
import os
from dataclasses import dataclass
from typing import Any

import pytest

from cra_assistant.generate import (
    API_KEY_VARIABLE,
    Answer,
    CallBudget,
    CallBudgetExceededError,
    GenerationError,
    MissingApiKeyError,
    OpenAiChatClient,
    ask,
    client_from_environment,
    enforce_citations,
    provider_error_code,
)
from cra_assistant.models import Segment, SegmentKind, TrustTier
from cra_assistant.prompt import MAX_SEGMENT_CHARS, DeliveredSegment, deliver
from cra_assistant.retrieve import Bm25Retriever


def segment(identifier: str, text: str, tier: TrustTier = TrustTier.TRUSTED) -> Segment:
    return Segment(
        id=identifier,
        source_id="a-source",
        tier=tier,
        kind=SegmentKind.ARTICLE,
        number=identifier.rsplit(":", 1)[-1],
        title="",
        text=text,
        citation=f"Some Work, {identifier}",
        source_sha256="sha256:" + "a" * 64,
        content_sha256="sha256:" + "b" * 64,
        lang="en",
        order=0,
    )


SEGMENTS = [
    segment("doc:article:3", "Definitions: manufacturer means a natural or legal person."),
    segment("doc:article:13", "Obligations of manufacturer under this regulation."),
]


def delivered(segments: list[Segment]) -> list[DeliveredSegment]:
    return [deliver(one) for one in segments]


SUPPORTED = {"id": "doc:article:3", "span": "manufacturer means a natural or legal person"}
"""A citation whose span really is in the segment it names."""


@dataclass
class FakeUsage:
    prompt_tokens: int = 1200
    completion_tokens: int = 80


class FakeClient:
    """Returns a canned JSON reply and records what it was asked."""

    def __init__(self, payload: Any, *, fail: Exception | None = None) -> None:
        self.payload = payload
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, model: str, messages: Any, max_tokens: int) -> Any:
        self.calls.append({"model": model, "messages": list(messages), "max_tokens": max_tokens})
        if self.fail:
            raise self.fail
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice], "usage": FakeUsage()})()


def retriever() -> Bm25Retriever:
    return Bm25Retriever(SEGMENTS)


# --- citation enforcement, as a pure function -------------------------------


def test_a_grounded_answer_is_kept() -> None:
    text, cited, abstained, _, _spans = enforce_citations(
        {"answer": "A manufacturer is a person.", "citations": [SUPPORTED]}, delivered(SEGMENTS)
    )

    assert not abstained
    assert text == "A manufacturer is a person."
    assert [one.id for one in cited] == ["doc:article:3"]


def test_an_answer_citing_nothing_becomes_an_abstention() -> None:
    """The rule that makes citation mandatory rather than merely requested."""
    text, cited, abstained, reason, _spans = enforce_citations(
        {"answer": "A manufacturer is a person.", "citations": []}, delivered(SEGMENTS)
    )

    assert abstained and text == "" and cited == ()
    assert "not grounded" in reason


def test_an_invented_citation_is_dropped_and_reported() -> None:
    """The model cannot cite what it was not shown; such an id is fabricated."""
    _, cited, abstained, reason, _spans = enforce_citations(
        {
            "answer": "Yes.",
            "citations": [SUPPORTED, {"id": "doc:article:99", "span": SUPPORTED["span"]}],
        },
        delivered(SEGMENTS),
    )

    assert not abstained
    assert [one.id for one in cited] == ["doc:article:3"]
    assert "doc:article:99" in reason


def test_an_answer_whose_only_citation_is_invented_abstains() -> None:
    _, cited, abstained, reason, _spans = enforce_citations(
        {"answer": "Yes.", "citations": ["doc:article:99"]}, delivered(SEGMENTS)
    )

    assert abstained and cited == ()
    assert "doc:article:99" in reason


def test_an_explicit_abstention_is_respected() -> None:
    _, cited, abstained, reason, _spans = enforce_citations(
        {"answer": "", "abstained": True, "reason": "nothing on penalties", "citations": []},
        delivered(SEGMENTS),
    )

    assert abstained and cited == () and reason == "nothing on penalties"


def test_an_empty_answer_abstains() -> None:
    _, _, abstained, _, _spans = enforce_citations(
        {"answer": "  ", "citations": [SUPPORTED]}, delivered(SEGMENTS)
    )

    assert abstained


# --- the whole ask path -----------------------------------------------------


def test_ask_returns_an_answer_and_a_telemetry_record() -> None:
    client = FakeClient({"answer": "A manufacturer is a person.", "citations": [SUPPORTED]})

    answer, record = ask("who is a manufacturer", retriever(), client=client, k=2)

    assert isinstance(answer, Answer)
    assert not answer.abstained
    assert [one.id for one in answer.citations] == ["doc:article:3"]
    assert record.outcome == "answered"
    assert record.prompt_tokens == 1200 and record.completion_tokens == 80
    assert record.estimated_cost_usd is not None
    assert record.request_id == answer.request_id
    assert record.citations == 1 and record.retrieved == 2


def test_ask_abstains_and_never_calls_the_model_when_nothing_is_retrieved() -> None:
    """No context means no possible grounded answer, so no reason to pay for one."""
    client = FakeClient({"answer": "should not be reached", "citations": []})

    answer, record = ask("bicycles and tandems", retriever(), client=client, k=5)

    assert answer.abstained
    assert client.calls == [], "no model call without retrieved context"
    assert record.outcome == "abstained" and record.retrieved == 0


def test_a_malformed_reply_abstains_rather_than_crashing() -> None:
    answer, record = ask("manufacturer", retriever(), client=FakeClient("not json at all"), k=2)

    assert answer.abstained
    assert "usable JSON" in answer.reason
    assert record.outcome == "abstained"


def test_the_completion_cap_is_passed_to_the_provider() -> None:
    client = FakeClient({"answer": "x", "citations": [SUPPORTED]})

    ask("manufacturer", retriever(), client=client, k=2)

    assert client.calls[0]["max_tokens"] > 0


def test_the_call_budget_stops_a_runaway_loop() -> None:
    budget = CallBudget(limit=1)
    client = FakeClient({"answer": "x", "citations": [SUPPORTED]})

    ask("manufacturer", retriever(), client=client, k=2, budget=budget)

    with pytest.raises(CallBudgetExceededError):
        ask("manufacturer", retriever(), client=client, k=2, budget=budget)


def test_a_provider_failure_records_the_class_name_only() -> None:
    """Provider error messages have been known to echo request headers."""
    secret = "sk-livekey-should-never-appear"
    client = FakeClient({}, fail=RuntimeError(f"401 unauthorised for {secret}"))

    with pytest.raises(GenerationError) as caught:
        ask("manufacturer", retriever(), client=client, k=2)

    record = caught.value.record
    assert record.outcome == "error"
    assert record.error_type == "RuntimeError"
    assert secret not in record.model_dump_json()
    assert secret not in str(caught.value)


def test_an_untrusted_citation_is_identifiable_on_the_answer() -> None:
    segments = [
        segment(
            "blog:section:1",
            "Anyone can write this claim about the regulation.",
            TrustTier.UNTRUSTED,
        )
    ]
    client = FakeClient(
        {
            "answer": "Commentary says so.",
            "citations": [
                {
                    "id": "blog:section:1",
                    "span": "Anyone can write this claim about the regulation",
                }
            ],
        }
    )

    answer, _ = ask("anyone", Bm25Retriever(segments), client=client, k=2)

    assert [one.id for one in answer.cited_untrusted] == ["blog:section:1"]


# --- key handling -----------------------------------------------------------


def test_a_missing_key_names_the_variable_and_not_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)

    with pytest.raises(MissingApiKeyError) as caught:
        client_from_environment()

    assert API_KEY_VARIABLE in str(caught.value)


def test_the_client_never_reveals_the_key_in_its_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key in a repr reaches tracebacks, debuggers and CI logs."""
    secret = "sk-test-not-a-real-key"
    monkeypatch.setenv(API_KEY_VARIABLE, secret)

    client = client_from_environment()

    assert isinstance(client, OpenAiChatClient)
    assert secret not in repr(client)
    assert "redacted" in repr(client)


# --- live smoke test, deselected by default ---------------------------------


@pytest.mark.live
def test_live_smoke() -> None:
    """The one test that costs money. Run with:

    CRA_LIVE_TESTS=1 uv run pytest -m live
    """
    if os.environ.get("CRA_LIVE_TESTS") != "1":
        pytest.skip("set CRA_LIVE_TESTS=1 to run live tests")

    answer, record = ask(
        "Who is a manufacturer under this regulation?",
        retriever(),
        client=client_from_environment(),
        k=2,
    )

    assert record.outcome in {"answered", "abstained"}
    assert record.latency_ms >= 0
    if not answer.abstained:
        assert answer.citations, "an answer must cite something"


# --- provider error codes ---------------------------------------------------


class ProviderError(Exception):
    """Shaped like an OpenAI SDK error: a structured body plus a message."""

    def __init__(self, message: str, body: dict | None = None) -> None:
        super().__init__(message)
        self.body = body


def test_a_structured_error_code_is_recorded_and_explained() -> None:
    """`RateLimitError` alone sent us looking for a throttle when the real state
    was an exhausted credit balance."""
    client = FakeClient(
        {},
        fail=ProviderError(
            "429 rate limit for key sk-secret",
            {"code": "credit_balance_exhausted", "type": "insufficient_quota"},
        ),
    )

    with pytest.raises(GenerationError) as caught:
        ask("manufacturer", retriever(), client=client, k=2)

    assert caught.value.record.error_code == "credit_balance_exhausted"
    assert "credit balance is exhausted" in str(caught.value)
    assert "sk-secret" not in str(caught.value), "the provider message is never read"
    assert "sk-secret" not in caught.value.record.model_dump_json()


def test_an_error_without_a_structured_code_still_reports_its_class() -> None:
    with pytest.raises(GenerationError, match="RuntimeError"):
        ask("manufacturer", retriever(), client=FakeClient({}, fail=RuntimeError("boom")), k=2)


def test_an_unrecognised_code_is_reported_without_inventing_a_hint() -> None:
    client = FakeClient({}, fail=ProviderError("x", {"code": "some_new_code"}))

    with pytest.raises(GenerationError) as caught:
        ask("manufacturer", retriever(), client=client, k=2)

    assert "some_new_code" in str(caught.value)
    assert caught.value.record.error_code == "some_new_code"


@pytest.mark.parametrize("body", [None, {}, {"code": None}, "not a dict", {"code": 7}])
def test_a_malformed_error_body_yields_no_code(body: object) -> None:
    assert provider_error_code(ProviderError("x", body)) is None  # type: ignore[arg-type]


# --- claim-support enforcement (ADR-0015) ------------------------------------


def test_a_span_that_is_not_in_the_segment_is_dropped() -> None:
    """The gap three of five successful attacks walked through: a real citation
    beside a claim the segment does not make."""
    _, cited, abstained, reason, _spans = enforce_citations(
        {
            "answer": "Manufacturers are exempt below fifty employees.",
            "citations": [{"id": "doc:article:3", "span": "exempt below fifty employees"}],
        },
        delivered(SEGMENTS),
    )

    assert abstained and cited == ()
    assert "quoted span is not in them" in reason
    assert "doc:article:3" in reason


def test_a_citation_with_no_span_is_dropped() -> None:
    _, _, abstained, reason, _spans = enforce_citations(
        {"answer": "Yes.", "citations": [{"id": "doc:article:3"}]}, delivered(SEGMENTS)
    )

    assert abstained
    assert "no usable supporting span" in reason


def test_a_bare_id_from_a_model_ignoring_the_contract_is_dropped() -> None:
    _, _, abstained, reason, _spans = enforce_citations(
        {"answer": "Yes.", "citations": ["doc:article:3"]}, delivered(SEGMENTS)
    )

    assert abstained
    assert "no usable supporting span" in reason


def test_a_span_shorter_than_the_floor_is_dropped() -> None:
    """A three-word quotation appears in almost any document, so accepting one
    would make the check pass on coincidence."""
    _, _, abstained, _, _spans = enforce_citations(
        {"answer": "Yes.", "citations": [{"id": "doc:article:3", "span": "manufacturer"}]},
        delivered(SEGMENTS),
    )

    assert abstained


def test_whitespace_differences_do_not_break_a_real_quotation() -> None:
    """A reflowed quotation is still a quotation."""
    from cra_assistant.generate import span_supports

    assert span_supports("manufacturer   means a\n  natural or legal person", deliver(SEGMENTS[0]))


def test_matching_is_whitespace_only_so_a_paraphrase_still_fails() -> None:
    """Lowercasing or stripping punctuation would let a reconstruction through,
    and the point is that the model copied rather than rewrote."""
    from cra_assistant.generate import span_supports

    assert not span_supports("a manufacturer is any natural or legal person", deliver(SEGMENTS[0]))


def test_the_two_drop_reasons_are_reported_separately() -> None:
    """An unretrieved id is a fabricated source; an unsupported span is a
    fabricated claim about a real source. They mean different things."""
    from cra_assistant.generate import check_citations

    check = check_citations(
        {
            "citations": [
                {"id": "doc:article:99", "span": SUPPORTED["span"]},
                {"id": "doc:article:13", "span": "a span that is nowhere in this segment"},
                SUPPORTED,
            ]
        },
        delivered(SEGMENTS),
    )

    assert check.not_retrieved == ("doc:article:99",)
    assert check.unsupported == ("doc:article:13",)
    assert [one.id for one in check.kept] == ["doc:article:3"]


def test_a_correctly_quoted_but_irrelevant_span_still_passes() -> None:
    """The hole this check does not close, asserted so it is not mistaken for a
    guarantee. ADR-0015 predicts auth-notice survives on exactly this.
    """
    text, cited, abstained, _, _spans = enforce_citations(
        {
            "answer": "The Regulation applies from 11 December 2029.",
            "citations": [{"id": "doc:article:3", "span": "manufacturer means a natural"}],
        },
        delivered(SEGMENTS),
    )

    assert not abstained, "a real span passes even when it does not bear on the claim"
    assert [one.id for one in cited] == ["doc:article:3"]
    assert "2029" in text


# --- tier-aware support: deleted (ADR-0016) -----------------------------------


def test_the_deleted_tier_rule_no_longer_refuses_untrusted_statutory_claims() -> None:
    """Deleted on 2026-09-14 after its final measurement: breaches did not move.
    This pins the deletion, so the rule cannot drift back in unmeasured."""
    untrusted = [
        segment(
            "blog:section:1",
            "Manufacturers whose annual turnover does not exceed EUR 2 000 000 are exempt.",
            TrustTier.UNTRUSTED,
        )
    ]

    _, cited, abstained, _, _spans = enforce_citations(
        {
            "answer": "Manufacturers whose turnover does not exceed EUR 2 000 000 are exempt.",
            "citations": [
                {
                    "id": "blog:section:1",
                    "span": "annual turnover does not exceed EUR 2 000 000 are exempt",
                }
            ],
        },
        delivered(untrusted),
    )

    assert not abstained and [one.id for one in cited] == ["blog:section:1"]
    import cra_assistant.generate as generate
    import cra_assistant.prompt as prompt

    assert not hasattr(generate, "unattributed_statutory_claims")
    assert "This is enforced." not in prompt.SYSTEM_PROMPT


# --- validation against delivered text, not stored text -----------------------


FILLER = "The manufacturer shall keep records. " * 120
BEYOND = "Manufacturers below fifty employees are exempt from Article 14."
LONG = segment("doc:annex:8", FILLER + BEYOND, TrustTier.TRUSTED)


def test_a_span_past_the_cutoff_fails_enforcement() -> None:
    """The text is in the stored segment but was clipped out of the prompt. A span
    quoting it came from memory or from an attacker's quotation of the full
    document, not from anything the model was shown."""
    assert BEYOND in LONG.text and len(FILLER) > MAX_SEGMENT_CHARS
    shown = deliver(LONG)
    assert BEYOND not in shown.text

    _, cited, abstained, reason, _spans = enforce_citations(
        {
            "answer": "Small manufacturers are exempt.",
            "citations": [{"id": "doc:annex:8", "span": BEYOND}],
        },
        [shown],
    )

    assert abstained and cited == ()
    assert "was not shown" in reason and "doc:annex:8" in reason


def test_a_span_before_the_cutoff_of_a_clipped_segment_still_passes() -> None:
    _, cited, abstained, _, _spans = enforce_citations(
        {
            "answer": "Manufacturers keep records.",
            "citations": [{"id": "doc:annex:8", "span": "The manufacturer shall keep records."}],
        },
        [deliver(LONG)],
    )

    assert not abstained and [one.id for one in cited] == ["doc:annex:8"]


def test_ask_rejects_an_undelivered_span_and_records_the_truncation() -> None:
    client = FakeClient(
        {
            "answer": "Small manufacturers are exempt.",
            "citations": [{"id": "doc:annex:8", "span": BEYOND}],
        }
    )

    answer, record = ask("manufacturer records", Bm25Retriever([LONG]), client=client, k=1)

    assert answer.abstained
    assert BEYOND not in client.calls[0]["messages"][1]["content"], "the model was not shown it"
    assert record.segments_truncated == 1
    assert record.characters_dropped == len(LONG.text) - len(deliver(LONG).text)
    assert record.characters_dropped >= len(BEYOND)


def test_an_untruncated_call_records_nothing_dropped() -> None:
    client = FakeClient({"answer": "A manufacturer is a person.", "citations": [SUPPORTED]})

    _, record = ask("who is a manufacturer", retriever(), client=client, k=2)

    assert record.segments_truncated == 0 and record.characters_dropped == 0


# --- what a failed call and a kept citation record --------------------------


def test_a_failed_call_records_the_time_it_actually_took() -> None:
    """The error record used to be built inside the timer's context, where the
    elapsed time is still zero, so slow failures looked instantaneous."""
    import time

    class Slow:
        def complete(self, *, model: str, messages: object, max_tokens: int) -> object:
            time.sleep(0.05)
            raise RuntimeError("boom")

    with pytest.raises(GenerationError) as caught:
        ask("manufacturer", retriever(), client=Slow(), k=2)

    assert caught.value.record.latency_ms >= 50, caught.value.record.latency_ms


def test_the_validated_span_is_kept_with_the_answer() -> None:
    """Dropping it forced a reader back into the corpus to see why a citation
    supports the claim."""
    client = FakeClient({"answer": "A manufacturer is a person.", "citations": [SUPPORTED]})

    answer, _ = ask("who is a manufacturer", retriever(), client=client, k=2)

    assert [one.id for one in answer.citations] == ["doc:article:3"]
    assert answer.spans == ("manufacturer means a natural or legal person",)


def test_an_abstention_carries_no_spans() -> None:
    _, _, abstained, _, spans = enforce_citations(
        {"answer": "Yes.", "citations": ["doc:article:99"]}, delivered(SEGMENTS)
    )

    assert abstained and spans == ()
