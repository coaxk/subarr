"""#259: pure key-generation + hashing helpers for managed API keys."""

from __future__ import annotations

import hashlib

from subarr.api_keys import generate_key, hash_key


def test_token_has_prefix_and_entropy():
    token, _, _ = generate_key()
    assert token.startswith("sbar_")
    # sbar_ + token_urlsafe(32) → comfortably long
    assert len(token) > 40


def test_tokens_are_distinct():
    t1, _, _ = generate_key()
    t2, _, _ = generate_key()
    assert t1 != t2


def test_hash_is_sha256_hex_of_token():
    token, token_hash, _ = generate_key()
    assert token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert len(token_hash) == 64
    assert all(c in "0123456789abcdef" for c in token_hash)


def test_hash_key_matches_generate_and_is_deterministic():
    token, token_hash, _ = generate_key()
    assert hash_key(token) == token_hash
    assert hash_key(token) == hash_key(token)


def test_last4_is_token_tail():
    token, _, last4 = generate_key()
    assert last4 == token[-4:]
    assert len(last4) == 4
