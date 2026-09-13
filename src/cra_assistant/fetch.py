"""Downloading declared sources and recording what came back.

Fetching is deliberately dumb: it asks once per source, stores the bytes under
their own digest, and appends an observation. It does not compare anything
against anything. In particular **a changed checksum is never an error here** —
noticing that is `cra_assistant.verify`'s job, and conflating the two would mean
a routine upstream edit could stop the corpus being fetched at all.
"""

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

import httpx

from cra_assistant import __version__
from cra_assistant.github import (
    GithubError,
    fetch_issues_document,
    fetch_markdown_tree_document,
)
from cra_assistant.manifest import FetchObservation, append_observation
from cra_assistant.models import Parser, Source
from cra_assistant.paths import REPO_ROOT
from cra_assistant.plausibility import check_document
from cra_assistant.problems import has_errors
from cra_assistant.segment import document_content_checksum, segment_document

USER_AGENT = f"cra-assistant/{__version__} (+https://github.com/bitorbiter/cra-assistant)"
"""Identifiable on sight, with somewhere to complain to. We are a guest on
EUR-Lex and on GitHub."""

MANIFEST_FILENAME = "manifest.jsonl"

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
"""Transient by nature. Every other 4xx says the request itself is wrong, and
repeating it is rude rather than useful."""

EXTENSION_BY_PARSER: dict[Parser, str] = {
    Parser.EURLEX_HTML: "html",
    Parser.GENERIC_HTML: "html",
    Parser.MARKDOWN: "md",
    Parser.GITHUB_ISSUES: "json",
    Parser.GITHUB_MARKDOWN_TREE: "json",
}
"""File extension per declared parser.

Taken from the parser rather than the served ``Content-Type`` on purpose:
raw.githubusercontent.com serves Markdown as ``text/plain``, so the header would
name the file worse than the registry does.
"""

if set(EXTENSION_BY_PARSER) != set(Parser):
    missing = sorted(str(parser) for parser in set(Parser) - set(EXTENSION_BY_PARSER))
    raise RuntimeError(f"EXTENSION_BY_PARSER is missing parsers: {', '.join(missing)}")


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """How hard to try, and how politely."""

    timeout: float = 30.0
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    """Attempt *n* waits ``backoff_seconds * 2 ** (n - 1)``."""
    delay_between_sources: float = 1.0
    user_agent: str = USER_AGENT


DEFAULT_POLICY = FetchPolicy()
"""Shared default. Safe to share because FetchPolicy is frozen."""


Fetcher = Callable[..., "FetchedDocument"]


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """What a fetch produced, however many requests it took.

    A source assembled from an API is still one document with one checksum, so
    the store, the manifest and drift detection need no special case.
    """

    content: bytes
    resolved_url: str
    http_status: int
    content_type: str | None


class FetchError(RuntimeError):
    """A source could not be retrieved.

    Raised rather than recorded. An empty or truncated document that silently
    entered the corpus would be worse than a missing one, because a retrieval
    system cannot tell the difference between "nothing was said about this" and
    "the page failed to load".
    """

    def __init__(self, source_id: str, reason: str) -> None:
        super().__init__(f"{source_id}: {reason}")
        self.source_id = source_id
        self.reason = reason


