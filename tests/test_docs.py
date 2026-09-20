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
        "export-segments",
        "validate",
        "verify",
        "eval",
        "attack",
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


# --- the architecture document ----------------------------------------------
#
# docs/architecture.md names modules and functions instead of line numbers, so
# that it survives refactoring. That only helps if the names are real, so they
# are checked here.

ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"

REFERENCE = re.compile(r"\b([a-z_]+)\.([A-Za-z_]\w*)")

FILE_EXTENSIONS = frozenset({"toml", "jsonl", "json", "md", "py", "html", "yml", "yaml", "lock"})
"""Several data files share a stem with a module — `golden.toml` beside
`golden.py`, `manifest.jsonl` beside `manifest.py` — so an extension is not
an attribute reference."""


def package_modules() -> set[str]:
    return {path.stem for path in (REPO_ROOT / "src" / "cra_assistant").glob("*.py")}


def documented_references() -> set[tuple[str, str]]:
    """Every `module.attribute` in the document, prose and code blocks alike.

    Anchored on the real module list rather than on backticks, so the flow
    diagrams are covered too and local variables that look similar
    (`retriever.retrieve`, `budget.spend`) are skipped without a deny-list.
    """
    modules = package_modules()
    text = ARCHITECTURE.read_text(encoding="utf-8")
    return {
        (module, attribute)
        for module, attribute in REFERENCE.findall(text)
        if module in modules and attribute not in FILE_EXTENSIONS
    }


def test_the_architecture_document_names_things_that_exist() -> None:
    import importlib

    missing = []
    for module, attribute in sorted(documented_references()):
        found = importlib.import_module(f"cra_assistant.{module}")
        if not hasattr(found, attribute):
            missing.append(f"{module}.{attribute}")

    assert not missing, f"docs/architecture.md refers to things that do not exist: {missing}"


def test_the_reference_pattern_actually_matches_something() -> None:
    """Guard against the check above passing because the regex matches nothing."""
    references = documented_references()

    assert len(references) > 20, f"only found {len(references)} references — pattern broken?"


def test_the_architecture_document_links_only_to_files_that_exist() -> None:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    links = re.findall(r"\]\((?!https?:)([^)#]+)", text)
    missing = [link for link in links if not (ARCHITECTURE.parent / link).exists()]

    assert not missing, f"broken relative links: {missing}"


def test_the_readme_states_security_numbers_as_counts_not_percentages() -> None:
    """With three cases per vector a single case moves a percentage by 33
    points, and a percentage invites a comparison the sample size cannot
    support (ADR-0016).

    Fenced blocks are excluded because they are verbatim transcripts and corpus
    quotations: Article 64 sets a fine of "2,5 % of the its total worldwide
    annual turnover", and the rule is about how this project reports its own
    results, not about rewriting the regulation to fit it.
    """
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", text, flags=re.DOTALL)

    percentages = re.findall(r"\d+(?:[.,]\d+)?\s?%", prose)

    assert not percentages, f"README still quotes percentages: {percentages}"


TRUNCATED_CORPUS_REPORTS = (
    "baseline-2026-09-12-after-corpus-repair.md",
    "baseline-2026-09-12-untrusted-items-readded.md",
    "baseline-2026-09-12-with-depth-sweep.md",
    "attacks-2026-09-12.md",
    "attacks-2026-09-12b-corrected.md",
    "attacks-2026-09-12c-after-mitigation.md",
    "attacks-2026-09-12d-ablation.md",
    "attacks-2026-09-12e-framing-removed.md",
    "attacks-2026-09-12f-hardened.md",
    "attacks-2026-09-13a-reaxed.md",
    "attacks-2026-09-13b-claim-support.md",
    "attacks-2026-09-13c-claim-support-reaxed.md",
    "attacks-2026-09-13d-tier-rule-paired.md",
    "attacks-2026-09-13e-tier-rule-rerun.md",
    "attacks-2026-09-13f-rescored.md",
)


def test_every_report_measured_on_the_truncated_collection_says_so() -> None:
    """Supersede, do not delete: each keeps its numbers and states their corpus."""
    for name in TRUNCATED_CORPUS_REPORTS:
        text = (REPO_ROOT / "docs" / "eval" / name).read_text(encoding="utf-8")
        head = text.split("\n", 4)
        assert "800-comment truncation" in "\n".join(head[:4]), name


def test_every_baseline_reports_the_date_its_filename_claims() -> None:
    """Baselines are append-only (CLAUDE.md), and a rerun once overwrote one.

    `baseline-2026-09-19-passages.md` was regenerated in place the next day, so
    a file named for the 19th opened with "Retrieval baseline — 2026-09-20" and
    the measurement it used to hold was only in git history. A new measurement
    gets a new file; this catches the overwrite rather than the policy.
    """
    baselines = sorted((REPO_ROOT / "docs" / "eval").glob("baseline-*.md"))
    assert baselines, "no baselines found"
    for path in baselines:
        claimed = path.name.removeprefix("baseline-")[:10]
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        assert first == f"# Retrieval baseline — {claimed}", (
            f"{path.name} starts with {first!r}, which is not the date its name claims"
        )
