# ADR-0010: Pin the model to a dated snapshot

- Status: accepted
- Date: 2026-09-12

## Context

Every input to this system is version-controlled or pinned. Source documents are
fetched, checksummed and recorded in a committed pin file that a human must edit
to acknowledge a change ([ADR-0003](0003-drift-policy.md)). Segment ids are
permanent names ([ADR-0005](0005-corrigenda-as-separate-sources.md)). Gold labels
are committed data reviewed in pull requests
([ADR-0007](0007-measure-before-tuning.md)). Baselines are append-only.

Except the model. `DEFAULT_MODEL` was `"gpt-4o-mini"`, a floating alias that the
provider repoints to a new snapshot whenever it chooses, without notice and
without a version we record.

So the largest and least predictable input to the system was the only one not
under any of the controls built for the others. A baseline could change because
the corpus changed — which the drift gate would catch and require someone to
acknowledge — or because the alias moved overnight, which nothing would catch at
all.

## Decision

`DEFAULT_MODEL` is a dated snapshot: `gpt-4o-mini-2024-07-18`. Never a floating
alias.

The model id is recorded in every telemetry record and printed in the header of
every evaluation report, so any number this project produces can be attributed
to the model that produced it.

`CRA_MODEL` still overrides, and may be set to an alias deliberately. What is
ruled out is an alias as the committed default.

## Rationale

This is the pins.toml argument applied to the largest uncontrolled input in the
system. The point of pinning has never been that upstream is untrustworthy; it
is that a change should be *visible and acknowledged* rather than silent. A
floating alias fails that test the same way an unpinned source would, and worse,
because a model change moves every generated answer at once.

Recording the id everywhere is the cheaper half and matters more. A baseline
without a model id is not reproducible: a reader cannot tell whether last
month's numbers and this month's were produced by the same system. Since the
evaluation now prints estimated cost per question, the id also names what the
price table was applied to — a cost figure with no model attached is a number
with no meaning.

The cost of pinning is that we do not get improvements for free, and a pinned
snapshot is eventually retired by the provider. Both are acceptable: an upgrade
should be a commit with a baseline either side of it, which is exactly what the
retirement will force.

## Consequences

- Any two baselines can be compared, because each names the model that produced
  it.
- Upgrading the model becomes a deliberate act with a measurable before and
  after, rather than something that happens overnight.
- The pinned snapshot will be retired by the provider eventually, and the
  project will stop working until someone changes the constant. That failure is
  loud and dated, which is preferable to a silent quality shift.
- The price table now carries entries for both the alias and the snapshot, and
  will go stale. `estimate_cost` returns `None` for an unknown model rather than
  guessing, so a stale entry is a wrong number and a missing one is an honest
  blank — worth remembering when adding models.

## Rejected alternatives

### Keep the floating alias

Take the provider's latest automatically.

Rejected because it is the one place in the project where we would accept an
unannounced change to an input. It also makes the evaluation incoherent: the
golden set exists to detect regressions, and a silent model swap is a regression
source the harness cannot see, attribute or reproduce.

### Pin, and also pin the temperature, seed and system prompt hash

Go further: record everything that could change an answer.

Rejected as premature rather than wrong. Temperature is already 0 and the prompt
is in version control, so both are recoverable from a commit. A seed would only
matter once generation is being scored, which is a later step; the honest place
for that decision is when there is a generation metric to protect.

### Record the model id only, without pinning

Attribution without control.

Rejected because attribution alone tells you a number changed and not why. Half
the value of pinning is that the id in the report is *stable*, so a differing
baseline points at something we did.
