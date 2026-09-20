"""Finding segments that might answer a question.

The :class:`Retriever` protocol is the part meant to survive. The BM25
implementation behind it is explicitly disposable (ADR-0006): it exists to find
out what retrieval and generation actually do before committing to pgvector,
and everything downstream depends on the protocol rather than on this class.

BM25 is implemented here rather than pulled in as a dependency. It is forty
lines of arithmetic, and taking a dependency on something we intend to delete
would be the wrong trade.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Protocol

from cra_assistant.models import Segment
from cra_assistant.passages import Passage, split_all

TOKEN = re.compile(r"\w+", re.UNICODE)

TOP_PASSAGES_PER_SEGMENT = 2
"""How many passages of one segment may be delivered, once it has been ranked.

A delivery cap, not a scoring rule: a segment ranks on its single best passage,
and this only decides how much of it the model then gets to read. Without the
cap one article's paragraphs fill the whole window and k stops meaning "how many
sources the model gets to see"."""


def tokenise(text: str) -> list[str]:
    """Casefolded word tokens.

    ``casefold`` rather than ``lower`` because the corpus is German too, and
    ``STRAẞE`` must match ``straße``. There is no stemming and no compound
    splitting, which is a real weakness for German — ``Herstellerpflichten``
    will not match ``Hersteller``. Noted rather than fixed: fixing it properly
    means a language-aware analyser, and this index is going to be thrown away.
    """
    return [token for token in TOKEN.findall(text.casefold()) if len(token) > 1]


class Retriever(Protocol):
    """What the rest of the system is allowed to assume about retrieval."""

    def retrieve(self, query: str, k: int) -> list[Passage]:
        """The ``k`` passages most likely to bear on ``query``, best first.

        A passage names the segment it came from, so a caller that only wants to
        know *which article* answered reads ``.id`` exactly as before (ADR-0018).
        """
        ...


class Bm25Retriever:
    """Okapi BM25 over segment text, built in memory at startup.

    Deliberately the dumbest thing that could work. It matches words, so a
    question asked in the regulation's own vocabulary does well and a question
    asked in ordinary language does badly — which is exactly the sort of thing
    this step exists to find out.
    """

    def __init__(
        self,
        segments: Iterable[Segment],
        *,
        k1: float = 1.5,
        b: float = 0.75,
        index_title: bool = True,
    ) -> None:
        self.segments: list[Segment] = list(segments)
        self.passages: list[Passage] = split_all(self.segments)
        self.k1 = k1
        self.b = b
        self.index_title = index_title

        # Passages, not segments: a 15,000-character article competed as one
        # document and BM25's length normalisation buried it (ADR-0018). The
        # title is indexed with every passage of its segment, which is what makes
        # a question phrased as an article's title find that article's paragraphs.
        self._documents = [
            tokenise(f"{passage.title} {passage.text}" if index_title else passage.text)
            for passage in self.passages
        ]
        self._lengths = [len(document) for document in self._documents]
        self._average_length = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        self._frequencies = [Counter(document) for document in self._documents]

        document_count = len(self._documents)
        containing: Counter[str] = Counter()
        for frequencies in self._frequencies:
            containing.update(frequencies.keys())
        self._idf = {
            term: math.log(1 + (document_count - count + 0.5) / (count + 0.5))
            for term, count in containing.items()
        }

    def __len__(self) -> int:
        return len(self.passages)

    def score(self, query_terms: Sequence[str], index: int) -> float:
        frequencies = self._frequencies[index]
        length = self._lengths[index] or 1
        total = 0.0
        for term in query_terms:
            frequency = frequencies.get(term)
            if not frequency:
                continue
            denominator = frequency + self.k1 * (
                1 - self.b + self.b * length / (self._average_length or 1)
            )
            total += self._idf.get(term, 0.0) * frequency * (self.k1 + 1) / denominator
        return total

    def retrieve(self, query: str, k: int) -> list[Passage]:
        if k < 1:
            raise ValueError(f"retrieval depth must be at least 1, got {k}")
        query_terms = tokenise(query)
        if not query_terms or not self.passages:
            return []

        scored = [(self.score(query_terms, index), index) for index in range(len(self.passages))]
        # Ties break on document order, so the same question gives the same
        # answer twice — a prerequisite for the evaluation step being meaningful.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        hits = [(score, index) for score, index in scored if score > 0]

        # A segment is worth its single best passage. Summing its best two was
        # tried and reverted: a segment with two matching passages scored up to
        # twice one with a single passage, and how many passages a segment has
        # is a fact about its length, not its relevance. That handed long
        # statute articles a bonus over single-paragraph community posts and
        # short articles — the length advantage passages exist to remove
        # (ADR-0018) — and it cost two of the five tier-collapse controls and
        # Article 71. Passages come out best segment first, and within a segment
        # best passage first.
        per_segment: dict[str, float] = {}
        for score, index in hits:
            segment_id = self.passages[index].id
            per_segment[segment_id] = max(per_segment.get(segment_id, 0.0), score)
        segment_score = per_segment
        order = {
            segment_id: rank
            for rank, segment_id in enumerate(
                sorted(segment_score, key=lambda one: (-segment_score[one], one))
            )
        }
        hits.sort(key=lambda pair: (order[self.passages[pair[1]].id], -pair[0], pair[1]))

        # At most TOP_PASSAGES_PER_SEGMENT passages of any one segment. Without
        # the cap one article's paragraphs fill the whole window, and k stops
        # meaning "how many sources the model gets to see".
        emitted: Counter[str] = Counter()
        out: list[Passage] = []
        for _score, index in hits:
            passage = self.passages[index]
            if emitted[passage.id] >= TOP_PASSAGES_PER_SEGMENT:
                continue
            emitted[passage.id] += 1
            out.append(passage)
            if len(out) == k:
                break
        return out
