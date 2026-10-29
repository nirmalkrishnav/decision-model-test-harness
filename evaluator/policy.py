"""Shared compact classification policy; full taxonomy stays an annotation reference."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "intent_runtime_policy.yml"


def load_policy(*, include_context: bool = False) -> dict[str, Any]:
    try:
        policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Unable to read runtime policy {POLICY_PATH}: {exc}") from exc
    required = {"version", "definitions", "precedence", "boundary_rules", "context_rules"}
    if not isinstance(policy, dict) or set(policy) != required:
        raise ValueError(f"{POLICY_PATH}: expected fields {', '.join(sorted(required))}")
    if not isinstance(policy["version"], str) or not policy["version"].strip():
        raise ValueError(f"{POLICY_PATH}: version must be a non-empty string")
    definitions = policy["definitions"]
    if not isinstance(definitions, dict) or len(definitions) != 10 or any(
        not isinstance(label, str) or not label.strip()
        or not isinstance(description, str) or not description.strip()
        for label, description in definitions.items()
    ):
        raise ValueError(f"{POLICY_PATH}: definitions must contain ten named, non-empty descriptions")
    for field in ("precedence", "boundary_rules", "context_rules"):
        values = policy[field]
        if not isinstance(values, list) or not values or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"{POLICY_PATH}: {field} must be a non-empty list of strings")
    if len(policy["precedence"]) != len(definitions) or set(policy["precedence"]) != set(definitions):
        raise ValueError(f"{POLICY_PATH}: precedence must contain each intent exactly once")
    result = {
        "version": policy["version"],
        "intent_taxonomy": {
            "definitions": definitions,
            "precedence": policy["precedence"],
        },
        "boundary_rules": policy["boundary_rules"],
    }
    if include_context:
        result["context_rules"] = policy["context_rules"]
    return result
