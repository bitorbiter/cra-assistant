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

TOKEN = re.compile(r"\w+", re.UNICODE)


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

    def retrieve(self, query: str, k: int) -> list[Segment]:
        """The ``k`` segments most likely to bear on ``query``, best first."""
        ...


class Bm25Retriever:
    """Okapi BM25 over segment text, built in memory at startup.

    Deliberately the dumbest thing that could work. It matches words, so a
    question asked in the regulation's own vocabulary does well and a question
    asked in ordinary language does badly — which is exactly the sort of thing
    this step exists to find out.
    """

    def __init__(self, segments: Iterable[Segment], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.segments: list[Segment] = list(segments)
        self.k1 = k1
        self.b = b

        self._documents = [tokenise(f"{segment.title} {segment.text}") for segment in self.segments]
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
        return len(self.segments)

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

    def retrieve(self, query: str, k: int) -> list[Segment]:
        query_terms = tokenise(query)
        if not query_terms or not self.segments:
            return []

        scored = [(self.score(query_terms, index), index) for index in range(len(self.segments))]
        # Ties break on document order, so the same question gives the same
        # answer twice — a prerequisite for the evaluation step being meaningful.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [self.segments[index] for score, index in scored[:k] if score > 0]
