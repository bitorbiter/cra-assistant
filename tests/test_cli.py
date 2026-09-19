"""CLI behaviour. `verify` needs no network; `fetch` is covered in test_fetch.py."""

import argparse
import hashlib
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cra_assistant.cli import main, note_for
from cra_assistant.fetch import MANIFEST_FILENAME
from cra_assistant.manifest import FetchObservation, append_observation
from cra_assistant.models import TrustTier
from cra_assistant.paths import DEFAULT_PINS_PATH, DEFAULT_REGISTRY_PATH
from cra_assistant.registry import load_registry
from cra_assistant.segment import document_content_checksum, segment_document
from cra_assistant.verify import DriftStatus, SourceVerdict, load_pins

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_verify_on_a_fresh_clone_reports_without_failing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No data/ means nothing can be judged, so the armed gate must still pass."""
    exit_code = main(["--data-root", str(tmp_path), "verify"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "unfetched" in output
    assert "0 blocking" in output


def test_export_segments_without_fetched_bytes_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--data-root", str(tmp_path), "export-segments"])

    assert exit_code == 1
    assert "Run `cra-assistant fetch` first" in capsys.readouterr().err


def test_fetch_rejects_an_unknown_source_id(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="unknown source ids: no-such-source"):
        main(["--data-root", str(tmp_path), "fetch", "--source", "no-such-source"])


def test_every_declared_source_is_pinned() -> None:
    """Committed data must agree with itself: a source with no pin is an oversight."""
    declared = {source.id for source in load_registry(DEFAULT_REGISTRY_PATH).sources}
    pinned = {pin.source_id for pin in load_pins(DEFAULT_PINS_PATH).pins}

    assert declared == pinned, f"unpinned: {declared - pinned}; stale pins: {pinned - declared}"


@pytest.mark.parametrize(
    ("tier", "raw", "content", "expected"),
    [
        (TrustTier.TRUSTED, DriftStatus.CLEAN, DriftStatus.CLEAN, ""),
        (TrustTier.UNTRUSTED, DriftStatus.CLEAN, DriftStatus.CLEAN, ""),
        # Raw drift with clean content is the ordinary EUR-Lex refetch.
        (TrustTier.TRUSTED, DriftStatus.DRIFTED, DriftStatus.CLEAN, "recorded, no action needed"),
        (
            TrustTier.TRUSTED,
            DriftStatus.DRIFTED,
            DriftStatus.DRIFTED,
            "BLOCKING — acknowledge: update the pin and say why",
        ),
        (
            TrustTier.UNTRUSTED,
            DriftStatus.DRIFTED,
            DriftStatus.DRIFTED,
            "recorded, no action needed",
        ),
        (
            TrustTier.TRUSTED,
            DriftStatus.CLEAN,
            DriftStatus.UNPINNED,
            "BLOCKING — no pin yet: add one",
        ),
        # Status beats tier: an unfetched source needs fetching either way.
        (
            TrustTier.TRUSTED,
            DriftStatus.UNFETCHED,
            DriftStatus.UNFETCHED,
            "run `cra-assistant fetch`",
        ),
        (
            TrustTier.UNTRUSTED,
            DriftStatus.UNFETCHED,
            DriftStatus.UNFETCHED,
            "run `cra-assistant fetch`",
        ),
    ],
)
def test_the_report_note_matches_tier_and_status(
    tier: TrustTier, raw: DriftStatus, content: DriftStatus, expected: str
) -> None:
    verdict = SourceVerdict(
        source_id="a-source",
        tier=tier,
        status=raw,
        content_status=content,
        pinned_checksum=None,
        observed_checksum=None,
        pinned_content_checksum=None,
        observed_content_checksum=None,
    )

    assert note_for(verdict) == expected


UNVERIFIED_ONLY_GOLDEN = """
[[items]]
id = "drafted-item"
question = "Who counts as a manufacturer?"
lang = "en"
vocabulary = "statute"
answer_type = "answerable"
expected_segment_ids = ["cra-en:article:3"]
verified = false
"""


def test_eval_refuses_when_no_item_is_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scoring drafted labels by default would produce a number that looks like
    a measurement and is not."""
    golden = tmp_path / "golden.toml"
    golden.write_text(UNVERIFIED_ONLY_GOLDEN, encoding="utf-8")

    exit_code = main(["--data-root", str(tmp_path), "eval", "--golden", str(golden)])

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "verified = false" in error
    assert "--include-unverified" in error


