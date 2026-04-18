"""Regression tests for the TX signature regex used to detect payment hashes."""

import pytest

from bot.course import TX_PATTERN


VALID_SIGNATURES = [
    # Real-world Solana tx signatures (base58, 87-88 chars)
    "5KvJQtLK8yHd9y6qZ3UYm2pM8yL4rN3ksP9XJTe4wGHdYF2nWsqLcXPr2zLvXvKpMWu3ZrQaBfDmvhsRPpkaEJqu",
    "3yZvN2NqK8fXvQwYxT3rMpLjDqPf7UWGsRxHyLpA8XBGPmJzFnMqVpWcGdJRB3UqYT8nF4kEZP6nC2W7DxMNqQRL",
]

INVALID_SIGNATURES = [
    "",
    "short",
    "0OIl" * 22,  # Contains base58-forbidden chars (0, O, I, l)
    "a" * 79,     # Too short
    "a" * 101,    # Too long
    "hello world this is not a tx",
    "https://solscan.io/tx/5KvJQt",
    "5Kv.JQt",    # Contains a dot
]


@pytest.mark.parametrize("sig", VALID_SIGNATURES)
def test_valid_tx_signatures_match(sig):
    assert TX_PATTERN.match(sig), f"{sig} should be accepted"


@pytest.mark.parametrize("sig", INVALID_SIGNATURES)
def test_invalid_tx_signatures_rejected(sig):
    assert not TX_PATTERN.match(sig), f"{sig} should be rejected"
