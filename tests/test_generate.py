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
    text, cited, abstained, _ = enforce_citations(
        {"answer": "A manufacturer is a person.", "citations": ["doc:article:3"]}, SEGMENTS
    )

    assert not abstained
    assert text == "A manufacturer is a person."
    assert [one.id for one in cited] == ["doc:article:3"]


def test_an_answer_citing_nothing_becomes_an_abstention() -> None:
    """The rule that makes citation mandatory rather than merely requested."""
    text, cited, abstained, reason = enforce_citations(
        {"answer": "A manufacturer is a person.", "citations": []}, SEGMENTS
    )

    assert abstained and text == "" and cited == ()
    assert "not grounded" in reason


def test_an_invented_citation_is_dropped_and_reported() -> None:
    """The model cannot cite what it was not shown; such an id is fabricated."""
    _, cited, abstained, reason = enforce_citations(
        {"answer": "Yes.", "citations": ["doc:article:3", "doc:article:99"]}, SEGMENTS
    )

    assert not abstained
    assert [one.id for one in cited] == ["doc:article:3"]
    assert "doc:article:99" in reason


def test_an_answer_whose_only_citation_is_invented_abstains() -> None:
    _, cited, abstained, reason = enforce_citations(
        {"answer": "Yes.", "citations": ["doc:article:99"]}, SEGMENTS
    )

    assert abstained and cited == ()
    assert "doc:article:99" in reason


def test_an_explicit_abstention_is_respected() -> None:
    _, cited, abstained, reason = enforce_citations(
        {"answer": "", "abstained": True, "reason": "nothing on penalties", "citations": []},
        SEGMENTS,
    )

    assert abstained and cited == () and reason == "nothing on penalties"


def test_an_empty_answer_abstains() -> None:
    _, _, abstained, _ = enforce_citations(
        {"answer": "  ", "citations": ["doc:article:3"]}, SEGMENTS
    )

    assert abstained


# --- the whole ask path -----------------------------------------------------


def test_ask_returns_an_answer_and_a_telemetry_record() -> None:
    client = FakeClient({"answer": "A manufacturer is a person.", "citations": ["doc:article:3"]})

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
    client = FakeClient({"answer": "x", "citations": ["doc:article:3"]})

    ask("manufacturer", retriever(), client=client, k=2)

    assert client.calls[0]["max_tokens"] > 0


def test_the_call_budget_stops_a_runaway_loop() -> None:
    budget = CallBudget(limit=1)
    client = FakeClient({"answer": "x", "citations": ["doc:article:3"]})

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
    segments = [segment("blog:section:1", "Anyone can write this.", TrustTier.UNTRUSTED)]
    client = FakeClient({"answer": "Commentary says so.", "citations": ["blog:section:1"]})

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
