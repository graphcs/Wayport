"""Tests for human-readable connection codes."""

from __future__ import annotations

import pytest

from wayport.common.codes import (
    code_key,
    code_space,
    derive_word_code,
    generate_word_code,
    is_word_code,
)
from wayport.common.wordlist import ADJECTIVES, NOUNS


def test_wordlists_have_no_duplicates() -> None:
    """A duplicate silently shrinks the code space and biases derivation."""
    assert len(set(ADJECTIVES)) == len(ADJECTIVES)
    assert len(set(NOUNS)) == len(NOUNS)


def test_words_are_typeable() -> None:
    for word in (*ADJECTIVES, *NOUNS):
        assert word.isalpha()
        assert word.islower()
        assert word.isascii()
        assert 2 < len(word) <= 9


def test_code_space_is_large_enough_to_resist_guessing() -> None:
    """With relay rate limiting, this is the second line of defence."""
    assert code_space() > 2_000_000


def test_generated_code_shape() -> None:
    for _ in range(100):
        code = generate_word_code()
        adjective, noun, number = code.split("-")
        assert adjective in ADJECTIVES
        assert noun in NOUNS
        assert 10 <= int(number) <= 99


def test_generated_codes_vary() -> None:
    assert len({generate_word_code() for _ in range(200)}) > 150


def test_derived_code_is_stable_for_a_key() -> None:
    """The same machine keeps its code across restarts."""
    assert derive_word_code("some-secret-key") == derive_word_code("some-secret-key")


def test_derived_code_changes_with_the_key() -> None:
    """Rotating the key must produce a different code."""
    assert derive_word_code("key-a") != derive_word_code("key-b")


def test_derived_code_is_valid() -> None:
    for key in ("a", "another-key", "x" * 100):
        assert is_word_code(derive_word_code(key))


@pytest.mark.parametrize(
    "written",
    ["blue-otter-42", "BLUE-OTTER-42", "Blue Otter 42", "blueotter42", "  blue_otter_42  "],
)
def test_code_key_normalizes_how_people_type_it(written: str) -> None:
    assert code_key(written) == code_key("blue-otter-42")


def test_code_key_keeps_distinct_codes_distinct() -> None:
    assert code_key("blue-otter-42") != code_key("blue-otter-43")


def test_code_key_handles_legacy_short_codes() -> None:
    """Old six-character codes must still resolve to themselves."""
    assert code_key("BS3WW2") == "bs3ww2"


def test_is_word_code_rejects_non_codes() -> None:
    for text in ("BS3WW2", "not-a-code-99", "blue-otter", "blue-otter-abc", ""):
        assert not is_word_code(text)
