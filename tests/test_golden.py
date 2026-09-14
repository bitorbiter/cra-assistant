"""The golden set loader. Committed data, validated like the registry."""

import re
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from cra_assistant.golden import (
    DEFAULT_GOLDEN_PATH,
    AnswerType,
    GoldenSet,
    Vocabulary,
    load_golden_set,
)

VALID = """
    [[items]]
    id = "an-item"
    question = "What are the obligations of manufacturers?"
    lang = "en"
    vocabulary = "statute"
    answer_type = "answerable"
    expected_segment_ids = ["cra-en:article:13"]
    note = ""
    verified = false
"""


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "golden.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_the_committed_golden_set_validates() -> None:
    golden = load_golden_set(DEFAULT_GOLDEN_PATH)

    assert len(golden.items) >= 35


def test_the_committed_set_has_all_three_answer_types() -> None:
    types = {item.answer_type for item in load_golden_set().items}

    assert types == set(AnswerType)


def test_there_are_at_least_five_tier_collapse_control_items() -> None:
    """The only check for whether a mitigation stops the system using community
    sources (ADR-0012, ADR-0016). Deleted once with the old ids; not again."""
    control = [
        item for item in load_golden_set().items if item.answer_type is AnswerType.UNTRUSTED_ONLY
    ]

    assert len(control) >= 5
    assert all(
        one.startswith(("orcwg-", "ossf-", "ec-faq-"))
        for item in control
        for one in item.expected_segment_ids
    )


def test_untrusted_only_items_point_at_untrusted_sources() -> None:
    """An item claiming only an untrusted source answers it must not be labelled
    with a segment from the regulation."""
    for item in load_golden_set().items:
        if item.answer_type is AnswerType.UNTRUSTED_ONLY:
            assert not any(one.startswith("cra-") for one in item.expected_segment_ids), item.id


def test_the_committed_set_covers_both_vocabularies_and_languages() -> None:
    items = load_golden_set().items

    assert {item.vocabulary for item in items} == set(Vocabulary)
    assert {item.lang for item in items} >= {"de", "en"}


def test_every_verified_item_records_when_it_was_verified_by_hand() -> None:
    """Drafted labels must not masquerade as checked ones.

    Verification is a human reading the labelled text against the question.
    An item flipped to verified without a dated note saying so is a draft that
    quietly became authority.
    """
    for item in load_golden_set().verified:
        assert re.search(r"VERIFIED BY HAND \d{4}-\d{2}-\d{2}", item.note), item.id


def test_an_unknown_answer_type_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, VALID.replace('"answerable"', '"probably"'))

    with pytest.raises(ValidationError, match="answer_type"):
        load_golden_set(path)


def test_an_unknown_vocabulary_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, VALID.replace('"statute"', '"legalese"'))

    with pytest.raises(ValidationError, match="vocabulary"):
        load_golden_set(path)


def test_an_unanswerable_item_may_not_carry_gold_labels(tmp_path: Path) -> None:
    """Otherwise 'nothing answers this' and 'this answers it' could both be true."""
    path = write(tmp_path, VALID.replace('"answerable"', '"unanswerable"'))

    with pytest.raises(ValidationError, match="no expected_segment_ids"):
        load_golden_set(path)


def test_an_answerable_item_must_carry_a_gold_label(tmp_path: Path) -> None:
    path = write(tmp_path, VALID.replace('["cra-en:article:13"]', "[]"))

    with pytest.raises(ValidationError, match="at least one expected segment"):
        load_golden_set(path)


def test_a_malformed_segment_id_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, VALID.replace('"cra-en:article:13"', '"Article 13"'))

    with pytest.raises(ValidationError, match="not segment ids"):
        load_golden_set(path)


def test_duplicate_item_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="duplicate item ids: an-item"):
        load_golden_set(write(tmp_path, VALID + VALID))


def test_an_unexpected_key_is_rejected(tmp_path: Path) -> None:
    """As on Source: a field nobody reads must not look like it works."""
    path = write(tmp_path, VALID + '    difficulty = "hard"\n')

    with pytest.raises(ValidationError, match="difficulty"):
        load_golden_set(path)


def test_verified_defaults_to_false() -> None:
    """Drafting must never produce a verified item by omission."""
    item = GoldenSet.model_validate(
        {
            "items": [
                {
                    "id": "an-item",
                    "question": "What are the obligations of manufacturers?",
                    "lang": "en",
                    "vocabulary": "statute",
                    "answer_type": "answerable",
                    "expected_segment_ids": ["cra-en:article:13"],
                }
            ]
        }
    ).items[0]

    assert item.verified is False
