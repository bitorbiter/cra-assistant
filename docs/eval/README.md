# Evaluation baselines

One file per measurement, named `baseline-<date>.md`.

**These files are append-only. Never edit one.** A baseline is a record of what
the system did on a given day, and editing it destroys the only thing it is for:
being comparable with the next one. If a baseline is wrong, take a new one and
say so in the journal.

**The one exception is an annotation, never a change to what was measured.** On
2026-09-13 every report measured on the truncated `orcwg-cra-hub-issues`
collection received a one-line corpus note under its title, marked with the date
it was added. The measured content below each note is untouched.
`baseline-2026-09-12.md` predates that collection — it was measured on the broken
HTML page ADR-0009 replaced — and carries no note.

Each file is generated, not written by hand, so any of them can be reproduced:

```sh
uv run cra-assistant eval --include-unverified --out docs/eval/baseline-<date>.md
```

Interpretation lives in `docs/journal.md` and in the ADRs, not here. The
baseline reports what happened; the journal argues about what it means.

Drop `--include-unverified` once the golden set has been checked by hand. Until
then the report carries a header saying its numbers are provisional, and the
default invocation refuses to score at all.
