# ADR-0002: Keep the source registry as committed data, not as Python

- Status: accepted
- Date: 2026-09-12

## Context

Something has to list the sources the corpus is built from, and give each one a
trust tier. [ADR-0001](0001-two-tier-trust-model.md) makes that tier the
security boundary of the system: a source marked `trusted` may influence the
model's behaviour, one marked `untrusted` may not.

That makes the list itself a security-relevant artefact. The question is what
form it takes — a Python module holding `Source(...)` constructor calls, or a
data file loaded and validated at runtime.

The audience matters. This is a public project, so the list will be read by
people who have not read the code, and the most important review question about
any change to it is a simple one: *does this pull request mark something as
trusted, and should it?*

## Decision

We will keep the registry in a committed TOML file, `registry/sources.toml`,
loaded through `cra_assistant.registry.load_registry` and validated by pydantic
against the models in `cra_assistant.models`.

The file is data. It contains no expressions, no imports and nothing conditional.
Validation is strict: unknown tiers, unknown parsers, duplicate ids and unexpected
keys are errors, never defaults.

## Rationale

A TOML entry has one possible reading. `tier = "trusted"` in a diff means that
source is trusted, and there is no enclosing loop, helper function or default
argument that could make it mean something else. A reviewer who does not know
Python can still review the security-relevant part of the change, and a reviewer
who does know Python does not have to read the surrounding module to be sure.

Stdlib `tomllib` reads it, so the choice costs no dependency. Pydantic is doing
the work that matters — turning a permissive file format into strictly typed
objects, at one place, with errors that name the offending entry and field.

TOML over JSON because it takes comments, and the file needs them: the trust
semantics are explained at the top of the registry, where someone editing it
will actually see them. TOML over YAML because YAML's implicit typing is a poor
fit for a file whose entire job is unambiguous classification.

Splitting validation out of the file format is what makes the "just data"
property safe. A data file with no schema is not simpler than code, only later
to fail. Here the failure happens at load, with a message naming the entry.

## Consequences

- "Source added, marked trusted" is visible in a pull request diff without
  reading code, which is the review property [ADR-0001](0001-two-tier-trust-model.md)
  depends on.
- The registry can be validated in CI without importing the retrieval stack, and
  malformed entries fail at load rather than at query time.
- Non-Python contributors can propose sources.
- Anything expressible only in code — a source list generated from an API, per
  environment variation, hundreds of near-identical entries — becomes awkward.
  We accept that; if it is ever needed, generating the TOML in a separate step
  keeps the reviewable artefact intact.
- The file will get repetitive as it grows. TOML has no way to factor out shared
  fields, so common values such as the EUR-Lex licence string are repeated per
  entry. This is verbosity in exchange for each entry being complete and
  readable on its own.
- Two artefacts must stay in step: adding a field to `Source` means editing every
  registry entry. The strict schema turns that into a loud test failure rather
  than a silent `None`.
- The default path is resolved relative to the source tree, so it does not
  survive installation as a wheel. Deferred deliberately: making it configuration
  before anything needs it would be speculation.

## Rejected alternatives

### A Python module of `Source(...)` literals

Declare the sources in `sources.py` as a list of model instances.

Rejected on reviewability. It reads well and gets type checking for free, but it
puts the trust boundary inside a file where a reader must reason about Python to
be certain what a line does. The failure is not that someone hides a
`tier=TRUSTED` behind a helper deliberately; it is that a well-meaning refactor
introduces `_eurlex(lang)` returning a trusted source, and afterwards the diff
that adds a trusted source no longer contains the word "trusted". The property we
want is that it always does.

### A database table as the source of truth

Keep sources in Postgres, which the project will run anyway from the indexing
step onward.

Rejected because it removes the change from version control. There would be no
diff, no review, and no way to answer "when did this become trusted, and who
approved it?" without an audit log we would then have to build. A row is also
editable by anything holding the connection string, which is a much wider set of
actors than "people who can merge a pull request". The registry may well be
mirrored into the database for query convenience later; committed data stays the
source of truth.

### Environment variables or runtime configuration

Supply the source list as deployment configuration.

Rejected outright: it would make the trust boundary a property of a deployment
rather than of the reviewed codebase, and the most security-relevant fact about
the system would live somewhere with no history.

### JSON instead of TOML

Rejected only because JSON has no comments. The registry's header explains what
`trusted` means and why adding one is a reviewed change; that text belongs in
the file being edited, not in a separate document nobody opens.
