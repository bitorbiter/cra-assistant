"""The golden set loader. Committed data, validated like the registry."""

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


def test_the_committed_set_has_no_untrusted_only_items_until_they_are_reauthored() -> None:
    """Deleted on 2026-09-13: their labels named heading-slug ids that opaque
    untrusted ids replaced (ADR-0017). When they are re-authored this test flips
    back to requiring all three answer types."""
    types = {item.answer_type for item in load_golden_set().items}

    assert types == {AnswerType.ANSWERABLE, AnswerType.UNANSWERABLE}


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


def test_nothing_in_the_committed_set_is_verified_yet() -> None:
    """Drafted labels must not masquerade as checked ones.

    When Achim verifies items by hand this test changes; until then it is the
    guard against a draft quietly becoming authority.
    """
    assert load_golden_set().verified == ()


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
