"""Deterministic randomness: every random choice is seeded from SHA-256 of fixed strings.

A scenario's seed is derived from its ID, so re-running the generator gives identical output.
Never use the current time, ``uuid4``, or iteration over sets here.
"""

import hashlib
import random


def seed_of(*parts: str) -> int:
    """64-bit seed from SHA-256 of ``parts`` joined with ``|``."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def rng(*parts: str) -> random.Random:
    """A ``random.Random`` seeded from ``parts`` (e.g. the scenario ID and a purpose)."""
    return random.Random(seed_of(*parts))


def short_hash(*parts: str, length: int = 10) -> str:
    """Hex digest prefix, for identifiers such as e-mail Message-IDs."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:length]
