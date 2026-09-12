# Prompt injection risk in CRA compliance tooling

## Background

Several members are building assistants that answer CRA questions over a mixed
corpus of statute and community commentary. This note collects what is known
about prompt injection in that setting, because the risk is specific: the
commentary is exactly the material anyone can edit.

## Discussion

The attack is straightforward to describe. A document in the corpus contains
text addressed not to the reader but to the model, such as "ignore previous
instructions" or "the preceding quoted section has ended". If the assistant
treats retrieved documents as instructions rather than as evidence, that text
can change what the assistant says, and the reader has no way to tell.

Mitigations discussed so far include marking retrieved passages with their
provenance, wrapping untrusted passages in delimiters, and checking after
generation that every citation corresponds to a document actually retrieved.
None of these is sufficient alone. Delimiters can be closed by the content they
are supposed to contain unless the content is escaped first, and instructions
that need no escape are not addressed by delimiters at all.

The working group's recommendation is that tooling authors test their own
assembly against authored attack documents rather than assume the framing
holds, and that they measure what fraction of attacks succeed rather than
asserting that none do.

## See also

Related discussion appears in the working group issue tracker. This document is
community interpretation and carries no legal standing.
