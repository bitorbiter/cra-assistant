# Build journal

Dated entries, newest last. Records what was built, what surprised us and what
broke — including the dead ends, because a blog post gets written from this.

## 2026-09-12 — Bootstrapping

**Built.** An empty but complete skeleton: `pyproject.toml` (uv, hatchling,
ruff, pytest), `src/cra_assistant` with nothing in it but a version string, a
smoke test that asserts the package imports, `.gitignore`, `.env.example`, a
GitHub Actions workflow running `ruff check` / `ruff format --check` / `pytest`,
the MADR ADR template, README with the trust-tier table and roadmap, MIT
licence, and `CLAUDE.md` so the next session starts with the brief instead of a
reconstruction of it.

**Decisions worth naming, none of which earned an ADR.**

- The version lives in `src/cra_assistant/__init__.py` and hatchling reads it
  from there. One source of truth beats keeping `pyproject.toml` and the module
  in sync by hand.
- `dependencies = []`. pydantic v2 is in the agreed stack, but nothing models
  anything yet, and an unused dependency in a portfolio repo is a small lie
  about what the code needs. It arrives with the first model, in step 2.
- Dev tooling sits in a PEP 735 `dev` group rather than an optional extra, so
  plain `uv sync` produces a working environment with no flags to remember.
- CI runs `uv sync --locked`, not `uv sync`. A lockfile that has drifted from
  `pyproject.toml` should fail the build loudly rather than resolve something
  else quietly. The cost: every dependency change needs the lockfile committed
  alongside it, and a forgotten `uv lock` shows up as a red CI run rather than
  as a helpful local warning.
- ruff rule set is `E, F, I, UP, B, SIM, RUF`. `I` replaces isort outright,
  which is the whole reason ruff is in the stack.

**Surprised us.** `uv` was not installed on the machine at all, which is an odd
thing to discover in the session where uv is the already-decided answer. Two
Pythons were in play: the system had 3.14.7, the project pins 3.12. Installed
uv with `pip3 install --user uv` (consistent with the other tools already in
`~/.local/bin`) rather than the curl-pipe-sh installer; uv then downloaded
CPython 3.12.14 for itself, so the system interpreter never entered the
project's environment. Committing a `.python-version` file means CI and a
stranger's laptop resolve the same interpreter without anyone installing
Python 3.12 by hand.

**Broke.** Nothing. Worth writing down that `ruff format --check .` reports
"5 files already formatted" while `ruff check . --show-files` lists three
paths — the format counter is not a file list, and `.venv` is genuinely
excluded. Ten minutes went into confirming CI would not be formatting the
virtualenv.

**Deliberately absent.** No downloading, no parsing, no retrieval, no database,
no OpenAI calls, no MCP, no Docker, and no placeholder modules for any of them.
The temptation to stub out `corpus/` "so the layout is visible" was real and was
refused; an empty directory documents nothing that the roadmap does not.

**Next.** Step 2: the corpus. Source registry with trust tiers, download with
checksums, structure-based segmentation into articles, recitals and annexes,
and validation.
