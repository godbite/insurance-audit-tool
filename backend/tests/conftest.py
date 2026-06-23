"""
Shared fixtures for all tests.
"""
from __future__ import annotations

import pytest

from app.policy.loader import load_policy
from app.models.domain import PolicyTerms


@pytest.fixture(scope="session")
def policy() -> PolicyTerms:
    """Load the real policy terms for all tests."""
    return load_policy("./policy_terms.json")
