"""The call log. Its main job is to contain no secrets."""

from pathlib import Path

from cra_assistant.telemetry import (
    CallRecord,
    estimate_cost,
    load_calls,
    log_call,
    new_request_id,
    timed,
    utc_now,
)


def record(**overrides: object) -> CallRecord:
    fields: dict[str, object] = {
        "request_id": new_request_id(),
        "started_at": utc_now(),
        "operation": "ask",
        "model": "gpt-4o-mini",
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "latency_ms": 850,
        "outcome": "answered",
    }
    return CallRecord.model_validate(fields | overrides)


def test_a_record_survives_the_jsonl_round_trip(tmp_path: Path) -> None:
    first = record()
    second = record(outcome="abstained")

    log_call(tmp_path, first)
    log_call(tmp_path, second)

    assert load_calls(tmp_path) == [first, second]


def test_the_log_is_append_only(tmp_path: Path) -> None:
    log_call(tmp_path, record())
    log_call(tmp_path, record())

    assert len(load_calls(tmp_path)) == 2


def test_a_missing_log_is_empty_not_an_error(tmp_path: Path) -> None:
    assert load_calls(tmp_path) == []


def test_the_record_has_nowhere_to_put_a_secret() -> None:
    """A fixed shape with no free-form field is the reason a key cannot leak
    here by accident."""
    assert CallRecord.model_config["extra"] == "forbid"

    fields = set(CallRecord.model_fields)
    assert not fields & {"api_key", "headers", "prompt", "messages", "response"}


def test_cost_is_estimated_for_a_known_model() -> None:
    cost = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)

    assert cost == 0.75


def test_an_unknown_model_records_no_cost_rather_than_a_guess() -> None:
    assert estimate_cost("some-future-model", 1000, 1000) is None


def test_timing_is_recorded_even_when_the_block_raises() -> None:
    elapsed = {}
    try:
        with timed() as elapsed:
            raise RuntimeError("provider exploded")
    except RuntimeError:
        pass

    assert elapsed["latency_ms"] >= 0
