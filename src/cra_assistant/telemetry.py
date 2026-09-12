"""A JSONL log of every model call.

OpenTelemetry is a later step. This exists now anyway, because the expensive
part of telemetry is not the exporter — it is threading a request id and a token
count through code that was written without them. The seam costs a few lines
today and is unpleasant to retrofit later.

**No key material is ever recorded here.** The record has a fixed shape with no
free-form dict, so a credential cannot arrive by accident, and there is a test
asserting an API key cannot reach the log.
"""

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

CALL_LOG_FILENAME = "calls.jsonl"

USD_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
}
"""(prompt, completion) price per million tokens.

These go stale, and a stale price is worse than none if it is believed. The
figure is recorded as ``estimated_cost_usd`` and is an estimate for spotting
runaway usage, not an invoice. An unknown model records ``None`` rather than
guessing.
"""


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    prices = USD_PER_MILLION_TOKENS.get(model)
    if prices is None:
        return None
    prompt_price, completion_price = prices
    return round(
        (prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000, 6
    )


class CallRecord(BaseModel):
    """One model call. Fixed shape: there is nowhere to put a secret."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    started_at: datetime
    operation: str = Field(description="What the call was for, e.g. 'ask'.")
    model: str
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost_usd: float | None = None
    outcome: str = Field(description="'answered', 'abstained', or 'error'.")
    error_type: str | None = Field(
        default=None,
        description="Exception class name only. Never a message: provider errors "
        "have been known to echo request headers.",
    )
    retrieved: int = Field(default=0, ge=0)
    citations: int = Field(default=0, ge=0)


def new_request_id() -> str:
    return uuid.uuid4().hex


def log_call(data_root: Path, record: CallRecord) -> None:
    path = data_root / CALL_LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")


def load_calls(data_root: Path) -> list[CallRecord]:
    path = data_root / CALL_LOG_FILENAME
    if not path.exists():
        return []
    return [
        CallRecord.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@contextmanager
def timed() -> Iterator[dict[str, int]]:
    """Wall-clock milliseconds for the enclosed block, recorded even on failure."""
    elapsed = {"latency_ms": 0}
    start = time.perf_counter()
    try:
        yield elapsed
    finally:
        elapsed["latency_ms"] = int((time.perf_counter() - start) * 1000)


def utc_now() -> datetime:
    return datetime.now(UTC)
