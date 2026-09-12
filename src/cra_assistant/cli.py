"""Command line entry points: ``fetch`` and ``verify``.

Two commands, deliberately separate. ``fetch`` records what upstream served;
``verify`` reports how that differs from what a human approved. Keeping them
apart is what lets a changed source still download (ADR-0003).

argparse rather than a CLI framework: two subcommands and a handful of flags do
not justify a dependency.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import httpx

from cra_assistant import __version__
from cra_assistant.config import apply_dotenv
from cra_assistant.evaluate import render_report, unknown_gold_ids
from cra_assistant.evaluate import run as run_evaluation
from cra_assistant.fetch import (
    DEFAULT_POLICY,
    MANIFEST_FILENAME,
    FetchPolicy,
    fetch_sources,
)
from cra_assistant.generate import (
    DEFAULT_MODEL,
    Answer,
    CallBudget,
    GenerationError,
    ask,
    client_from_environment,
)
from cra_assistant.golden import DEFAULT_GOLDEN_PATH, load_golden_set
from cra_assistant.manifest import latest_by_source, load_manifest
from cra_assistant.models import Segment, SegmentKind, Source, TrustTier
from cra_assistant.paths import DEFAULT_DATA_ROOT, DEFAULT_PINS_PATH, DEFAULT_REGISTRY_PATH
from cra_assistant.plausibility import check_document
from cra_assistant.prompt import build_messages
from cra_assistant.registry import load_registry
from cra_assistant.retrieve import Bm25Retriever
from cra_assistant.segment import document_content_checksum, segment_document
from cra_assistant.telemetry import log_call
from cra_assistant.validate import Severity, has_errors, validate_segments
from cra_assistant.verify import (
    GATE_ENABLED,
    DriftStatus,
    SourceVerdict,
    exit_code_for,
    load_pins,
    observed_content_checksums,
    verify,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cra-assistant", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY_PATH, help="source registry TOML"
    )
    parser.add_argument(
        "--data-root", type=Path, default=DEFAULT_DATA_ROOT, help="where fetched bytes are stored"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    fetch_command = subcommands.add_parser("fetch", help="download declared sources")
    fetch_command.add_argument(
        "--source",
        dest="source_ids",
        action="append",
        metavar="ID",
        help="fetch only this source; repeatable. Default: every source.",
    )
    fetch_command.add_argument("--timeout", type=float, default=DEFAULT_POLICY.timeout)
    fetch_command.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_POLICY.delay_between_sources,
        help="seconds to wait between sources",
    )
    fetch_command.set_defaults(handler=run_fetch)

    parse_command = subcommands.add_parser(
        "parse", help="segment fetched sources and write them to the data root"
    )
    parse_command.add_argument("--source", dest="source_ids", action="append", metavar="ID")
    parse_command.set_defaults(handler=run_parse)

    validate_command = subcommands.add_parser(
        "validate", help="check segmented documents for structural problems"
    )
    validate_command.add_argument("--source", dest="source_ids", action="append", metavar="ID")
    validate_command.set_defaults(handler=run_validate)

    ask_command = subcommands.add_parser(
        "ask", help="answer a question from the corpus, with citations"
    )
    ask_command.add_argument("question", help="the question, in any corpus language")
    ask_command.add_argument("-k", type=int, default=8, help="segments to retrieve (default: 8)")
    ask_command.add_argument(
        "--model", default=None, help=f"model id (default: $CRA_MODEL or {DEFAULT_MODEL})"
    )
    ask_command.add_argument(
        "--show-prompt",
        action="store_true",
        help="print the assembled prompt and exit without calling the model. "
        "No API key needed; useful for inspecting how the trust boundary is rendered.",
    )
    # ask always indexes the whole corpus; the subset flag belongs to the
    # commands that act on individual sources.
    ask_command.set_defaults(handler=run_ask, source_ids=None)

    eval_command = subcommands.add_parser(
        "eval", help="score retrieval against the golden set (offline, no API key)"
    )
    eval_command.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN_PATH)
    eval_command.add_argument(
        "--include-unverified",
        action="store_true",
        help="score items whose gold labels nobody has checked by hand. The report "
        "says so in its header.",
    )
    eval_command.add_argument("-k", type=int, default=10, help="retrieval depth (default: 10)")
    eval_command.add_argument(
        "--out", type=Path, default=None, help="also write the report to this file"
    )
    eval_command.set_defaults(handler=run_eval, source_ids=None)

    verify_command = subcommands.add_parser(
        "verify", help="report drift against the committed pins (report only)"
    )
    verify_command.add_argument("--pins", type=Path, default=DEFAULT_PINS_PATH)
    verify_command.set_defaults(handler=run_verify)

    return parser


def subcommand_names() -> set[str]:
    """Every registered subcommand.

    argparse has no public accessor for this, so the one piece of reaching into
    its internals lives here, next to the parser it describes, rather than in
    the test that needs it.
    """
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def select_sources(registry_path: Path, source_ids: Sequence[str] | None) -> list[Source]:
    sources = list(load_registry(registry_path).sources)
    if not source_ids:
        return sources

    by_id = {source.id: source for source in sources}
    unknown = sorted(set(source_ids) - by_id.keys())
    if unknown:
        raise SystemExit(f"unknown source ids: {', '.join(unknown)}")
    return [by_id[source_id] for source_id in source_ids]


SEGMENTS_DIRNAME = "segments"

PLURAL = {
    SegmentKind.RECITAL: "recitals",
    SegmentKind.ARTICLE: "articles",
    SegmentKind.ANNEX: "annexes",
    SegmentKind.SECTION: "sections",
}


def segments_for(args: argparse.Namespace) -> list[tuple[Source, bytes, list[Segment]]]:
    """Segment the newest stored copy of each selected source.

    Reads from the fetch manifest rather than globbing the data directory, so
    what gets segmented is exactly what was last observed.
    """
    sources = select_sources(args.registry, args.source_ids)
    latest = latest_by_source(load_manifest(args.data_root / MANIFEST_FILENAME))

    results: list[tuple[Source, bytes, list[Segment]]] = []
    missing: list[str] = []
    for source in sources:
        observation = latest.get(source.id)
        stored = args.data_root / observation.stored_path if observation else None
        if stored is None or not stored.exists():
            missing.append(source.id)
            continue
        raw = stored.read_bytes()
        results.append((source, raw, segment_document(source, raw)))

    if missing:
        sys.stdout.flush()
        print(
            f"not fetched, skipping: {', '.join(missing)}. Run `cra-assistant fetch` first.",
            file=sys.stderr,
        )
    return results


def run_parse(args: argparse.Namespace) -> int:
    results = segments_for(args)
    if not results:
        return 1

    output_dir = args.data_root / SEGMENTS_DIRNAME
    output_dir.mkdir(parents=True, exist_ok=True)
    for source, _raw, segments in results:
        path = output_dir / f"{source.id}.jsonl"
        path.write_text(
            "".join(segment.model_dump_json() + "\n" for segment in segments), encoding="utf-8"
        )
        kinds = Counter(segment.kind for segment in segments)
        summary = ", ".join(f"{count} {PLURAL[kind]}" for kind, count in sorted(kinds.items()))
        print(
            f"{source.id:<32} {len(segments):>4} segments ({summary})  "
            f"content {document_content_checksum(segments)[7:19]}…"
        )
    return 0


def run_validate(args: argparse.Namespace) -> int:
    results = segments_for(args)
    if not results:
        return 1

    failed = False
    for source, raw, segments in results:
        # Structure and plausibility are different questions (ADR-0009): one asks
        # whether the document is shaped right, the other whether it contains
        # anything at all. Both are errors, and both are reported here.
        problems = [
            *check_document(source, raw, segments),
            *validate_segments(segments, source.parser),
        ]
        errors = [problem for problem in problems if problem.severity is Severity.ERROR]
        warnings = [problem for problem in problems if problem.severity is Severity.WARNING]
        print(
            f"{source.id:<32} {len(segments):>4} segments  "
            f"{len(errors)} errors, {len(warnings)} warnings"
        )
        for problem in problems:
            where = f" [{problem.segment_id}]" if problem.segment_id else ""
            print(f"  {problem.severity.value:<8} {problem.code}{where}: {problem.message}")
        failed = failed or has_errors(problems)

    if failed:
        sys.stdout.flush()
        print(
            "\nStructural errors mean a marker stopped matching. Fix the marker, not the check.",
            file=sys.stderr,
        )
    return 1 if failed else 0


def build_retriever(args: argparse.Namespace) -> Bm25Retriever | None:
    """Rebuild the index from the newest stored bytes. No persistence: this
    index is disposable (ADR-0006) and rebuilding takes under a second."""
    results = segments_for(args)
    if not results:
        return None
    return Bm25Retriever([segment for _, _raw, segments in results for segment in segments])


def format_answer(answer: Answer) -> str:
    lines: list[str] = []
    if answer.abstained:
        lines.append("No answer from the corpus.")
        lines.append(f"Reason: {answer.reason}")
    else:
        lines.append(answer.text)
        lines.append("")
        lines.append("Citations:")
        for segment in answer.citations:
            marker = " [UNTRUSTED]" if segment.tier is not TrustTier.TRUSTED else ""
            lines.append(f"  {segment.id:<28} {segment.citation}{marker}")
        if answer.cited_untrusted:
            lines.append(
                "\nAn untrusted source was cited. It is third-party commentary, not the regulation."
            )
        if answer.reason:
            lines.append(f"\nNote: {answer.reason}")

    lines.append("")
    lines.append(
        f"[{answer.request_id[:8]}] retrieved {len(answer.retrieved)}, "
        f"cited {len(answer.citations)}, model {answer.model}"
    )
    return "\n".join(lines)


def run_ask(args: argparse.Namespace) -> int:
    retriever = build_retriever(args)
    if retriever is None:
        return 1

    if args.show_prompt:
        retrieved = retriever.retrieve(args.question, args.k)
        for message in build_messages(args.question, retrieved):
            print(f"=== {message['role']} ===")
            print(message["content"])
            print()
        return 0

    try:
        client = client_from_environment()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        answer, record = ask(
            args.question,
            retriever,
            client=client,
            k=args.k,
            model=args.model,
            budget=CallBudget(),
        )
    except GenerationError as error:
        log_call(args.data_root, error.record)
        print(str(error), file=sys.stderr)
        return 1

    log_call(args.data_root, record)
    print(format_answer(answer))
    return 0


def run_eval(args: argparse.Namespace) -> int:
    golden = load_golden_set(args.golden)
    items = golden.items if args.include_unverified else golden.verified

    if not items:
        sys.stdout.flush()
        print(
            f"No verified golden items: all {len(golden.items)} are `verified = false`.\n"
            "Nobody has checked the gold labels by hand, so scoring them would produce a "
            "number that looks like a measurement and is not.\n"
            "Pass --include-unverified to score them anyway; the report will say so.",
            file=sys.stderr,
        )
        return 1

    results_by_source = segments_for(args)
    if not results_by_source:
        return 1
    segments = [segment for _, _raw, found in results_by_source for segment in found]
    retriever = Bm25Retriever(segments)

    outcomes = run_evaluation(items, retriever, k=args.k)
    report = render_report(
        outcomes,
        corpus_size=len(segments),
        k=args.k,
        included_unverified=args.include_unverified,
        unverified_count=len(golden.unverified),
        broken_labels=unknown_gold_ids(outcomes, (segment.id for segment in segments)),
    )
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"written to {args.out}", file=sys.stderr)

    # Reporting only. No threshold is justified before a baseline exists; the
    # gate arrives in a later step (ADR-0007).
    return 0


def run_fetch(args: argparse.Namespace) -> int:
    sources = select_sources(args.registry, args.source_ids)
    policy = FetchPolicy(timeout=args.timeout, delay_between_sources=args.delay)

    with httpx.Client() as client:
        observations, errors = fetch_sources(
            sources, data_root=args.data_root, client=client, policy=policy
        )

    for observation in observations:
        print(
            f"fetched  {observation.source_id:<32} "
            f"{observation.http_status}  "
            f"{observation.byte_count:>9,d} bytes  "
            f"{observation.checksum[:19]}…  {observation.stored_path}"
        )
    for error in errors:
        print(f"FAILED   {error.source_id:<32} {error.reason}", file=sys.stderr)

    print(
        f"\n{len(observations)} fetched, {len(errors)} failed. "
        f"Manifest: {args.data_root / MANIFEST_FILENAME}"
    )
    return 1 if errors else 0


ACKNOWLEDGEMENT_BY_STATUS = {
    DriftStatus.DRIFTED: "acknowledge: update the pin and say why",
    DriftStatus.UNPINNED: "no pin yet: add one",
}


def note_for(verdict: SourceVerdict) -> str:
    """The one-line instruction beside a verdict.

    Status decides before tier does: an unfetched source needs fetching whatever
    its tier, and calling that "no action needed" would be wrong in both.
    """
    if verdict.status is DriftStatus.UNFETCHED:
        return "run `cra-assistant fetch`"
    if verdict.blocking:
        return "BLOCKING — " + ACKNOWLEDGEMENT_BY_STATUS[verdict.content_status]
    if verdict.status is DriftStatus.CLEAN and verdict.content_status is DriftStatus.CLEAN:
        return ""
    return "recorded, no action needed"


def format_report(verdicts: Sequence[SourceVerdict]) -> str:
    lines = [
        "Drift report",
        "",
        f"  {'source':<32} {'raw':<10} {'content':<10} note",
        f"  {'-' * 32} {'-' * 10} {'-' * 10} {'-' * 30}",
    ]
    for tier in (TrustTier.TRUSTED, TrustTier.UNTRUSTED):
        in_tier = [verdict for verdict in verdicts if verdict.tier is tier]
        if not in_tier:
            continue
        lines.append(f"{tier} sources")
        for verdict in sorted(in_tier, key=lambda v: v.source_id):
            lines.append(
                f"  {verdict.source_id:<32} {verdict.status:<10} "
                f"{verdict.content_status:<10} {note_for(verdict)}".rstrip()
            )
        lines.append("")

    raw_drift = sum(1 for verdict in verdicts if verdict.status is DriftStatus.DRIFTED)
    blocking = [verdict for verdict in verdicts if verdict.blocking]
    lines.append(
        f"{len(verdicts)} sources; {raw_drift} with raw-byte drift "
        f"(never blocking); {len(blocking)} blocking."
    )
    lines.append(
        "\nRaw-byte drift is expected and report-only: an EUR-Lex response carries a\n"
        "per-request analytics id, so its bytes differ between two fetches seconds\n"
        "apart while the text is identical. The content checksum is taken over\n"
        "extracted segment text and is what blocks. See docs/adr/0003-drift-policy.md."
    )
    if not GATE_ENABLED:
        lines.append("\nGATE DISABLED — this command reports and always exits 0.")
    return "\n".join(lines)


def run_verify(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    observations = load_manifest(args.data_root / MANIFEST_FILENAME)
    content = observed_content_checksums(registry.sources, observations, args.data_root)
    verdicts = verify(registry.sources, observations, load_pins(args.pins), content)

    print(format_report(verdicts))
    # Returns 0 regardless of drift while GATE_ENABLED is false.
    return exit_code_for(verdicts)


def main(argv: Sequence[str] | None = None) -> int:
    # `.env` is read before anything else so that a key put where the README
    # says to put it actually works. The real environment still wins.
    apply_dotenv()
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
