"""Fetching, with no network: every request is answered by a MockTransport."""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from cra_assistant.fetch import (
    MANIFEST_FILENAME,
    FetchError,
    FetchPolicy,
    fetch_one,
    fetch_sources,
    store_bytes,
)
from cra_assistant.manifest import load_manifest
from cra_assistant.models import Parser
from factories import make_source

NO_DELAY = FetchPolicy(backoff_seconds=0.0, delay_between_sources=0.0)


def client_returning(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def always(status: int, body: bytes = b"<html>body</html>", **headers: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers=headers)

    return client_returning(handler)


def test_a_successful_fetch_is_recorded_in_the_manifest(tmp_path: Path) -> None:
    source = make_source()
    with always(200, b"hello", **{"content-type": "text/html; charset=utf-8"}) as client:
        observations, errors = fetch_sources(
            [source], data_root=tmp_path, client=client, policy=NO_DELAY, sleep=lambda _: None
        )

    assert not errors
    (observation,) = observations
    assert observation.source_id == "example-source"
    assert observation.byte_count == 5
    assert observation.http_status == 200
    assert observation.content_type == "text/html; charset=utf-8"
    assert observation.retrieved_at.utcoffset() is not None, "timestamps must be timezone-aware"

    assert load_manifest(tmp_path / MANIFEST_FILENAME) == tuple(observations), (
        "an observation must survive the JSONL round trip unchanged"
    )
    assert (tmp_path / observation.stored_path).read_bytes() == b"hello"


def test_bytes_are_stored_content_addressed_and_never_overwritten(tmp_path: Path) -> None:
    source = make_source()

    first_checksum, first_path = store_bytes(tmp_path, source, b"original")
    second_checksum, second_path = store_bytes(tmp_path, source, b"changed")

    assert first_checksum != second_checksum
    assert first_path != second_path, "different bytes must not collide"
    assert (tmp_path / first_path).read_bytes() == b"original", "the old version survives"
    assert (tmp_path / second_path).read_bytes() == b"changed"

    repeat_checksum, repeat_path = store_bytes(tmp_path, source, b"original")
    assert (repeat_checksum, repeat_path) == (first_checksum, first_path)
    assert len(list((tmp_path / "raw" / source.id).iterdir())) == 2


def test_extension_comes_from_the_declared_parser_not_the_served_type(tmp_path: Path) -> None:
    """raw.githubusercontent.com serves Markdown as text/plain."""
    markdown = make_source("md-source", parser=Parser.MARKDOWN)
    with always(200, b"# heading", **{"content-type": "text/plain"}) as client:
        observations, _ = fetch_sources(
            [markdown], data_root=tmp_path, client=client, policy=NO_DELAY, sleep=lambda _: None
        )

    assert observations[0].stored_path.endswith(".md")
    assert observations[0].content_type == "text/plain"


def test_http_error_is_surfaced_not_swallowed() -> None:
    with always(404) as client, pytest.raises(FetchError, match="HTTP 404"):
        fetch_one(make_source(), policy=NO_DELAY, sleep=lambda _: None, client=client)


def test_a_failed_source_is_reported_and_stores_nothing(tmp_path: Path) -> None:
    with always(404) as client:
        observations, errors = fetch_sources(
            [make_source()],
            data_root=tmp_path,
            client=client,
            policy=NO_DELAY,
            sleep=lambda _: None,
        )

    assert not observations
    (error,) = errors
    assert error.source_id == "example-source"
    assert not (tmp_path / MANIFEST_FILENAME).exists(), "a failure must not enter the manifest"
    assert not (tmp_path / "raw").exists()


def test_a_failure_does_not_stop_the_other_sources(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/missing":
            return httpx.Response(404)
        return httpx.Response(200, content=b"fine")

    good = make_source("good-source")
    bad = make_source("bad-source", url="https://example.org/missing")
    with client_returning(handler) as client:
        observations, errors = fetch_sources(
            [bad, good], data_root=tmp_path, client=client, policy=NO_DELAY, sleep=lambda _: None
        )

    assert [observation.source_id for observation in observations] == ["good-source"]
    assert [error.source_id for error in errors] == ["bad-source"]


def test_server_errors_are_retried_then_give_up() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    waits: list[float] = []
    with client_returning(handler) as client, pytest.raises(FetchError, match="after 3 attempts"):
        fetch_one(
            make_source(),
            client=client,
            policy=FetchPolicy(max_attempts=3, backoff_seconds=1.0),
            sleep=waits.append,
        )

    assert attempts == 3
    assert waits == [1.0, 2.0], "backoff doubles, and nothing is slept after the last attempt"


def test_a_retried_request_can_succeed() -> None:
    responses = iter([httpx.Response(503), httpx.Response(200, content=b"ok")])

    with client_returning(lambda request: next(responses)) as client:
        response = fetch_one(make_source(), client=client, policy=NO_DELAY, sleep=lambda _: None)

    assert response.content == b"ok"


def test_client_errors_are_not_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(403)

    with client_returning(handler) as client, pytest.raises(FetchError, match="HTTP 403"):
        fetch_one(make_source(), client=client, policy=NO_DELAY, sleep=lambda _: None)

    assert attempts == 1, "repeating a rejected request is rude, not useful"


def test_transport_errors_are_retried_and_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with client_returning(handler) as client, pytest.raises(FetchError, match="ConnectError"):
        fetch_one(make_source(), client=client, policy=NO_DELAY, sleep=lambda _: None)


def test_the_user_agent_identifies_the_project() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, content=b"ok")

    with client_returning(handler) as client:
        fetch_one(make_source(), client=client, policy=NO_DELAY, sleep=lambda _: None)

    assert seen == [NO_DELAY.user_agent]
    assert "cra-assistant" in seen[0] and "github.com/bitorbiter" in seen[0]


def test_sources_are_fetched_once_each_with_a_delay_between_them(tmp_path: Path) -> None:
    requested: list[str] = []
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=str(request.url).encode())

    sources = [
        make_source(f"source-{index}", url=f"https://example.org/{index}") for index in range(3)
    ]
    with client_returning(handler) as client:
        fetch_sources(
            sources,
            data_root=tmp_path,
            client=client,
            policy=FetchPolicy(delay_between_sources=0.5),
            sleep=waits.append,
        )

    assert len(requested) == len(set(requested)) == 3, "one request per source"
    assert waits == [0.5, 0.5], "a delay between sources, not before the first or after the last"
