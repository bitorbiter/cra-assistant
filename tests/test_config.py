"""Reading `.env`.

Written after discovering that `.env.example`, the README and the missing-key
error had all told the user to put their key in `.env` since step 1, while
nothing in the codebase ever read the file.
"""

import os
from pathlib import Path

import pytest

from cra_assistant.config import apply_dotenv, load_dotenv, parse_env


def test_a_plain_assignment_is_read() -> None:
    assert parse_env("OPENAI_API_KEY=sk-abc123") == {"OPENAI_API_KEY": "sk-abc123"}


def test_comments_and_blank_lines_are_ignored() -> None:
    text = "\n# a comment\n\n  # indented comment\nKEY=value\n"

    assert parse_env(text) == {"KEY": "value"}


def test_an_export_prefix_is_tolerated() -> None:
    """`.env` files are often sourced by a shell as well as read by a program."""
    assert parse_env("export KEY=value") == {"KEY": "value"}


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('KEY="value"', "value"),
        ("KEY='value'", "value"),
        ("KEY=value", "value"),
        ("KEY = value ", "value"),
        ('KEY="with spaces"', "with spaces"),
        ("KEY=\"mismatched'", "\"mismatched'"),
    ],
)
def test_surrounding_quotes_are_stripped_only_when_matched(line: str, expected: str) -> None:
    assert parse_env(line)["KEY"] == expected


def test_a_value_containing_an_equals_sign_survives() -> None:
    """Base64 and JWT-shaped secrets routinely contain '='."""
    assert parse_env("KEY=abc=def==")["KEY"] == "abc=def=="


def test_malformed_lines_are_skipped_rather_than_guessed_at() -> None:
    assert parse_env("NOT_AN_ASSIGNMENT\n=novalue\nGOOD=yes") == {"GOOD": "yes"}


def test_a_missing_file_is_normal(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "absent") == {}


def test_the_real_environment_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported variable is a deliberate act — a CI secret, a one-off
    override — and a file on disk must not silently replace it."""
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "from-environment")

    applied = apply_dotenv(path)

    assert os.environ["OPENAI_API_KEY"] == "from-environment"
    assert applied == []


def test_a_variable_only_in_the_file_is_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    applied = apply_dotenv(path)

    assert os.environ["OPENAI_API_KEY"] == "from-file"
    assert applied == ["OPENAI_API_KEY"]


def test_an_empty_value_is_not_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` ships `OPENAI_API_KEY=` with no value; copying it verbatim
    must not set an empty key that then fails confusingly further in."""
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert apply_dotenv(path) == []
    assert "OPENAI_API_KEY" not in os.environ


def test_only_names_are_returned_never_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The return value ends up in diagnostics; a secret must not."""
    secret = "sk-should-never-be-returned"
    path = tmp_path / ".env"
    path.write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    applied = apply_dotenv(path)

    assert secret not in "".join(applied)


def test_the_committed_example_parses(tmp_path: Path) -> None:
    """Whatever we ship as a template must at least be readable by our own parser."""
    example = Path(__file__).resolve().parents[1] / ".env.example"

    assert "OPENAI_API_KEY" in load_dotenv(example)
