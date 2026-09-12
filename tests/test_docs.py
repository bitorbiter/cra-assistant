"""The documentation must describe the CLI that exists.

Written after finding that two earlier edits to CLAUDE.md had silently failed to
match: the command list was missing four subcommands and described `verify` as
"report-only, always exits 0" long after it had started blocking. Documentation
that drifts is worse than none, because it is believed.
"""

import re
from pathlib import Path

import pytest

from cra_assistant.cli import subcommand_names

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ["CLAUDE.md", "README.md"]

INVOCATION = re.compile(r"\bcra-assistant\s+(?:--[\w-]+(?:[= ][^\s]+)?\s+)*([a-z][\w-]*)")


def documented_subcommands(document: str) -> set[str]:
    text = (REPO_ROOT / document).read_text(encoding="utf-8")
    return {match.group(1) for match in INVOCATION.finditer(text)}


def test_the_cli_registers_the_commands_we_think_it_does() -> None:
    """A canary: if this list changes, the documents below must change with it."""
    assert subcommand_names() == {
        "fetch",
        "parse",
        "validate",
        "verify",
        "eval",
        "ask",
    }


@pytest.mark.parametrize("document", DOCUMENTS)
def test_documents_mention_no_command_the_cli_does_not_have(document: str) -> None:
    invented = documented_subcommands(document) - subcommand_names()

    assert not invented, f"{document} documents commands that do not exist: {sorted(invented)}"


@pytest.mark.parametrize("document", DOCUMENTS)
def test_documents_mention_every_command_the_cli_has(document: str) -> None:
    missing = subcommand_names() - documented_subcommands(document)

    assert not missing, f"{document} is missing commands: {sorted(missing)}"


def test_the_pattern_actually_matches_a_documented_invocation() -> None:
    """Guard against the checks above passing because the regex matches nothing."""
    assert documented_subcommands("CLAUDE.md"), "no invocations found — the pattern is broken"
