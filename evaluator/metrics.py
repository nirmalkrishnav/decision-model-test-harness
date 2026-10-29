"""Metrics for canonical model outputs and operational run records."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _prf(true_positive: int, false_positive: int, false_negative: int) -> dict[str, float]:
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def validate_pricing(pricing: dict[str, dict[str, float | None]]) -> None:
    for deployment, rates in pricing.items():
        if not isinstance(deployment, str) or not deployment or not isinstance(rates, dict):
            raise ValueError("Each pricing entry must map a deployment name to token rates")
        if "input_usd_per_1m_tokens" not in rates:
            raise ValueError(f"{deployment}: input_usd_per_1m_tokens is required")
        for key in (
            "input_usd_per_1m_tokens",
            "output_usd_per_1m_tokens",
            "cached_input_usd_per_1m_tokens",
        ):
            value = rates.get(key)
            if value is None and key != "input_usd_per_1m_tokens":
                continue
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{deployment}: {key} must be a non-negative number")


def calculate_cost(
    record: dict[str, Any],
    rates: dict[str, float | None] | None,
) -> dict[str, float | bool | None]:
    input_tokens = int(record.get("input_tokens") or 0)
    output_tokens = int(record.get("output_tokens") or 0)
    cached_input_tokens = record.get("cached_input_tokens")
    if record.get("error") and input_tokens == 0 and output_tokens == 0:
        return {
            "input_cost_usd": None,
            "cached_input_cost_usd": None,
            "output_cost_usd": None,
            "cost_usd": None,
            "cost_estimate_complete": False,
        }
    if rates is None:
        return {
            "input_cost_usd": None,
            "cached_input_cost_usd": None,
            "output_cost_usd": None,
            "cost_usd": None,
            "cost_estimate_complete": False,
        }

    input_rate = rates["input_usd_per_1m_tokens"]
    output_rate = rates.get("output_usd_per_1m_tokens")
    cached_rate = rates.get("cached_input_usd_per_1m_tokens")

    if cached_rate is None:
        input_cost = input_tokens * input_rate / 1_000_000
        cached_input_cost = 0.0
    elif cached_input_tokens is None:
        input_cost = None
        cached_input_cost = None
    else:
        uncached_input_tokens = max(input_tokens - int(cached_input_tokens), 0)
        input_cost = uncached_input_tokens * input_rate / 1_000_000
        cached_input_cost = int(cached_input_tokens) * cached_rate / 1_000_000

    output_cost = (
        output_tokens * output_rate / 1_000_000
        if output_rate is not None
        else (0.0 if output_tokens == 0 else None)
    )
    components = (input_cost, cached_input_cost, output_cost)
    complete = all(component is not None for component in components)
    total = sum(component for component in components if component is not None) if complete else None
    return {
        "input_cost_usd": input_cost,
        "cached_input_cost_usd": cached_input_cost,
        "output_cost_usd": output_cost,
        "cost_usd": total,
        "cost_estimate_complete": complete,
    }


def evaluate_records(
    records: list[dict[str, Any]],
    intents: list[str],
    entity_names: list[str],
    pricing: dict[str, dict[str, float]] | None = None,
    *,
    include_categories: bool = True,
) -> dict[str, Any]:
    """Return one metrics summary for a deployment's execution records."""
    pricing = pricing or {}
    validate_pricing(pricing)
    count = len(records)
    if not count:
        raise ValueError("Cannot evaluate an empty result set")

    intent_counts = {intent: [0, 0, 0] for intent in intents}
    entity_counts = {name: [0, 0, 0] for name in entity_names}
    intent_correct = 0
    entity_exact_match = 0
    entity_evaluated_count = 0
    schema_valid = 0
    failures = 0
    latencies: list[float] = []
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
    cached_input_reported_count = 0
    cost_components: dict[str, float] = {
        "input_cost_usd": 0.0,
        "cached_input_cost_usd": 0.0,
        "output_cost_usd": 0.0,
    }
    cost_component_known = {key: True for key in cost_components}
    run_groups: dict[str, list[str | None]] = defaultdict(list)

    for record in records:
        expected = record["expected"]
        actual = record.get("actual")
        deployment = record.get("deployment", "")
        if record.get("error"):
            failures += 1
        if record.get("schema_valid") is True:
            schema_valid += 1

        expected_intent = expected["intent"]
        usable_actual = actual if record.get("schema_valid") is True and isinstance(actual, dict) else None
        actual_intent = usable_actual.get("intent") if usable_actual is not None else None
        if actual_intent == expected_intent:
            intent_correct += 1
        for label in intents:
            if actual_intent == label and expected_intent == label:
                intent_counts[label][0] += 1
            elif actual_intent == label:
                intent_counts[label][1] += 1
            elif expected_intent == label:
                intent_counts[label][2] += 1

        expected_entities = expected.get("entities", {})
        actual_entities = usable_actual.get("entities", {}) if usable_actual is not None else {}
        case_exact = usable_actual is not None
        evaluate_entities = record.get("entity_evaluation", "entities" in expected)
        if not isinstance(evaluate_entities, bool):
            raise ValueError("entity_evaluation must be a boolean")
        if evaluate_entities:
            entity_evaluated_count += 1
        for name in entity_names if evaluate_entities else []:
            expected_values = set(expected_entities[name])
            actual_values = set(actual_entities.get(name, [])) if isinstance(actual_entities, dict) else set()
            if expected_values != actual_values:
                case_exact = False
            tp, fp, fn = entity_counts[name]
            entity_counts[name] = [
                tp + len(expected_values & actual_values),
                fp + len(actual_values - expected_values),
                fn + len(expected_values - actual_values),
            ]
        if evaluate_entities and case_exact:
            entity_exact_match += 1

        case_id = expected["test_case_id"]
        run_groups[f"{deployment}:{case_id}"].append(
            json.dumps(usable_actual, sort_keys=True, separators=(",", ":"))
            if usable_actual is not None else None
        )
        if isinstance(record.get("latency_ms"), (int, float)):
            latencies.append(float(record["latency_ms"]))
        input_tokens += int(record.get("input_tokens") or 0)
        output_tokens += int(record.get("output_tokens") or 0)
        if record.get("cached_input_tokens") is not None:
            cached_input_tokens += int(record["cached_input_tokens"])
            cached_input_reported_count += 1

        rates = pricing.get(deployment)
        record_cost = calculate_cost(record, rates)
        for component in cost_components:
            value = record_cost[component]
            if value is None:
                cost_component_known[component] = False
            else:
                cost_components[component] += value

    class_metrics = {
        label: _prf(tp, fp, fn)
        for label, (tp, fp, fn) in intent_counts.items()
    }
    entity_metrics = {
        name: {
            **_prf(tp, fp, fn),
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
        }
        for name, (tp, fp, fn) in entity_counts.items()
    }
    intent_macro = {
        metric: statistics.mean(values[metric] for values in class_metrics.values())
        for metric in ("precision", "recall", "f1")
    }
    entity_total = [
        sum(counts[index] for counts in entity_counts.values())
        for index in range(3)
    ]
    consistency_groups = [runs for runs in run_groups.values() if len(runs) > 1]
    consistency = (
        sum(runs[0] is not None and len(set(runs)) == 1 for runs in consistency_groups)
        / len(consistency_groups)
        if consistency_groups else None
    )
    cost_complete = all(cost_component_known.values())
    cost_total = sum(cost_components.values()) if cost_complete else None
    cached_tokens_complete = cached_input_reported_count == count

    summary = {
        "question_count": count,
        "intent": {
            "accuracy": intent_correct / count,
            "macro": intent_macro,
            "per_class": class_metrics,
        },
        "entities": {
            "exact_match": entity_exact_match / entity_evaluated_count if entity_evaluated_count else None,
            "micro": _prf(*entity_total) if entity_evaluated_count else None,
            "per_type": entity_metrics if entity_evaluated_count else None,
        },
        "schema_valid_pct": 100 * schema_valid / count,
        "failure_rate_pct": 100 * failures / count,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "tokens": {
            "input_total": input_tokens,
            "output_total": output_tokens,
            "cached_input_total": cached_input_tokens if cached_tokens_complete else None,
            "cached_input_reported_count": cached_input_reported_count,
            "input_mean": input_tokens / count,
            "output_mean": output_tokens / count,
        },
        "cost_usd": cost_total,
        "cost_usd_per_question": cost_total / count if cost_total is not None else None,
        "cost_estimate_complete": cost_complete,
        "cost_breakdown_usd": {
            key: value if cost_component_known[key] else None
            for key, value in cost_components.items()
        },
        "repeat_consistency": consistency,
        "repeat_groups": len(consistency_groups),
    }
    if entity_evaluated_count != count:
        summary["entities"]["evaluated_count"] = entity_evaluated_count
        summary["entities"]["excluded_count"] = count - entity_evaluated_count
    if include_categories and any("category" in record for record in records):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            groups[record.get("category", "uncategorized")].append(record)
        summary["by_category"] = {
            category: evaluate_records(
                rows, intents, entity_names, pricing, include_categories=False
            )
            for category, rows in groups.items()
        }
    return summary


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return records