def fetch_one(
    source: Source,
    *,
    client: httpx.Client,
    policy: FetchPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """Request one source, retrying only what is worth retrying."""
    reason = "no attempt made"
    for attempt in range(1, policy.max_attempts + 1):
        try:
            response = client.get(
                str(source.url),
                headers={"User-Agent": policy.user_agent},
                timeout=policy.timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as error:
            reason = f"{type(error).__name__}: {error}"
        else:
            if not response.is_error:
                return response
            reason = f"HTTP {response.status_code}"
            if response.status_code not in RETRYABLE_STATUS:
                raise FetchError(source.id, reason)

        if attempt < policy.max_attempts:
            sleep(policy.backoff_seconds * 2 ** (attempt - 1))

    raise FetchError(source.id, f"{reason} (after {policy.max_attempts} attempts)")


def fetch_plain(
    source: Source,
    *,
    client: httpx.Client,
    policy: FetchPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchedDocument:
    """One document, one request. The original behaviour."""
    response = fetch_one(source, client=client, policy=policy, sleep=sleep)
    return FetchedDocument(
        content=response.content,
        resolved_url=str(response.url),
        http_status=response.status_code,
        content_type=response.headers.get("content-type"),
    )


def _github_fetcher(builder: Callable[..., bytes]) -> Fetcher:
    def fetch(
        source: Source,
        *,
        client: httpx.Client,
        policy: FetchPolicy = DEFAULT_POLICY,
        sleep: Callable[[float], None] = time.sleep,
    ) -> FetchedDocument:
        try:
            content = builder(
                str(source.url),
                client,
                user_agent=policy.user_agent,
                timeout=policy.timeout,
                sleep=sleep,
            )
        except GithubError as error:
            raise FetchError(source.id, str(error)) from error
        except httpx.HTTPError as error:
            raise FetchError(source.id, f"{type(error).__name__}: {error}") from error
        return FetchedDocument(
            content=content,
            resolved_url=str(source.url),
            http_status=200,
            content_type="application/json",
        )

    return fetch


FETCHERS: dict[Parser, Fetcher] = {}
"""Populated below. Every parser needs a fetcher; a test asserts it."""


def fetch_local_file(
    source: Source,
    *,
    client: httpx.Client,
    policy: FetchPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchedDocument:
    """Read a committed file declared with a ``file:`` URL.

    The seam promised by ADR-0009 and not built until it was needed: a fixture
    in this repository becomes an ordinary untrusted source, going through the
    same plausibility check, store, manifest, segmentation and retrieval as
    anything fetched over the network. Nothing about it is test-only, which is
    the point — an attack that arrives by a special path proves nothing about
    the path real content takes.

    Paths resolve against the repository root and must stay inside it. A
    registry is committed data, but it is still input, and ``file:../../..`` is
    the obvious thing to try.
    """
    relative = unquote(source.url.path or "").lstrip("/")
    if not relative:
        raise FetchError(source.id, f"no path in {source.url}")

    target = (REPO_ROOT / relative).resolve()
    if not target.is_relative_to(REPO_ROOT.resolve()):
        raise FetchError(source.id, f"{relative!r} resolves outside the repository")
    if not target.is_file():
        raise FetchError(source.id, f"no such file: {relative}")

    return FetchedDocument(
        content=target.read_bytes(),
        resolved_url=f"file:{relative}",
        http_status=200,
        content_type=None,
    )


def fetcher_for(source: Source) -> Fetcher:
    """Transport is chosen by URL scheme, format by parser.

    A Markdown fixture on disk and a Markdown file on raw.githubusercontent are
    the same parser and different transports, so the two must be dispatched
    separately.
    """
    if source.url.scheme == "file":
        return fetch_local_file
    return FETCHERS.get(source.parser, fetch_plain)


def store_bytes(data_root: Path, source: Source, content: bytes) -> tuple[str, str]:
    """Write ``content`` under its own digest and return ``(checksum, relative path)``.

    Content-addressed, so two fetches of unchanged bytes land on the same file
    and a changed upstream document lands beside its predecessor instead of
    replacing it. Nothing here ever overwrites: if the path exists, the bytes at
    it are identical by construction.
    """
    digest = hashlib.sha256(content).hexdigest()
    relative = Path("raw") / source.id / f"{digest[:12]}.{EXTENSION_BY_PARSER[source.parser]}"
    target = data_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(content)
    return f"sha256:{digest}", relative.as_posix()


def item_counts(source: Source, content: bytes) -> dict[str, int]:
    """Collection sizes in a document this project assembled from an API.

    Only the JSON parsers have collections. Anything else records nothing rather
    than a number that would mean something different per format.
    """
    if EXTENSION_BY_PARSER[source.parser] != "json":
        return {}
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {key: len(value) for key, value in sorted(payload.items()) if isinstance(value, list)}


def fetch_sources(
    sources: Iterable[Source],
    *,
    data_root: Path,
    client: httpx.Client,
    policy: FetchPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Sequence[FetchObservation], Sequence[FetchError]]:
    """Fetch each source once, storing bytes and appending observations.

    A failing source does not stop the others: its error is collected and
    returned so the caller can report every failure at once rather than one per
    run. Returning the errors instead of raising is not swallowing them — the
    CLI prints them and exits non-zero.
    """
    manifest_path = data_root / MANIFEST_FILENAME
    observations: list[FetchObservation] = []
    errors: list[FetchError] = []

    remaining = list(sources)
    for position, source in enumerate(remaining):
        if position > 0:
            sleep(policy.delay_between_sources)
        try:
            document = fetcher_for(source)(source, client=client, policy=policy, sleep=sleep)
        except FetchError as error:
            errors.append(error)
            continue

        content = document.content
        # Plausibility runs before anything is stored or recorded (ADR-0009).
        # A document that contains nothing usable is as much a failed fetch as
        # a 404, and must not enter the corpus looking healthy.
        segments = segment_document(source, content)
        implausible = check_document(source, content, segments)
        if has_errors(implausible):
            errors.append(
                FetchError(
                    source.id,
                    "implausible document: "
                    + "; ".join(problem.message for problem in implausible),
                )
            )
            continue

        checksum, stored_path = store_bytes(data_root, source, content)
        observation = FetchObservation(
            source_id=source.id,
            retrieved_at=datetime.now(UTC),
            requested_url=source.url,
            resolved_url=document.resolved_url,
            http_status=document.http_status,
            content_type=document.content_type,
            byte_count=len(content),
            checksum=checksum,
            stored_path=stored_path,
            content_checksum=document_content_checksum(segments),
            segment_count=len(segments),
            item_counts=item_counts(source, content),
        )
        append_observation(manifest_path, observation)
        observations.append(observation)

    return observations, errors


FETCHERS.update(
    {
        Parser.GITHUB_ISSUES: _github_fetcher(fetch_issues_document),
        Parser.GITHUB_MARKDOWN_TREE: _github_fetcher(fetch_markdown_tree_document),
    }
)
