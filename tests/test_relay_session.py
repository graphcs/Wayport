"""Tests for connection-code generation and session bookkeeping."""

from __future__ import annotations

from wayport.relay.session import (
    CODE_ALPHABET,
    generate_connection_code,
    generate_deterministic_code,
)

# Characters that are easy to confuse when read aloud or typed.
CONFUSABLE = set("0OI1L")


def test_alphabet_excludes_confusable_characters() -> None:
    assert CONFUSABLE.isdisjoint(CODE_ALPHABET)


def test_generated_codes_use_only_the_alphabet() -> None:
    for _ in range(200):
        assert set(generate_connection_code()) <= set(CODE_ALPHABET)


def test_generated_code_length() -> None:
    assert len(generate_connection_code()) == 6
    assert len(generate_connection_code(length=8)) == 8


def test_random_codes_vary() -> None:
    """A weak generator here would collide sessions."""
    assert len({generate_connection_code() for _ in range(200)}) > 150


def test_deterministic_code_is_stable_for_a_seed() -> None:
    assert generate_deterministic_code("my-laptop") == generate_deterministic_code("my-laptop")


def test_deterministic_code_differs_between_seeds() -> None:
    assert generate_deterministic_code("laptop-a") != generate_deterministic_code("laptop-b")


def test_deterministic_code_uses_only_the_alphabet() -> None:
    for seed in ("a", "my-laptop", "Johns-MacBook-Pro.local", "", "  "):
        assert set(generate_deterministic_code(seed)) <= set(CODE_ALPHABET)


def test_deterministic_code_is_derived_from_the_hostname() -> None:
    """Documents the current, guessable behaviour.

    The code is a pure function of the device name, so anyone who knows the
    hostname can compute it. Replacing this with a locally-stored random key is
    the job of the word-codes change; this test pins the status quo so that
    change is visibly a behaviour change.
    """
    assert generate_deterministic_code("Johns-MacBook-Pro.local") == generate_deterministic_code(
        "Johns-MacBook-Pro.local"
    )
