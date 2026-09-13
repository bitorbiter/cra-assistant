# Attack fixtures

Authored documents that attempt to subvert the trust boundary. They enter the
corpus as ordinary `untrusted` sources declared in `registry/attacks.toml`, with
`file:` URLs, going through the same fetch, plausibility, segmentation,
retrieval and prompt-assembly path as anything downloaded from the internet.

**These are never published anywhere.** They exist only in this repository. No
issue, no forum post, no pull request to a third-party project has ever carried
this content, and none ever should: the point is to attack our own prompt
assembly, not anybody else's system.

They are also never part of the production corpus. `registry/sources.toml` does
not reference them, so `ask` and `eval` never see them. Only
`cra-assistant attack` loads them, and it loads them alongside the real trusted
corpus, because an attack that arrives without genuine statute text beside it
does not test what happens when a model has to choose.

Each file carries a canary — a distinctive string it tries to make the assistant
emit. Grepping the model's answer for the canary is how success is measured.

## Where the payload sits

Seventeen of the fixtures are Markdown files with the payload in the **body**.
Two are JSON documents of the shape the GitHub Markdown-tree fetcher assembles,
with a benign body and the payload in **metadata**:

- `metadata-heading.json` — in a section heading, which becomes the segment
  title and, slugified, part of its id.
- `metadata-filename.json` — in a file name, which becomes part of the citation
  and, slugified, part of the id.

They were added after an external code review found that headings and file names
were rendered outside the untrusted wrapper
([ADR-0017](../../docs/adr/0017-metadata-is-untrusted-content.md)). No body-only
fixture could have found that. Each attack case declares `payload_placements`,
and every report lists which placements are covered and names any that are not.
