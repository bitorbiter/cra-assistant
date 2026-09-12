# ADR-0003: Record source drift, do not fail on it; escalate by trust tier

- Status: accepted; the deferral in it was lifted the same day
- Date: 2026-09-12

> **Update, 2026-09-12.** This ADR shipped the drift gate disabled because no
> checksum then existed that was stable against page furniture. Segmentation
> ([ADR-0004](0004-structure-based-segmentation.md)) produced one, and
> `GATE_ENABLED` is now `True`: **content** drift on a trusted source blocks.
> Raw-byte drift remains report-only forever, exactly as argued below. Read
> "verify is report-only, always exits 0" in the Decision section as the state
> at the time of writing, not the state of the code.

## Context

Sources change. EUR-Lex republishes pages, a community FAQ takes a pull request,
a GitHub issue list changes with every comment. Two questions follow: what should
happen when the bytes behind a source differ from last time, and who needs to
know.

They are different questions because the corpus has two tiers
([ADR-0001](0001-two-tier-trust-model.md)). A trusted document changing matters:
it is allowed to influence the model's behaviour, and if its wording moved we
are answering from text nobody approved. An untrusted forum thread changing is
not an event at all — it is what forum threads do.

There is a measurement problem underneath this, and we did not appreciate its
size until we measured it. A checksum over raw HTTP response bytes covers the
whole page, not the part we care about. Two fetches of the English CRA text,
**seconds apart**, produced different digests:

```
712,856 bytes  sha256:bb1249aac7ec…
712,855 bytes  sha256:bf8254e31435…
```

The entire difference is inside one `data-dtconfig` attribute belonging to a
Dynatrace analytics script, which embeds a per-request `agentId` and `rpid` in
the markup. Not one character of the legal text differs. A raw-byte checksum on
this source is therefore close to a random number per fetch.

## Decision

Three separate things.

**Fetch never fails on a changed checksum.** It stores the bytes
content-addressed at `data/raw/<source_id>/<sha256[:12]>.<ext>`, never
overwrites, and appends an observation to the manifest. It has no opinion about
whether the content should have changed.

**Verification is a separate command.** `cra-assistant verify` compares the
newest observation per source against a committed pin file,
`registry/pins.toml`, and reports the difference. Drift detection lives here and
nowhere else.

**Escalation follows the trust tier.** Drift on a `trusted` source requires human
acknowledgement: read it, then update the pin and write a one-line note saying
what you concluded. The `note` field is required, so the pin file cannot record
an approval with no reasoning behind it. Drift on an `untrusted` source is
recorded and nothing more.

**And the gate ships disabled.** `verify` is report-only, always exits 0, and
does not run in CI. It arms when pins are taken over parser-extracted text rather
than raw bytes, which arrives with the segmentation step.

## Rationale

Separating fetch from verify is about failure modes. If a changed checksum
aborted the fetch, then the ordinary event of an upstream edit would stop the
corpus being downloaded at all — and the moment a document changes is exactly
when you most want a copy of both versions to compare. Fetch's job is to make
the evidence exist. Judging it is a different job with a different audience.

Content-addressing follows from the same reasoning. If the store were keyed by
source id, a re-fetch would overwrite the previous bytes and the diff that
explains the drift would be gone at the moment it became interesting. Keyed by
digest, the old version simply stays, unchanged fetches are free, and no write
ever destroys anything.

Tier-dependent escalation is what keeps the alarm meaningful. Most drift in this
corpus is untrusted drift and carries no information. Treating it as an incident
would bury the one case that matters — the regulation's own text moving — under
noise from three forum pages.

Shipping the gate disabled is the part that deserves the most scrutiny, because
it looks like unfinished work. The argument is that a gate which fires on nearly
every run is worse than no gate: people learn the override, and after a fortnight
nobody reads the output. We have measured that this gate would fire on nearly
every run, for reasons that have nothing to do with the regulation. So the
detection, the report and the tier policy are built and tested now, and the only
thing deferred is the exit code — one constant, `GATE_ENABLED`, with a test
asserting it is off and naming the condition that turns it on. Building the alarm
and leaving it unarmed is honest; arming an alarm we know to be miscalibrated
would be theatre.

## Consequences

- The corpus can always be fetched. No upstream edit can block it.
- Every version ever fetched is kept, so any drift can be explained after the
  fact by diffing two stored files.
- `data/` grows monotonically. Nothing prunes it. With five sources and one
  EUR-Lex refetch already producing a duplicate, this is fine; it will need a
  retention policy long before it needs a bigger disk.
- The pin file is a second committed artefact that has to be maintained, and
  `verify` currently reports every EUR-Lex refetch as drift on a trusted source.
  Anyone running it will see noise and must read the caveat in `pins.toml` to
  understand why. That is the cost of shipping the honest version of this tool
  rather than a quiet one.
- Nothing enforces that a trusted source's drift is ever acknowledged. The pin
  file could stay stale indefinitely and no test would complain. Acknowledgement
  is a social commitment right now, not a mechanical one.
- Because the digest is over raw bytes, it cannot answer the question anyone
  actually wants answered: *did the legal text change?* Until step 3, drift on
  `cra-eurlex-*` means "the page was served again", nothing more.

## Rejected alternatives

### Fail the fetch when a checksum changes

Treat a changed digest as a corruption or tampering signal and abort.

Rejected because it inverts the desired behaviour at the worst moment. An
upstream amendment is a legitimate, expected event, and the response to it must
be "fetch it and tell someone", not "refuse to fetch". It would also make the
pin file a blocker on routine work: with EUR-Lex drifting per request, the fetch
would fail every time, and the pin would be updated reflexively to make the tool
run — which is a worse outcome than no check at all, because the pin would then
carry a note nobody meant.

### One checksum policy for all sources, ignoring tier

Report all drift identically.

Rejected because the signal-to-noise ratio makes it useless. Three of five
sources are rendered GitHub pages that change continuously; the two that matter
change a few times a decade. Reporting them the same way means the report is
mostly noise, and a report that is mostly noise does not get read. The tier is
already the project's answer to "how much does this source's content matter",
so reusing it here costs nothing.

### Normalise the HTML before checksumming, now

Strip scripts and analytics attributes, then digest what remains, so that a
stable checksum is available in this step.

Tempting, and rejected on scope rather than on merit. Stripping markup to find
the stable part is parsing, and parsing is step 3 with its own decisions to
make about the EUR-Lex document structure. A quick regex over `data-dtconfig`
would work today and would then quietly become the thing the real parser has to
stay compatible with. Better to have one extraction path, arriving once, with
the drift gate built on top of it.

### Verify in CI now, as a warning that does not fail

Run it on every push and let people read the log.

Rejected because a check that cannot fail and is not read is indistinguishable
from a check that does not exist, while still costing CI minutes and implying
coverage we do not have. It also cannot work as intended: CI would have to fetch
the sources on every run to have anything to verify, which means hitting EUR-Lex
on every push — impolite, slow, and a network dependency in a pipeline that
currently has none.
