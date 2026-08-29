"""Human-readable connection codes, e.g. ``blue-otter-42``.

A code is stable for a machine so you can memorise it, but it is derived from a
random key stored locally rather than from the hostname. The old scheme --
``SHA-256(device_name)`` -- meant anyone who knew the machine's name could
compute its code, which is not a property you want on a relay anyone can reach.
"""

from __future__ import annotations

import hashlib
import re
import secrets

from wayport.common.wordlist import ADJECTIVES, NOUNS

NUMBER_MIN = 10
NUMBER_MAX = 99
NUMBER_SPAN = NUMBER_MAX - NUMBER_MIN + 1

# Codes reduced to their comparable form: letters and digits only.
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def code_space() -> int:
    """Total number of distinct codes."""
    return len(ADJECTIVES) * len(NOUNS) * NUMBER_SPAN


def generate_word_code() -> str:
    """Generate a random code."""
    return format_code(
        secrets.choice(ADJECTIVES),
        secrets.choice(NOUNS),
        secrets.randbelow(NUMBER_SPAN) + NUMBER_MIN,
    )


def derive_word_code(key: str) -> str:
    """Derive a stable code from a per-machine secret key.

    Deterministic for a given key, so the same machine keeps the same code
    across restarts, but unguessable without the key.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    value = int.from_bytes(digest, "big")
    adjective = ADJECTIVES[value % len(ADJECTIVES)]
    noun = NOUNS[(value // len(ADJECTIVES)) % len(NOUNS)]
    number = (value // (len(ADJECTIVES) * len(NOUNS))) % NUMBER_SPAN + NUMBER_MIN
    return format_code(adjective, noun, number)


def format_code(adjective: str, noun: str, number: int) -> str:
    return f"{adjective}-{noun}-{number}"


def code_key(raw: str) -> str:
    """Reduce a code to a canonical lookup key.

    Makes ``blue-otter-42``, ``BLUE OTTER 42`` and ``BlueOtter42`` equivalent,
    and leaves legacy six-character codes as their own key so they keep working.
    """
    return _NON_ALNUM.sub("", raw.strip().lower())


def is_word_code(raw: str) -> bool:
    """True if the input looks like an adjective-noun-number code."""
    parts = raw.strip().lower().replace("_", "-").split("-")
    return len(parts) == 3 and parts[0] in ADJECTIVES and parts[1] in NOUNS and parts[2].isdigit()