def test_eval_scores_the_verified_items_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Once items are verified by hand, the default run scores those and only
    those; with an empty data root it then stops for want of a corpus."""
    exit_code = main(["--data-root", str(tmp_path), "eval"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "No verified golden items" not in captured.err
    assert "Run `cra-assistant fetch` first" in captured.err


def test_eval_with_include_unverified_gets_past_the_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With an empty data root it then stops for the other reason: no corpus."""
    exit_code = main(["--data-root", str(tmp_path), "eval", "--include-unverified"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "verified = false" not in captured.err
    assert "Run `cra-assistant fetch` first" in captured.err


# --- end to end against a real corpus, offline -------------------------------
#
# Every CLI test above runs against an empty data root, so `segments_for`
# returns nothing and each command exits early. That gap let a genuine bug ship:
# `build_retriever` still unpacked the two-tuple `segments_for` returned before
# it grew a `raw` element, and `ask` crashed with "too many values to unpack" on
# any real corpus. These tests build a small corpus so the commands actually run.


@pytest.fixture
def corpus(tmp_path: Path) -> argparse.Namespace:
    """A data root and registry holding one real (excerpted) document."""
    excerpt = (Path(__file__).parent / "fixtures" / "cra_excerpt_en.html").read_bytes()
    registry = tmp_path / "sources.toml"
    registry.write_text(
        textwrap.dedent("""
            [[sources]]
            id = "cra-eurlex-en"
            citation_prefix = "cra-en"
            short_title = "Regulation (EU) 2024/2847"
            title = "Cyber Resilience Act excerpt"
            url = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32024R2847"
            lang = "en"
            tier = "trusted"
            licence = "UNKNOWN"
            parser = "eurlex-html"
        """),
        encoding="utf-8",
    )

    data_root = tmp_path / "data"
    digest = hashlib.sha256(excerpt).hexdigest()
    stored = Path("raw") / "cra-eurlex-en" / f"{digest[:12]}.html"
    (data_root / stored).parent.mkdir(parents=True)
    (data_root / stored).write_bytes(excerpt)

    append_observation(
        data_root / MANIFEST_FILENAME,
        FetchObservation(
            source_id="cra-eurlex-en",
            retrieved_at=datetime.now(UTC),
            requested_url="https://eur-lex.europa.eu/x",
            resolved_url="https://eur-lex.europa.eu/x",
            http_status=200,
            content_type="text/html",
            byte_count=len(excerpt),
            checksum=f"sha256:{digest}",
            stored_path=stored.as_posix(),
        ),
    )
    # A pin matching this corpus, so the suite can exercise verify PASSING.
    # Computed here rather than committed: a hardcoded digest would make every
    # deliberate segmentation change look like drift in a test whose subject is
    # the comparison mechanism, not the corpus. Real drift-catching is done by
    # the committed pins over the real sources.
    source = load_registry(registry).sources[0]
    pins = tmp_path / "pins.toml"
    pins.write_text(
        "[[pins]]\n"
        f'source_id = "{source.id}"\n'
        f'checksum = "sha256:{digest}"\n'
        f'content_checksum = "{document_content_checksum(segment_document(source, excerpt))}"\n'
        'note = "the excerpt used by the test suite"\n',
        encoding="utf-8",
    )
    return argparse.Namespace(registry=registry, data_root=data_root, pins=pins)


def run(corpus: argparse.Namespace, *arguments: str) -> int:
    return main(
        ["--registry", str(corpus.registry), "--data-root", str(corpus.data_root), *arguments]
    )


def test_ask_builds_a_retriever_over_a_real_corpus(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """The regression: this crashed with "too many values to unpack"."""
    exit_code = run(corpus, "ask", "--show-prompt", "-k", "2", "What is the subject matter?")

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "CONTEXT" in output
    assert "cra-en:article:" in output


def test_export_segments_writes_a_dump_for_a_real_corpus(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(corpus, "export-segments") == 0
    assert "recitals" in capsys.readouterr().out
    assert (corpus.data_root / "exports" / "cra-eurlex-en.jsonl").exists()
    assert not (corpus.data_root / "segments").exists(), "the old name is gone"


def test_validate_runs_over_a_real_corpus(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(corpus, "validate") == 0
    assert "0 errors" in capsys.readouterr().out


def test_eval_scores_against_a_real_corpus(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(corpus, "eval", "--include-unverified") == 0
    assert "Drift report" not in capsys.readouterr().out


def test_verify_passes_when_the_pin_matches(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the gate.

    A fixture that always exits 1 cannot tell an expected block from a new one,
    so the suite has to see the passing path too.
    """
    exit_code = run(corpus, "verify", "--pins", str(corpus.pins))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "0 blocking" in output
    assert "BLOCKING" not in output
    assert "clean" in output


def test_verify_blocks_end_to_end_when_trusted_content_drifts(
    corpus: argparse.Namespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole gate, exercised for real.

    Pointed at the committed pins, which cover the full regulation, the fixture
    corpus holds only an excerpt — so the content checksums genuinely differ.
    That is exactly the condition the gate exists for, and it must exit 1.
    """
    exit_code = run(corpus, "verify")

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "BLOCKING" in output
    assert "1 blocking" in output


def test_the_answer_shows_the_quotation_behind_each_citation() -> None:
    from cra_assistant.cli import format_answer
    from cra_assistant.generate import Answer
    from cra_assistant.models import Segment, SegmentKind, TrustTier

    segment = Segment(
        id="cra-en:article:3",
        source_id="cra-eurlex-en",
        tier=TrustTier.TRUSTED,
        kind=SegmentKind.ARTICLE,
        number="3",
        title="Definitions",
        text="manufacturer means a natural or legal person",
        citation="Regulation (EU) 2024/2847, Article 3",
        source_sha256="sha256:" + "a" * 64,
        content_sha256="sha256:" + "b" * 64,
        lang="en",
        order=0,
    )
    answer = Answer(
        question="who is a manufacturer",
        text="A manufacturer is a natural or legal person.",
        citations=(segment,),
        abstained=False,
        reason="",
        retrieved=(segment,),
        request_id="r" * 8,
        model="gpt-4o-mini-2024-07-18",
        spans=("manufacturer means a natural or legal person",),
    )

    rendered = format_answer(answer)

    assert "cra-en:article:3" in rendered
    assert '"manufacturer means a natural or legal person"' in rendered


def test_a_non_positive_retrieval_depth_is_refused_at_the_boundary() -> None:
    import pytest

    from cra_assistant.cli import positive_depth

    assert positive_depth("8") == 8
    for bad in ("0", "-1"):
        with pytest.raises(argparse.ArgumentTypeError, match="at least 1"):
            positive_depth(bad)


def test_run_attack_survives_a_provider_that_always_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every trial failing used to crash the runner after the calls were spent:
    a case with no completed run has no representative, and the void check read
    one. The report has to be written, saying that nothing was measured."""
    from cra_assistant.models import Segment, SegmentKind
    from cra_assistant.registry import load_registry

    def fake_segments(args: argparse.Namespace) -> list:
        registry = load_registry(args.registry)
        out = []
        for source in registry.sources:
            segment = Segment(
                id=f"{source.citation_prefix}:section:000000000001",
                source_id=source.id,
                tier=source.tier,
                kind=SegmentKind.SECTION,
                number="000000000001",
                title="",
                text="A sentence about manufacturers and vulnerabilities under the regulation.",
                citation=f"{source.short_title}, section",
                source_sha256="sha256:" + "a" * 64,
                content_sha256="sha256:" + "b" * 64,
                lang=source.lang,
                order=0,
            )
            out.append((source, b"raw", [segment]))
        return out

    class AlwaysFails:
        def complete(self, *, model: str, messages: object, max_tokens: int) -> object:
            raise RuntimeError("provider is down")

    monkeypatch.setattr("cra_assistant.cli.segments_for", fake_segments)
    monkeypatch.setattr("cra_assistant.cli.client_from_environment", lambda **kw: AlwaysFails())

    report = tmp_path / "attack.md"
    exit_code = main(
        [
            "--data-root",
            str(tmp_path),
            "attack",
            "--runs",
            "2",
            "--case",
            "delim-literal",
            "--out",
            str(report),
        ]
    )

    assert exit_code != 0, "a run that measured nothing must not report success"
    assert report.exists(), "the report is written even when every trial failed"
    text = report.read_text(encoding="utf-8")
    assert "2 attempted, 0 completed, 2 failed" in text
    assert "Every trial failed for `delim-literal`" in text
    assert "no measurement" in text
    assert "VOID" in capsys.readouterr().err


def test_the_community_answer_control_separates_failure_from_refusal(tmp_path: Path) -> None:
    """With every model call failing, this control used to report that all five
    items had lost their answer — an apparent collapse of the untrusted tier
    measured from nothing at all."""
    from cra_assistant.cli import run_tier_collapse
    from cra_assistant.golden import AnswerType, load_golden_set
    from cra_assistant.models import Segment, SegmentKind, TrustTier

    golden = REPO_ROOT / "eval" / "golden.toml"
    control = [
        item
        for item in load_golden_set(golden).items
        if item.answer_type is AnswerType.UNTRUSTED_ONLY
    ]
    assert control, "the control set is what this test is about"

    # One segment per item, so retrieval succeeds and the provider is the only
    # thing that fails. Otherwise the items abstain for want of context, which
    # is a real answer, not a missing measurement.
    production = [
        Segment(
            id=f"orcwg-faq:section:{index:012x}",
            source_id="orcwg-cra-faq",
            tier=TrustTier.UNTRUSTED,
            kind=SegmentKind.SECTION,
            number=f"{index:012x}",
            title="Community answer",
            text=item.question + " Community discussion of exactly this question.",
            citation="ORC WG CRA FAQ, section",
            source_sha256="sha256:" + "a" * 64,
            content_sha256="sha256:" + "b" * 64,
            lang="en",
            order=index,
        )
        for index, item in enumerate(control)
    ]

    class AlwaysFails:
        def complete(self, *, model: str, messages: object, max_tokens: int) -> object:
            raise RuntimeError("provider is down")

    args = argparse.Namespace(golden=golden, runs=2, k=4, data_root=tmp_path)

    text = "\n".join(run_tier_collapse(args, production, AlwaysFails(), "m"))

    assert "0 of 0 measured items lost their answer entirely" in text
    assert "no measurement" in text
    assert f"{len(control)} of {len(control)} measured items lost" not in text
