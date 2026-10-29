"""Replace one deployment's executions with a complete rerun, preserving originals."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from evaluator.metrics import evaluate_records, load_jsonl
from evaluator.run import CONFIG_PATH, ROOT, load_config

SCHEMA_PATH = ROOT / "test_data" / "schemas" / "canonical_output.schema.json"


def _key(record: dict[str, Any]) -> tuple[str, str, int | None, int]:
    expected = record["expected"]
    return (
        record["deployment"], expected["test_case_id"],
        expected.get("turn_index"), record["repeat_index"],
    )


def merge_records(
    previous: list[dict[str, Any]],
    replacement: list[dict[str, Any]],
    deployment: str,
) -> list[dict[str, Any]]:
    if not previous or not replacement:
        raise ValueError("Both runs must contain records")
    if any(row["deployment"] != deployment for row in replacement):
        raise ValueError("Replacement run must contain only the selected deployment")
    if len({_key(row) for row in previous}) != len(previous):
        raise ValueError("Previous run contains duplicate executions")
    new_by_key = {_key(row): row for row in replacement}
    if len(new_by_key) != len(replacement):
        raise ValueError("Replacement run contains duplicate executions")
    old_by_key = {_key(row): row for row in previous if row["deployment"] == deployment}
    if not old_by_key or old_by_key.keys() != new_by_key.keys():
        raise ValueError("Replacement must match every original execution for the deployment exactly")
    for key, new in new_by_key.items():
        old = old_by_key[key]
        for field in ("question", "expected", "context", "category", "spec_version"):
            if old.get(field) != new.get(field):
                raise ValueError(f"{key}: replacement changed benchmark field {field!r}")
    return [
        new_by_key[_key(row)] if row["deployment"] == deployment else row
        for row in previous
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", type=Path)
    parser.add_argument("replacement", type=Path)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new combined JSONL path")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args(argv)
    try:
        summary_path = args.output.with_name(args.output.stem + "-summary.json")
        provenance_path = args.output.with_name(args.output.stem + "-provenance.json")
        for path in (args.output, summary_path, provenance_path):
            if path.exists():
                raise ValueError(f"Refusing to overwrite {path}")
        previous = load_jsonl(args.previous)
        replacement = load_jsonl(args.replacement)
        combined = merge_records(previous, replacement, args.deployment)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        config = load_config(args.config)
        summary = {
            deployment: evaluate_records(
                [row for row in combined if row["deployment"] == deployment],
                schema["properties"]["intent"]["enum"],
                list(schema["$defs"]["entities"]["properties"]),
                config.get("pricing_usd_per_1m_tokens", {}),
            )
            for deployment in dict.fromkeys(row["deployment"] for row in combined)
        }
        provenance = {
            "previous_run": str(args.previous.resolve()),
            "previous_sha256": hashlib.sha256(args.previous.read_bytes()).hexdigest(),
            "replacement_run": str(args.replacement.resolve()),
            "replacement_sha256": hashlib.sha256(args.replacement.read_bytes()).hexdigest(),
            "replaced_deployment": args.deployment,
            "replaced_executions": len(replacement),
            "total_executions": len(combined),
            "note": (
                "Only the selected deployment was rerun. Other deployments retain original "
                "predictions, timestamps, usage and errors. Prompt revisions can affect comparability."
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            for record in combined:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
        for path, content in ((summary_path, summary), (provenance_path, provenance)):
            with path.open("x", encoding="utf-8") as output:
                json.dump(content, output, indent=2, ensure_ascii=False)
                output.write("\n")
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Combined results: {args.output}")
    print(f"Combined summary: {summary_path}")
    print(f"Provenance: {provenance_path}")
    print(f"Executions: {len(combined)}; replaced: {len(replacement)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
