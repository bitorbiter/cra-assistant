# ADR-0011: Measure the defence before adding a second one

- Status: accepted
- Date: 2026-09-12

## Context

The trust boundary is enforced in exactly one place: `prompt.render_segment` and
the assembly around it. Untrusted segments are wrapped in delimiters they cannot
close, labelled as data, and the system prompt states that nothing inside them
is an instruction. Ranking is deliberately tier-blind
([ADR-0008](0008-tier-blind-ranking.md)), so nothing upstream thins the traffic
reaching that one place.

Until now nothing had attacked it. This step built thirteen authored fixtures —
delimiter escapes, plain instruction injection, authority mimicry, citation
misattribution, and benign controls — and measured what fraction succeed.

The results make the temptation concrete. Authority mimicry succeeded on the one
case that reached the prompt: a fabricated "Article 74" with a turnover
exemption was adopted and repeated. A literal delimiter escape succeeded. Both
have obvious mitigations, and both could be added in an afternoon: strip
statute-shaped formatting from untrusted segments, or refuse answers citing
article numbers outside the known range.

The question this ADR settles is whether to add them now.

## Decision

**No mitigation is added in the step that first measures the attack surface.**

The attack set is a measurement instrument, run by `cra-assistant attack`, with
results written to a dated, append-only report under `docs/eval/`. The next step
may add a mitigation; it must add one at a time, with a report either side of it.

An **invariant assertion** is not a mitigation and was added: `build_messages`
raises if the assembled prompt does not contain exactly one delimiter pair per
untrusted segment. It repairs nothing and filters nothing. It fails loudly if
what we assert about the prompt stops being true of the prompt.

## Rationale

A defence chosen before the attack surface is measured is a guess, and a second
defence added before the first is measured destroys the ability to measure
either. If delimiter neutralisation and a statute-format stripper are both in
place when the attack set runs, a 0% success rate says only that *something*
worked. Removing one to find out is an experiment nobody runs on a system that
currently looks safe.

The first run already justifies the ordering, because it produced a result no
amount of reasoning would have predicted:

**Every delimiter escape was structurally neutralised, and one succeeded
anyway.** The invariant held on every case — eight open and eight close markers
for eight untrusted segments, the literal `</untrusted-content>` replaced with
`[delimiter removed]`. The attack still worked, because the fixture followed its
escape attempt with prose asserting that the quoted region had ended and that
operator instructions followed. The model believed the sentence, not the markup.
It was inside an intact box and behaved as though it were outside one.

That reframes the whole defence. Escaping delimiters answers a *syntactic*
attack. The attack that got through was *semantic*, and no amount of better
escaping addresses it. Had a second mitigation been in place, the natural
reading would have been "delimiters need hardening" — the opposite of what the
evidence says.

The measurement also caught itself being wrong twice, which is the argument for
building the instrument carefully rather than quickly. The first version matched
retrieved attack segments by id prefix, but segment ids begin with the citation
prefix, not the source id, so every attack was recorded as never retrieved: a
defence credited with stopping everything because the meter read zero. And the
control row turns out not to measure what it was designed to measure — a control
detects over-defensiveness, and there is nothing in the system yet capable of
being over-defensive, so both control failures trace to retrieval rather than to
a refusal. Both are recorded in the report rather than quietly corrected.

**Detection in depth before defence in depth.** Layers of mitigation make a
system feel safer and make it unmeasurable. Layers of *detection* — an attack
set per class, a success rate over attacks that actually arrived, an invariant
that fails loudly, and a report that says which mechanism did the blocking —
make it legible. Mitigations can then be added one at a time against a baseline
that shows whether each one helped.

## Consequences

- There is a number for each attack class, and the next mitigation can be judged
  against it rather than asserted to work.
- **The system is currently known to be vulnerable, in public.** Authority
  mimicry succeeded; a delimiter escape succeeded by persuasion. That is written
  in the README and in a committed report. Publishing a measured weakness is
  better than publishing an unmeasured claim of safety, but it is a real cost
  and it was chosen deliberately.
- The attack set costs one model call per case, so running it is not free and it
  cannot go in the per-push CI job.
- Attack fixtures are committed and never published anywhere. They enter through
  the ordinary untrusted path via `file:` sources — the seam
  [ADR-0009](0009-untrusted-content-from-apis.md) promised and this step built —
  so nothing about the measurement depends on a test-only shortcut.
- The fixtures are authored by the same person building the defence, which is a
  known weakness of the whole exercise. They cover the attack classes we thought
  of. An attack class nobody imagined has a success rate of zero in this report
  and is not measured at all.
- Every future mitigation now carries an obligation: a report before, a report
  after, and a statement of which class it moved.

## Rejected alternatives

### Add the obvious mitigations now and measure afterwards

Strip statute formatting from untrusted segments, reject citations to
non-existent article numbers, and then run the attack set.

Rejected because the resulting numbers would be uninterpretable. With two
mechanisms in place and no before-measurement, a blocked attack cannot be
attributed, and the finding that delimiters were neutralised while the attack
succeeded anyway would have been invisible. It would also have produced a
better-looking first report, which is precisely the wrong incentive to build in.

### Add defence in depth on principle, without measuring at all

Layer mitigations because layering is good practice, and rely on the principle
rather than on evidence.

Rejected because it is the reasoning that produced five green checks over a
navigation menu. Defence in depth is sound when each layer is known to work;
unmeasured layers are not defence in depth but a stack of assumptions with no
way to tell which one is load-bearing. This project's whole subject is the
difference.

### Suppress untrusted content in ranking so attacks rarely arrive

Downrank the untrusted tier and let most attacks fail to be retrieved.

Rejected in [ADR-0008](0008-tier-blind-ranking.md) and confirmed here. Twelve of
thirteen fixtures reached the prompt, most at rank 1, and that is what made the
measurement possible. A ranking penalty would have converted the report into a
statement about retrieval rather than about the boundary — and the one case that
did not arrive, `auth-notice`, is recorded as inconclusive rather than blocked
for exactly this reason.

### Keep the attack fixtures private

Withhold the fixture documents so the attacks are not public.

Rejected. They are authored, generic, and describe techniques already documented
in the literature; withholding them would remove the reader's ability to check
the report, which is the only thing that makes it worth publishing. They are
never posted anywhere outside this repository, which is a different question and
a firm rule.
