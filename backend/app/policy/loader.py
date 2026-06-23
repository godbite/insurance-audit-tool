"""
Policy loader — reads and validates policy_terms.json at startup.

Cached as a singleton. Never read the file at request time.
All agents receive PolicyTerms via dependency injection or direct import.
"""
import json
from functools import lru_cache
from pathlib import Path

from app.models.domain import PolicyTerms


@lru_cache(maxsize=1)
def load_policy(policy_file_path: str = "./policy_terms.json") -> PolicyTerms:
    """
    Load and validate the policy terms JSON file.
    Raises FileNotFoundError or ValidationError on misconfiguration — fail fast at startup.
    """
    path = Path(policy_file_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Policy file not found at '{path.resolve()}'. "
            "Set POLICY_FILE_PATH in .env to the correct path."
        )

    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    # Pydantic v2 will raise ValidationError with field-level detail if schema is wrong
    policy = PolicyTerms.model_validate(raw)
    return policy


def get_policy() -> PolicyTerms:
    """FastAPI dependency — returns singleton PolicyTerms."""
    from app.core.config import get_settings
    settings = get_settings()
    return load_policy(settings.policy_file_path)
