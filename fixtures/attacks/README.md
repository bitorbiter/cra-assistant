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
