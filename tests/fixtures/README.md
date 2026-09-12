# Test fixtures

`cra_excerpt_en.html` and `cra_excerpt_de.html` are excerpts of Regulation (EU)
2024/2847 as published by EUR-Lex (CELEX 32024R2847), cut down to recitals 1–3,
Articles 1–2, Annexes I–II, the signature block and the Official Journal footer.

They were produced by slicing the real fetched document, and the markup is
**unmodified**. That matters: a hand-written imitation of EUR-Lex markup would
drift from the real thing and the tests would keep passing while the parser
stopped working. Slices of the genuine document cannot.

The signature block, a footnote line and the OJ footer are included on purpose —
they are the parts that get wrongly swept into the last article and the last
annex when boundary detection is missing.

© European Union; reuse authorised under Decision 2011/833/EU.
