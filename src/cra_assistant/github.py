"""Fetching from GitHub through its API and raw file hosting.

Never from a rendered page (ADR-0009). github.com serves its issue lists and
discussion threads as an application shell that fills itself in from the
browser, so a static fetch of one yields a navigation menu and an error banner.
The API returns the same content as data.

Both fetchers assemble one deterministic JSON document per source, so the
existing content-addressed store, manifest and drift machinery keep working
unchanged: a document is still a document, it just was not served as one file.
"""

import json
import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"
TOKEN_VARIABLE = "GITHUB_TOKEN"

MAX_PAGES = 8
"""Cap on paginated requests per collection.

Unauthenticated GitHub allows 60 requests an hour, and a source that quietly
made 200 of them would be both rude and unreliable. With ``per_page=100`` this
covers 800 issues, which is comfortably more than the repositories we track and
still leaves budget for a second source in the same run.
"""

PER_PAGE = 100
MAX_TREE_FILES = 400
"""Refuse to walk an unexpectedly large tree rather than issue a request per
file for a repository that turned out to be enormous."""

ISSUES_URL = re.compile(r"^https://api\.github\.com/repos/([\w.-]+)/([\w.-]+)/issues/?$")
TREE_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/tree/([\w.\-/]+?)/(.+?)/?$")


class GithubError(RuntimeError):
    """A GitHub request could not be completed.

    Messages name status codes and rate-limit counts, never the token.
    """


def auth_headers() -> dict[str, str]:
    """Authorization header if a token is present, otherwise nothing.

    A token only raises the rate limit; every source here is public. The value
    is never logged, never echoed and never included in an error.
    """
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _request(
    client: httpx.Client,
    url: str,
    *,
    user_agent: str,
    timeout: float,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    response = client.get(
        url,
        params=params,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **auth_headers(),
        },
        timeout=timeout,
        follow_redirects=True,
    )
    remaining = response.headers.get("x-ratelimit-remaining")
    if response.status_code == 403 and remaining == "0":
        reset = response.headers.get("x-ratelimit-reset", "unknown")
        raise GithubError(
            f"GitHub rate limit exhausted (resets at epoch {reset}). "
            f"Set {TOKEN_VARIABLE} to raise the limit from 60 requests an hour."
        )
    if response.is_error:
        raise GithubError(f"GitHub returned HTTP {response.status_code} for {url}")
    return response


def _paginate(
    client: httpx.Client,
    url: str,
    *,
    user_agent: str,
    timeout: float,
    sleep: Callable[[float], None],
    delay: float,
) -> Iterator[dict[str, Any]]:
    """Follow ``Link: rel="next"`` rather than guessing page numbers.

    GitHub paginates issues with an opaque cursor, so constructing ?page=N is
    not equivalent and silently returns the wrong window on large repositories.
    """
    next_url: str | None = url
    params: dict[str, Any] | None = {"per_page": PER_PAGE, "state": "all"}
    for page in range(MAX_PAGES):
        if next_url is None:
            return
        if page:
            sleep(delay)
        response = _request(client, next_url, user_agent=user_agent, timeout=timeout, params=params)
        payload = response.json()
        if not isinstance(payload, list):
            raise GithubError(f"expected a JSON array from {next_url}")
        yield from payload
        params = None  # the next link already carries them
        next_url = response.links.get("next", {}).get("url")


@dataclass(frozen=True, slots=True)
class Repository:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_issues_url(url: str) -> Repository:
    match = ISSUES_URL.match(url)
    if not match:
        raise GithubError(
            f"not a GitHub issues API url: {url!r}. "
            "Expected https://api.github.com/repos/<owner>/<repo>/issues"
        )
    return Repository(match.group(1), match.group(2))


