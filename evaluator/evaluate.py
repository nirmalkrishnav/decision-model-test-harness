"""Recompute evaluation metrics from a saved JSONL run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evaluator.metrics import evaluate_records, load_jsonl, validate_pricing

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "test_data" / "schemas" / "canonical_output.schema.json"
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a saved CPG benchmark JSONL run.")
    parser.add_argument("results", type=Path, help="run-*.jsonl file created by evaluator/run.py")
    parser.add_argument("--output", type=Path, help="optional path to write the summary JSON")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args(argv)
    try:
        records = load_jsonl(args.results)
        if not records:
            raise ValueError(f"{args.results}: no result records found")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError(f"{args.config}: expected a JSON object")
        pricing = config.get("pricing_usd_per_1m_tokens", {})
        if not isinstance(pricing, dict):
            raise ValueError("pricing_usd_per_1m_tokens must be a JSON object")
        validate_pricing(pricing)
        labels = schema["properties"]["intent"]["enum"]
        entities = list(schema["$defs"]["entities"]["properties"].keys())
        summaries = {
            deployment: evaluate_records(
                [record for record in records if record.get("deployment") == deployment],
                labels,
                entities,
                pricing,
            )
            for deployment in dict.fromkeys(record.get("deployment", "") for record in records)
        }
        rendered = json.dumps(summaries, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
