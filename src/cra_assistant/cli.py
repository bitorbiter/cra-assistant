"""Command line entry points: ``fetch`` and ``verify``.

Two commands, deliberately separate. ``fetch`` records what upstream served;
``verify`` reports how that differs from what a human approved. Keeping them
apart is what lets a changed source still download (ADR-0003).

argparse rather than a CLI framework: two subcommands and a handful of flags do
not justify a dependency.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from cra_assistant import __version__
from cra_assistant.fetch import (
    DEFAULT_POLICY,
    MANIFEST_FILENAME,
    FetchPolicy,
    fetch_sources,
)
from cra_assistant.manifest import load_manifest
from cra_assistant.models import Source, TrustTier
from cra_assistant.paths import DEFAULT_DATA_ROOT, DEFAULT_PINS_PATH, DEFAULT_REGISTRY_PATH
from cra_assistant.registry import load_registry
from cra_assistant.verify import (
    GATE_ENABLED,
    DriftStatus,
    SourceVerdict,
    exit_code_for,
    load_pins,
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

    verify_command = subcommands.add_parser(
        "verify", help="report drift against the committed pins (report only)"
    )
    verify_command.add_argument("--pins", type=Path, default=DEFAULT_PINS_PATH)
    verify_command.set_defaults(handler=run_verify)

    return parser


def select_sources(registry_path: Path, source_ids: Sequence[str] | None) -> list[Source]:
    sources = list(load_registry(registry_path).sources)
    if not source_ids:
        return sources

    by_id = {source.id: source for source in sources}
    unknown = sorted(set(source_ids) - by_id.keys())
    if unknown:
        raise SystemExit(f"unknown source ids: {', '.join(unknown)}")
    return [by_id[source_id] for source_id in source_ids]


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


ACTION_BY_STATUS = {
    DriftStatus.DRIFTED: "acknowledge: update the pin and say why",
    DriftStatus.UNPINNED: "no pin yet: add one",
    DriftStatus.UNFETCHED: "not fetched",
}


def format_report(verdicts: Sequence[SourceVerdict]) -> str:
    lines = ["Drift report", ""]
    for tier in (TrustTier.TRUSTED, TrustTier.UNTRUSTED):
        in_tier = [verdict for verdict in verdicts if verdict.tier is tier]
        if not in_tier:
            continue
        lines.append(f"{tier} sources")
        for verdict in sorted(in_tier, key=lambda v: v.source_id):
            note = ""
            if verdict.needs_acknowledgement:
                note = ACTION_BY_STATUS.get(verdict.status, "")
            elif verdict.status is not DriftStatus.CLEAN:
                note = "recorded, no action needed"
            lines.append(f"  {verdict.source_id:<32} {verdict.status:<10} {note}".rstrip())
        lines.append("")

    drifted = sum(1 for verdict in verdicts if verdict.status is DriftStatus.DRIFTED)
    needing = sum(1 for verdict in verdicts if verdict.needs_acknowledgement)
    lines.append(f"{drifted} of {len(verdicts)} sources drifted; {needing} need acknowledgement.")

    if not GATE_ENABLED:
        lines.append(
            "\nGATE DISABLED — this command reports and always exits 0. A checksum over\n"
            "raw bytes drifts on nearly every EUR-Lex fetch because of the page shell,\n"
            "not the legal text. The gate arms once pins cover parser-extracted text.\n"
            "See docs/adr/0003-drift-policy.md."
        )
    return "\n".join(lines)


def run_verify(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    observations = load_manifest(args.data_root / MANIFEST_FILENAME)
    verdicts = verify(registry.sources, observations, load_pins(args.pins))

    print(format_report(verdicts))
    # Returns 0 regardless of drift while GATE_ENABLED is false.
    return exit_code_for(verdicts)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
