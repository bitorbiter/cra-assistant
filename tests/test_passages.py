"""Splitting a provision into passages without losing what governs it.

The nested provision used here is Annex I of the CRA as the corpus stores it,
markers on their own lines and all. It is the shape that broke: lettered
sub-points became independent passages and the introduction imposing the
condition on them stayed behind.
"""

from cra_assistant.models import Segment, SegmentKind, TrustTier
from cra_assistant.passages import Passage, split, whole
from cra_assistant.prompt import deliver

# Annex I, Part I point (2) and Part II, abridged to four sub-points but
# otherwise verbatim, including the markers-on-their-own-line layout.
ANNEX_I = """ESSENTIAL CYBERSECURITY REQUIREMENTS
Part I Cybersecurity requirements relating to the properties of products with digital elements
(1)
Products with digital elements shall be designed, developed and produced in such a way that they ensure an appropriate level of cybersecurity based on the risks.
(2)
On the basis of the cybersecurity risk assessment referred to in Article 13(2) and where applicable, products with digital elements shall:
(a)
be made available on the market without known exploitable vulnerabilities;
(e)
protect the confidentiality of stored, transmitted or otherwise processed data, personal or other, such as by encrypting relevant data at rest or in transit by state of the art mechanisms, and by using other technical means;
(f)
protect the integrity of stored, transmitted or otherwise processed data, personal or other, commands, programs and configuration against any manipulation or modification not authorised by the user;
Part II Vulnerability handling requirements
Manufacturers of products with digital elements shall:
(1)
identify and document vulnerabilities and components contained in products with digital elements, including by drawing up a software bill of materials in a commonly used and machine-readable format covering at the very least the top-level dependencies of the products;
(2)
in relation to the risks posed to products with digital elements, address and remediate vulnerabilities without delay, including by providing security updates.
"""


def annex() -> Segment:
    return Segment(
        id="cra-en:annex:I",
        source_id="cra-en",
        tier=TrustTier.TRUSTED,
        kind=SegmentKind.ANNEX,
        number="I",
        title="Essential cybersecurity requirements",
        text=ANNEX_I,
        citation="Regulation (EU) 2024/2847, Annex I",
        source_sha256="sha256:" + "a" * 64,
        content_sha256="sha256:" + "b" * 64,
        lang="en",
        order=0,
    )


def passage_containing(needle: str) -> Passage:
    found = [one for one in split(annex()) if needle in one.text]
    assert len(found) == 1, f"expected exactly one passage containing {needle!r}, got {len(found)}"
    return found[0]


def test_a_lettered_subpoint_carries_the_introduction_that_conditions_it() -> None:
    """The defect: "(e) protect the confidentiality of stored ... data" read as an
    unconditional requirement, because the introduction imposing the risk
    assessment and "where applicable" stayed in another passage."""
    point = passage_containing("confidentiality of stored")

    assert "On the basis of the cybersecurity risk assessment" in point.delivered_text
    assert "where applicable" in point.delivered_text
    assert "Part I Cybersecurity requirements" in point.delivered_text, "the part heading too"
    assert "(e)" in point.delivered_text


def test_the_introduction_is_not_indexed_only_delivered() -> None:
    """Thirteen sub-points sharing an introduction would become thirteen
    near-identical documents, and a query matching the introduction would
    retrieve all of them ahead of anything else."""
    point = passage_containing("confidentiality of stored")

    assert "risk assessment" not in point.text
    assert point.context and point.context not in point.text


def test_a_point_carries_its_own_part_and_not_the_other_one() -> None:
    """Gluing Part II's requirements under Part I's heading would attribute a
    vulnerability-handling duty to the product-properties part."""
    handling = passage_containing("software bill of materials")

    assert "Part II Vulnerability handling requirements" in handling.delivered_text
    assert "Manufacturers of products with digital elements shall:" in handling.delivered_text
    assert "Part I Cybersecurity requirements" not in handling.delivered_text


def test_context_reaches_the_model_through_delivery() -> None:
    """Carried context is worth nothing if prompt assembly drops it."""
    point = passage_containing("confidentiality of stored")

    assert "On the basis of the cybersecurity risk assessment" in deliver(point).text


def test_a_numbered_paragraph_is_not_given_a_sibling_as_context() -> None:
    """Point (1) is not subordinate to anything but the part heading."""
    first = passage_containing("appropriate level of cybersecurity")

    assert "Part I Cybersecurity requirements" in first.delivered_text
    assert "confidentiality" not in first.delivered_text


def test_a_segment_with_no_markers_is_itself_and_carries_no_context() -> None:
    plain = Segment(
        id="orcwg-issues:section:issue-1-comment-2",
        source_id="orcwg-issues",
        tier=TrustTier.UNTRUSTED,
        kind=SegmentKind.SECTION,
        number="1",
        title="",
        text="A community comment that runs to a paragraph and has no markers at all.",
        citation="ORC WG, issue 1",
        source_sha256="sha256:" + "c" * 64,
        content_sha256="sha256:" + "d" * 64,
        lang="en",
        order=0,
    )

    only = split(plain)

    assert len(only) == 1
    assert only[0].context == ""
    assert only[0].delivered_text == plain.text
    assert only[0].whole_segment


def test_whole_wraps_a_segment_without_inventing_context() -> None:
    assert whole(annex()).context == ""
    assert whole(annex()).delivered_text == ANNEX_I
