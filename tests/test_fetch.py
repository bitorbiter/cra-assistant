"""Fetching, with no network: every request is answered by a MockTransport."""

import json
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
from factories import make_observation, make_source

NO_DELAY = FetchPolicy(backoff_seconds=0.0, delay_between_sources=0.0)

# Documents handed to fetch_sources must survive the ingest plausibility check
# (ADR-0009), so test payloads have to look like real documents.
PARAGRAPH = "A genuine paragraph about steward obligations under the regulation. " * 4
PLAUSIBLE_HTML = (
    "<html><body>"
    + "".join(f"<h2>Heading {n}</h2><p>{PARAGRAPH}</p>" for n in range(1, 5))
    + "</body></html>"
).encode()
PLAUSIBLE_MARKDOWN = ("".join(f"# Heading {n}\n\n{PARAGRAPH}\n\n" for n in range(1, 5))).encode()


def client_returning(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def always(status: int, body: bytes = PLAUSIBLE_HTML, **headers: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers=headers)

    return client_returning(handler)


def test_a_successful_fetch_is_recorded_in_the_manifest(tmp_path: Path) -> None:
    source = make_source()
    with always(200, PLAUSIBLE_HTML, **{"content-type": "text/html; charset=utf-8"}) as client:
        observations, errors = fetch_sources(
            [source], data_root=tmp_path, client=client, policy=NO_DELAY, sleep=lambda _: None
        )

    assert not errors
    (observation,) = observations
    assert observation.source_id == "example-source"
    assert observation.byte_count == len(PLAUSIBLE_HTML)
    assert observation.http_status == 200
    assert observation.content_type == "text/html; charset=utf-8"
    assert observation.retrieved_at.utcoffset() is not None, "timestamps must be timezone-aware"

    assert load_manifest(tmp_path / MANIFEST_FILENAME) == tuple(observations), (
        "an observation must survive the JSONL round trip unchanged"
    )
    assert (tmp_path / observation.stored_path).read_bytes() == PLAUSIBLE_HTML


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
    with always(200, PLAUSIBLE_MARKDOWN, **{"content-type": "text/plain"}) as client:
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
        return httpx.Response(200, content=PLAUSIBLE_HTML)

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
        return httpx.Response(200, content=PLAUSIBLE_HTML)

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


def test_a_client_rendered_page_never_enters_the_corpus(tmp_path: Path) -> None:
    """The regression. This document used to be fetched, stored, checksummed,
    pinned and reported clean while containing a navigation menu (ADR-0009).
    """
    chrome = (Path(__file__).parent / "fixtures" / "client_rendered_page.html").read_bytes()

    with always(200, chrome) as client:
        observations, errors = fetch_sources(
            [make_source()],
            data_root=tmp_path,
            client=client,
            policy=NO_DELAY,
            sleep=lambda _: None,
        )

    assert not observations
    (error,) = errors
    assert "implausible document" in error.reason
    assert "client-rendered" in error.reason or "error while loading" in error.reason
    assert not (tmp_path / MANIFEST_FILENAME).exists(), "nothing may be recorded"
    assert not (tmp_path / "raw").exists(), "nothing may be stored"


def test_every_declared_parser_has_a_fetcher() -> None:
    from cra_assistant.fetch import fetcher_for
    from cra_assistant.models import Parser

    assert all(fetcher_for(make_source(parser=parser)) is not None for parser in Parser)


def test_transport_is_chosen_by_scheme_not_by_parser() -> None:
    """A Markdown fixture on disk and a Markdown file on a CDN are the same
    parser and different transports."""
    from cra_assistant.fetch import fetch_local_file, fetcher_for
    from cra_assistant.models import Parser

    on_disk = make_source("local-md", url="file:README.md", parser=Parser.MARKDOWN)
    remote = make_source("remote-md", url="https://example.org/x.md", parser=Parser.MARKDOWN)

    assert fetcher_for(on_disk) is fetch_local_file
    assert fetcher_for(remote) is not fetch_local_file


def test_the_manifest_records_item_and_segment_counts(tmp_path: Path) -> None:
    """Truncation should be visible as a number in the manifest, not only as an
    exception on the day it happens."""
    from cra_assistant.attack import DEFAULT_ATTACK_REGISTRY
    from cra_assistant.manifest import load_manifest
    from cra_assistant.registry import load_registry

    (source,) = [
        one
        for one in load_registry(DEFAULT_ATTACK_REGISTRY).sources
        if one.id == "fixture-metadata-heading"
    ]
    with httpx.Client() as client:
        observations, errors = fetch_sources(
            [source], data_root=tmp_path, client=client, sleep=lambda _: None
        )

    assert errors == []
    (recorded,) = load_manifest(tmp_path / MANIFEST_FILENAME)
    assert recorded == observations[0]
    assert recorded.item_counts == {"files": 2}
    assert recorded.segment_count == 3
    assert recorded.content_checksum is not None and recorded.content_checksum.startswith("sha256:")


def test_the_corpus_hash_refuses_to_cover_part_of_the_corpus() -> None:
    from cra_assistant.manifest import corpus_content_checksum

    complete = make_observation("a-source", "sha256:" + "a" * 64).model_copy(
        update={"content_checksum": "sha256:" + "c" * 64}
    )
    legacy = make_observation("b-source", "sha256:" + "b" * 64)

    assert corpus_content_checksum([complete]) is not None
    assert corpus_content_checksum([complete, legacy]) is None


def test_a_manifest_line_written_before_counts_existed_still_loads(tmp_path: Path) -> None:
    old = make_observation("example-source", "sha256:" + "a" * 64).model_dump(
        mode="json", exclude={"segment_count", "item_counts"}
    )
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(old) + "\n")

    (loaded,) = load_manifest(tmp_path / MANIFEST_FILENAME)
    assert loaded.segment_count is None and loaded.item_counts == {}
