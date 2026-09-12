# ADR-0006: Build a walking skeleton with a disposable index before investing in retrieval

- Status: accepted
- Date: 2026-09-12

## Context

Everything so far produces a corpus: 488 segments across five sources, validated,
checksummed, with trust tiers attached. Nothing yet answers a question.

The roadmap's next items are all expensive and all assume things nobody has
measured: Postgres with pgvector and hybrid retrieval, authored poison fixtures,
an evaluation harness wired into CI. Each is a substantial piece of work, and
each is built on a guess about what the failure modes are.

The specific guesses worth naming: that retrieval quality is the bottleneck; that
segments are the right size; that a prompt can hold enough context; that
prompt-level trust framing is straightforward to express. Any of those could be
wrong, and being wrong about them after building pgvector is more expensive than
being wrong now.

## Decision

We will build the thinnest end-to-end path — question in, cited answer out — and
treat the implementation as disposable.

- **Retrieval is in-memory BM25**, rebuilt on every invocation. No database, no
  embeddings, no persistence, no ANN index. Roughly forty lines of arithmetic,
  written here rather than taken as a dependency, because taking a dependency on
  something we intend to delete is the wrong trade.
- **A `Retriever` protocol** — `retrieve(query, k) -> list[Segment]` — is what
  everything downstream depends on. The protocol is the part meant to survive.
- **Long segments are truncated**, with a TODO, not sub-split. The sub-split
  design should be informed by watching real retrieval, not guessed at first.
- **Prompt assembly is pure and unit-testable**, and is where the trust boundary
  becomes visible text rather than a field on a model.
- **Citations are enforced in code.** An answer citing nothing, or citing a
  segment that was not retrieved, is converted into an abstention.
- **Telemetry exists from the first call**, logging model, tokens, latency,
  estimated cost and a request id to JSONL, even though OpenTelemetry is later.

## Rationale

The interface survives and the implementation does not. That is the whole shape
of the decision, and it is why the `Retriever` protocol is defined before there
is more than one implementation: swapping BM25 for pgvector should be a new class
and a changed constructor call, not a refactor.

BM25 was chosen as the disposable option because it is honest about being dumb.
It matches words. When it fails, it fails legibly and you can see exactly why,
which is what makes it a good instrument for a step whose purpose is to find
things out. An embedding index would fail more gracefully and much less
informatively — and a graceful failure is the last thing you want when the goal
is to learn what breaks.

It worked as an instrument immediately. Asked *"Wer gilt als Hersteller im Sinne
der Verordnung?"*, the definition of "Hersteller" is Article 3, and BM25 ranks it
**16th** — outside any sensible `k`. The reasons are visible: BM25's length
normalisation penalises Article 3 for being 10,930 characters of definitions, and
five of the eight query tokens are German stopwords that match everything. Both
are fixable, neither is fixable *correctly* without a way to measure whether the
fix helped. That measurement is the evaluation step, and this finding is the
argument for building it before pgvector.

Enforcing citations in code rather than in the prompt follows the same logic as
the trust tier being materialised rather than looked up ([ADR-0001](0001-two-tier-trust-model.md)).
A prompt asking for citations is a request; dropping unretrieved citations and
converting an uncited answer into an abstention is a rule. A fabricated citation
is the worst output this system can produce — it is wrong in the specific way
that looks most right — so it is worth a hard check rather than a polite one.

Telemetry now, despite OTel being step 7, because the cost of the seam is a few
lines today and the cost of retrofitting it is threading a request id through
every call site later.

## What this step is allowed to be wrong about

Stated explicitly, so that later disappointment is not mistaken for regression:

- **Retrieval quality.** BM25 with no stemming, no stopword handling and no
  German compound splitting will miss things. It already does.
- **Segment size.** Truncation at 4,000 characters means Annex VIII's later
  parts cannot reach the model at all.
- **Answer quality.** The default model is chosen for being cheap.
- **Injection resistance.** The prompt-level framing is untested against real
  attacks, because the attacks do not exist yet.
- **Cost estimates.** The price table will go stale.
- **Scale.** Everything is rebuilt in memory per invocation. It is fine at 488
  segments and will not be at 50,000.

## What this step is *not* allowed to be wrong about

- The `Retriever` protocol's shape.
- That untrusted content is rendered as delimited, labelled data.
- That an answer without a valid retrieved citation is never shown.
- That no API key reaches a log, a repr, or an error message.
- That every model call is recorded.

## Consequences

- There is something to run, and therefore something to be wrong about
  concretely rather than in the abstract.
- The evaluation step now has a baseline to measure against, and a known
  deficiency (Article 3 at rank 16) to measure improvement on.
- The sub-split design can be based on observed segment lengths and observed
  truncation rather than on a guess.
- `openai` joins the dependencies; BM25 does not.
- **Some of this code will be deleted, and that is the plan.** The risk is the
  usual one: disposable code that works has a way of becoming permanent. The
  protocol boundary is the mitigation, and this ADR is the reminder.
- Answer quality is currently unmeasured, so no claim can be made about it.
  There is a difference between "it answered" and "it answered well", and only
  the first has been established.

## Rejected alternatives

### Go straight to Postgres with pgvector and hybrid retrieval

Build the real thing now: a database, an embedding model, hybrid dense/sparse
retrieval with reciprocal rank fusion.

Rejected because every parameter in it would be guessed. What to embed — the
whole segment, or a sub-split of it? What sub-split, given segments range from
148 to 22,000 characters? Which embedding model, in a bilingual corpus? How to
weight sparse against dense? None of those has a defensible answer today, and
choosing them without measurement produces a system that is complicated,
plausible and unevaluated — the worst of the three. The cost of this detour is a
few hundred lines that get deleted; the cost of the alternative is a design
committed to before the evidence for it exists.

### Build the evaluation harness first

Write the question set and the metrics, then build retrieval against them.

Genuinely tempting, and the closest call. Rejected because evaluating requires
something to evaluate, and — more importantly — writing a question set without
having watched the system fail produces a question set that tests what you
imagined would be hard. Thirty minutes with a working skeleton produced one
concrete, specific failure (a definition at rank 16 because of length
normalisation and stopwords) that is worth more than a speculatively authored
fixture. Evaluation is the *next* step, and it is now better informed.

### Skip retrieval and put the whole regulation in the context window

The CRA is roughly 700 KB of HTML, perhaps 200,000 tokens of text. Large-context
models exist.

Rejected on cost, latency and, decisively, on the point of the project. Citations
have to be *verifiable* and *attributable to a retrieved unit*; an answer drawn
from an undifferentiated 200,000-token blob cannot be traced to a segment, and
there would be no retrieval step in which to apply the trust boundary at all.
The two-tier design assumes untrusted content arrives as identifiable, labelled
units — which is exactly what retrieval produces and what a single giant context
destroys.
