"""GitHub API fetching, with a MockTransport standing in for the network."""

import json

import httpx
import pytest

from cra_assistant.github import (
    MAX_PAGES,
    TOKEN_VARIABLE,
    GithubError,
    IncompleteFetchError,
    auth_headers,
    fetch_issues_document,
    fetch_markdown_tree_document,
    parse_issues_url,
    parse_tree_url,
)

UA = "cra-assistant/test"


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def issue(number: int, *, pull_request: bool = False, body: str = "text") -> dict:
    item = {
        "number": number,
        "title": f"Issue {number}",
        "body": body,
        "state": "open",
        "created_at": "2026-01-01T00:00:00Z",
        "html_url": f"https://github.com/o/r/issues/{number}",
        "author_association": "MEMBER",
        "user": {"login": "somebody"},
    }
    if pull_request:
        item["pull_request"] = {"url": "..."}
    return item


# --- url parsing ------------------------------------------------------------


def test_an_issues_api_url_is_parsed() -> None:
    repository = parse_issues_url("https://api.github.com/repos/orcwg/cra-hub/issues")

    assert repository.full_name == "orcwg/cra-hub"


def test_a_rendered_issues_page_url_is_refused() -> None:
    """The whole point: the browser URL is not a source of data."""
    with pytest.raises(GithubError, match="not a GitHub issues API url"):
        parse_issues_url("https://github.com/orcwg/cra-hub/issues")


def test_a_tree_url_is_parsed() -> None:
    repository, ref, prefix = parse_tree_url("https://github.com/orcwg/cra-hub/tree/main/faq")

    assert (repository.full_name, ref, prefix) == ("orcwg/cra-hub", "main", "faq")


# --- issues -----------------------------------------------------------------


def test_issues_and_comments_are_assembled_into_one_document() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/comments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 900,
                        "issue_url": "https://api.github.com/repos/o/r/issues/2",
                        "body": "a reply",
                        "created_at": "",
                        "html_url": "",
                    }
                ],
            )
        return httpx.Response(200, json=[issue(2), issue(1)])

    with client_for(handler) as client:
        raw = fetch_issues_document(
            "https://api.github.com/repos/o/r/issues",
            client,
            user_agent=UA,
            timeout=5,
            sleep=lambda _: None,
        )

    document = json.loads(raw)
    assert [one["number"] for one in document["issues"]] == [1, 2], "sorted for stable bytes"
    assert document["comments"][0]["issue_number"] == 2
    assert document["repository"] == "o/r"


def test_pull_requests_are_dropped() -> None:
    """GitHub returns PRs from the issues endpoint; a merged patch is not
    community interpretation of the regulation."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/comments"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[issue(1), issue(2, pull_request=True)])

    with client_for(handler) as client:
        document = json.loads(
            fetch_issues_document(
                "https://api.github.com/repos/o/r/issues",
                client,
                user_agent=UA,
                timeout=5,
                sleep=lambda _: None,
            )
        )

    assert [one["number"] for one in document["issues"]] == [1]


def test_author_logins_are_not_copied_into_the_corpus() -> None:
    """The html_url identifies the author to anyone who follows it, so there is
    no reason to hold personal data we have no use for."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/comments"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[issue(1)])

    with client_for(handler) as client:
        raw = fetch_issues_document(
            "https://api.github.com/repos/o/r/issues",
            client,
            user_agent=UA,
            timeout=5,
            sleep=lambda _: None,
        )

    assert b"somebody" not in raw
    assert b"author_association" not in raw