def fetch_issues_document(
    url: str,
    client: httpx.Client,
    *,
    user_agent: str,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    delay: float = 0.5,
) -> bytes:
    """Issues and their comments, as one deterministic JSON document.

    Comments come from the repository-wide endpoint rather than per issue: one
    request per issue would be hundreds of requests and would exhaust the
    unauthenticated budget on a single source.

    Pull requests are dropped. GitHub returns them from the issues endpoint, and
    a merged patch is not community interpretation of the regulation.

    Author logins are not copied into the corpus. The html_url identifies the
    author to anyone who follows it, so nothing is lost by declining to hold
    personal data we have no use for.
    """
    repository = parse_issues_url(url)
    base = f"{API_ROOT}/repos/{repository.full_name}"

    issues = [
        {
            "number": item["number"],
            "title": item.get("title") or "",
            "body": item.get("body") or "",
            "state": item.get("state") or "",
            "created_at": item.get("created_at") or "",
            "html_url": item.get("html_url") or "",
        }
        for item in _paginate(
            client,
            f"{base}/issues",
            user_agent=user_agent,
            timeout=timeout,
            sleep=sleep,
            delay=delay,
        )
        if "pull_request" not in item
    ]

    sleep(delay)
    comments = [
        {
            "id": item["id"],
            "issue_number": int(str(item.get("issue_url", "")).rsplit("/", 1)[-1] or 0),
            "body": item.get("body") or "",
            "created_at": item.get("created_at") or "",
            "html_url": item.get("html_url") or "",
        }
        for item in _paginate(
            client,
            f"{base}/issues/comments",
            user_agent=user_agent,
            timeout=timeout,
            sleep=sleep,
            delay=delay,
        )
    ]

    document = {
        "kind": "github-issues",
        "repository": repository.full_name,
        "issues": sorted(issues, key=lambda one: one["number"]),
        "comments": sorted(comments, key=lambda one: one["id"]),
    }
    return json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")


def parse_tree_url(url: str) -> tuple[Repository, str, str]:
    """Split ``https://github.com/<owner>/<repo>/tree/<ref>/<prefix>``."""
    match = TREE_URL.match(url)
    if not match:
        raise GithubError(
            f"not a GitHub tree url: {url!r}. "
            "Expected https://github.com/<owner>/<repo>/tree/<ref>/<path>"
        )
    return Repository(match.group(1), match.group(2)), match.group(3), match.group(4)


def fetch_markdown_tree_document(
    url: str,
    client: httpx.Client,
    *,
    user_agent: str,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    delay: float = 0.15,
) -> bytes:
    """Every Markdown file under a repository path, as one JSON document.

    One API request lists the tree; the files themselves come from raw file
    hosting, which is what the registry's decision says untrusted repository
    documents are read from. A repository tarball would be a single request and
    was rejected for being neither an API nor a raw file (ADR-0009).
    """
    repository, ref, prefix = parse_tree_url(url)
    listing = _request(
        client,
        f"{API_ROOT}/repos/{repository.full_name}/git/trees/{ref}",
        user_agent=user_agent,
        timeout=timeout,
        params={"recursive": "1"},
    ).json()

    paths = sorted(
        entry["path"]
        for entry in listing.get("tree", [])
        if entry.get("type") == "blob"
        and entry["path"].startswith(f"{prefix}/")
        and entry["path"].endswith(".md")
    )
    if not paths:
        raise GithubError(f"no Markdown files under {prefix!r} in {repository.full_name}@{ref}")
    if len(paths) > MAX_TREE_FILES:
        raise GithubError(
            f"{len(paths)} Markdown files under {prefix!r} exceeds the {MAX_TREE_FILES} cap"
        )

    files = []
    for index, path in enumerate(paths):
        if index:
            sleep(delay)
        raw_url = f"{RAW_ROOT}/{repository.full_name}/{ref}/{path}"
        response = client.get(
            raw_url,
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
        )
        if response.is_error:
            raise GithubError(f"HTTP {response.status_code} for {raw_url}")
        files.append({"path": path, "text": response.text})

    document = {
        "kind": "github-markdown-tree",
        "repository": repository.full_name,
        "ref": ref,
        "prefix": prefix,
        "files": files,
    }
    return json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")
