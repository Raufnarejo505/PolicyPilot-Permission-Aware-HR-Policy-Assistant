"""Real password primitives and bounded verification of untrusted stored hashes."""

import hashlib
import re
import secrets
from dataclasses import dataclass
from unittest.mock import Mock

import pytest
from argon2 import PasswordHasher, Type, extract_parameters

from policypilot.app.core.security import (
    PasswordSecurity,
    new_token,
    token_digest,
    username_digest,
)


@dataclass(frozen=True)
class PasswordMaterial:
    security: PasswordSecurity
    password: str
    encoded: str
    salted_again: str


@pytest.fixture(scope="module")
def passwords():
    # Share production-cost material, rather than weakening the Argon2 profile.
    security = PasswordSecurity()
    password = secrets.token_urlsafe(24)
    return PasswordMaterial(security, password, security.hash(password), security.hash(password))


def test_hashes_are_salted_argon2id_and_verify_with_the_real_library(passwords):
    assert passwords.encoded != passwords.salted_again
    for encoded in (passwords.encoded, passwords.salted_again):
        parameters = extract_parameters(encoded)
        assert parameters.type is Type.ID
        assert parameters.version == 19
        assert (parameters.memory_cost, parameters.time_cost, parameters.parallelism) == (
            65536,
            3,
            4,
        )
        assert (parameters.salt_len, parameters.hash_len) == (16, 32)
        assert PasswordHasher().verify(encoded, passwords.password) is True
        assert passwords.security.verify(encoded, passwords.password) is True
        assert passwords.security.verify(encoded, passwords.password + "wrong") is False


@pytest.mark.parametrize("encoded", [None, "", "not-an-argon2-hash", "$argon2id$broken"])
def test_missing_and_malformed_hashes_deny(passwords, encoded):
    assert passwords.security.verify(encoded, passwords.password) is False


def test_dummy_hash_can_never_authenticate_a_missing_credential(passwords, monkeypatch):
    verifier = Mock(return_value=True)
    monkeypatch.setattr(PasswordHasher, "verify", verifier)
    assert passwords.security.verify(None, passwords.password) is False
    verifier.assert_called_once_with(passwords.security._dummy, passwords.password)


@pytest.mark.parametrize("password", ["a" * 12, "a" * 1024, "é" * 6, "🔒" * 256])
def test_hash_policy_accepts_utf8_byte_boundaries(passwords, password):
    encoded = passwords.security.hash(password)
    assert passwords.security.verify(encoded, password) is True


@pytest.mark.parametrize(
    "password",
    ["", "a" * 11, "a" * 1025, "é" * 5, "é" * 513, None, b"twelve-bytes", 123, True],
    ids=[
        "empty",
        "short",
        "oversize",
        "utf8-short",
        "utf8-oversize",
        "null",
        "bytes",
        "int",
        "bool",
    ],
)
def test_invalid_passwords_are_rejected_before_hashing(passwords, monkeypatch, password):
    hasher = Mock(side_effect=AssertionError("Invalid input reached Argon2"))
    monkeypatch.setattr(PasswordHasher, "hash", hasher)
    with pytest.raises(ValueError, match="^Password must contain 12 to 1024 UTF-8 bytes$"):
        passwords.security.hash(password)
    hasher.assert_not_called()


@pytest.mark.parametrize(
    "password",
    ["", "a" * 1025, "é" * 513, None, b"password", 123, True],
    ids=["empty", "oversize", "utf8-oversize", "null", "bytes", "int", "bool"],
)
def test_invalid_passwords_deny_before_verification(passwords, monkeypatch, password):
    verifier = Mock(side_effect=AssertionError("Invalid input reached Argon2"))
    monkeypatch.setattr(PasswordHasher, "verify", verifier)
    assert passwords.security.verify(passwords.encoded, password) is False
    verifier.assert_not_called()


def test_non_utf8_encodable_password_denies_instead_of_raising(passwords):
    assert passwords.security.verify(passwords.encoded, "\ud800") is False


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("argon2id", "argon2i"),
        ("v=19", "v=16"),
        ("m=65536", "m=4294967295"),
        ("t=3", "t=4294967295"),
        ("p=4", "p=16777215"),
    ],
    ids=["variant", "version", "huge-memory", "huge-time", "huge-parallelism"],
)
def test_nonstandard_cost_profiles_only_verify_the_normal_cost_dummy(
    passwords, monkeypatch, original, replacement
):
    encoded = passwords.encoded.replace(original, replacement)
    assert extract_parameters(encoded) != extract_parameters(passwords.encoded)
    # Never let a guard regression allocate attacker-selected resources in this test.
    verifier = Mock(return_value=True)
    monkeypatch.setattr(PasswordHasher, "verify", verifier)
    assert passwords.security.verify(encoded, passwords.password) is False
    verifier.assert_called_once_with(passwords.security._dummy, passwords.password)


@pytest.mark.parametrize("field", [4, 5], ids=["salt-length", "hash-length"])
def test_nonstandard_salt_and_hash_lengths_are_rejected(passwords, monkeypatch, field):
    fields = passwords.encoded.split("$")
    fields[field] = "YWJjZGVmZ2g"
    encoded = "$".join(fields)
    assert extract_parameters(encoded) != extract_parameters(passwords.encoded)
    verifier = Mock(return_value=True)
    monkeypatch.setattr(PasswordHasher, "verify", verifier)
    assert passwords.security.verify(encoded, passwords.password) is False
    verifier.assert_called_once_with(passwords.security._dummy, passwords.password)


def test_new_tokens_are_random_opaque_urlsafe_43_character_values():
    tokens = [new_token() for _ in range(32)]
    assert len(set(tokens)) == len(tokens)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", token) for token in tokens)


def test_new_token_uses_32_bytes_of_cryptographic_randomness(monkeypatch):
    random_token = Mock(return_value="A" * 43)
    monkeypatch.setattr("policypilot.app.core.security.secrets.token_urlsafe", random_token)
    assert new_token() == "A" * 43
    random_token.assert_called_once_with(32)


def test_token_digest_is_deterministic_lowercase_sha256():
    token = "Ab_-" * 10 + "xyz"
    expected = hashlib.sha256(token.encode("ascii")).hexdigest()
    assert token_digest(token) == token_digest(token) == expected
    assert re.fullmatch(r"[0-9a-f]{64}", token_digest(token))
    assert token_digest(token.swapcase()) != expected


@pytest.mark.parametrize(
    "token",
    [None, 123, b"A" * 43, "", "A" * 42, "A" * 44, "A" * 42 + "=", "é" * 43, "A" * 43 + "\n"],
)
def test_malformed_tokens_have_only_a_generic_error(token):
    with pytest.raises(ValueError, match="^Invalid session$"):
        token_digest(token)


def test_username_digest_hashes_exact_case_sensitive_utf8_bytes():
    username = "Émployee"
    expected = hashlib.sha256(username.encode("utf-8")).hexdigest()
    assert username_digest(username) == username_digest(username) == expected
    assert username_digest(username.lower()) != expected
    assert re.fullmatch(r"[0-9a-f]{64}", expected)