def test_the_document_is_byte_stable_across_identical_fetches() -> None:
    """Otherwise every fetch would look like drift."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/comments"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[issue(2), issue(1)])

    with client_for(handler) as client:
        first = fetch_issues_document(
            "https://api.github.com/repos/o/r/issues",
            client,
            user_agent=UA,
            timeout=5,
            sleep=lambda _: None,
        )
        second = fetch_issues_document(
            "https://api.github.com/repos/o/r/issues",
            client,
            user_agent=UA,
            timeout=5,
            sleep=lambda _: None,
        )

    assert first == second


def test_pagination_follows_the_link_header() -> None:
    """GitHub paginates issues with an opaque cursor, so ?page=N is not equivalent."""
    pages = {
        "/repos/o/r/issues": (
            [issue(1)],
            {"Link": '<https://api.github.com/repos/o/r/issues?after=CURSOR>; rel="next"'},
        )
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/issues/comments"):
            return httpx.Response(200, json=[])
        if "after=CURSOR" in str(request.url):
            return httpx.Response(200, json=[issue(2)])
        body, headers = pages["/repos/o/r/issues"]
        return httpx.Response(200, json=body, headers=headers)

    with client_for(handler) as client:
        document = json.loads(
            fetch_issues_document(
                "https://api.github.com/repos/o/r/issues",
                client,
                user_agent=UA,
                timeout=5,
                sleep=lambda _: None,
            )
        )

    assert [one["number"] for one in document["issues"]] == [1, 2]
    assert any("after=CURSOR" in url for url in seen)


def test_pagination_stops_at_the_cap_and_refuses_the_truncated_collection() -> None:
    """Unauthenticated GitHub allows 60 requests an hour; an unbounded loop
    would exhaust it on one source and be rude while doing so. But stopping is
    not finishing: this used to return 800 items as if they were all of them."""
    requests = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/comments"):
            return httpx.Response(200, json=[])
        requests["count"] += 1
        return httpx.Response(
            200,
            json=[issue(requests["count"])],
            headers={"Link": '<https://api.github.com/repos/o/r/issues?after=X>; rel="next"'},
        )

    with client_for(handler) as client, pytest.raises(IncompleteFetchError) as caught:
        fetch_issues_document(
            "https://api.github.com/repos/o/r/issues",
            client,
            user_agent=UA,
            timeout=5,
            sleep=lambda _: None,
        )

    assert requests["count"] == MAX_PAGES
    assert f"{MAX_PAGES} items" in str(caught.value) and "more pages remaining" in str(caught.value)


def test_a_collection_ending_exactly_on_the_last_allowed_page_is_complete() -> None:
    requests = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/comments"):
            return httpx.Response(200, json=[])
        requests["count"] += 1
        last = requests["count"] == MAX_PAGES
        link = (
            {}
            if last
            else {"Link": '<https://api.github.com/repos/o/r/issues?after=X>; rel="next"'}
        )
        return httpx.Response(200, json=[issue(requests["count"])], headers=link)

    with client_for(handler) as client:
        document = json.loads(
            fetch_issues_document(
                "https://api.github.com/repos/o/r/issues",
                client,
                user_agent=UA,
                timeout=5,
                sleep=lambda _: None,
            )
        )

    assert len(document["issues"]) == MAX_PAGES


def test_an_exhausted_rate_limit_is_reported_clearly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"})

    with client_for(handler) as client, pytest.raises(GithubError, match="rate limit exhausted"):
        fetch_issues_document(
            "https://api.github.com/repos/o/r/issues",
            client,
            user_agent=UA,
            timeout=5,
            sleep=lambda _: None,
        )


def test_a_server_error_is_reported_with_its_status() -> None:
    with (
        client_for(lambda request: httpx.Response(500)) as client,
        pytest.raises(GithubError, match="HTTP 500"),
    ):
        fetch_issues_document(
            "https://api.github.com/repos/o/r/issues",
            client,
            user_agent=UA,
            timeout=5,
            sleep=lambda _: None,
        )


# --- tokens -----------------------------------------------------------------


def test_no_token_means_no_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)

    assert auth_headers() == {}


def test_a_token_is_used_but_never_returned_in_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "ghp-not-a-real-token"
    monkeypatch.setenv(TOKEN_VARIABLE, secret)

    assert auth_headers()["Authorization"].endswith(secret)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].endswith(secret)
        return httpx.Response(404)

    with client_for(handler) as client, pytest.raises(GithubError) as caught:
        fetch_issues_document(
            "https://api.github.com/repos/o/r/issues",
            client,
            user_agent=UA,
            timeout=5,
            sleep=lambda _: None,
        )

    assert secret not in str(caught.value)


# --- markdown tree ----------------------------------------------------------


def test_the_markdown_tree_reads_the_api_listing_and_raw_files() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "faq/a.md", "type": "blob"},
                        {"path": "faq/sub/b.md", "type": "blob"},
                        {"path": "faq/c.png", "type": "blob"},
                        {"path": "other/d.md", "type": "blob"},
                        {"path": "faq/sub", "type": "tree"},
                    ]
                },
            )
        return httpx.Response(200, text=f"# From {request.url.path}\n")

    with client_for(handler) as client:
        document = json.loads(
            fetch_markdown_tree_document(
                "https://github.com/o/r/tree/main/faq",
                client,
                user_agent=UA,
                timeout=5,
                sleep=lambda _: None,
            )
        )

    assert [one["path"] for one in document["files"]] == ["faq/a.md", "faq/sub/b.md"], (
        "only Markdown under the prefix"
    )
    assert document["ref"] == "main"


def test_an_empty_tree_is_an_error_rather_than_an_empty_document() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tree": []})

    with client_for(handler) as client, pytest.raises(GithubError, match="no Markdown files"):
        fetch_markdown_tree_document(
            "https://github.com/o/r/tree/main/faq",
            client,
            user_agent=UA,
            timeout=5,
            sleep=lambda _: None,
        )
