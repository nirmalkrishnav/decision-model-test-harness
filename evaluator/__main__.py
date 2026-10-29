"""Run intent classification and execution-decision benchmarks sequentially."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evaluator import execution_gate, run
from evaluator.workers import positive_workers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=run.CONFIG_PATH)
    parser.add_argument("--deployment", help="run only this configured deployment in both suites")
    parser.add_argument("--workers", type=positive_workers, default=10,
                        help="maximum concurrent evaluation calls per model (default: 10)")
    parser.add_argument("--intent-dataset", type=Path, default=run.DATASET_PATH)
    parser.add_argument("--execution-dataset", type=Path, default=execution_gate.DATASET_PATH)
    parser.add_argument("--results-dir", type=Path, default=run.DEFAULT_RESULTS_DIR,
                        help="results root; execution-decision outputs use its execution_gate subfolder")
    args = parser.parse_args(argv)
    shared = ["--config", str(args.config), "--workers", str(args.workers)]
    if args.deployment:
        shared += ["--deployment", args.deployment]
    suites = [
        ("Intent classification", run.main, args.intent_dataset, args.results_dir),
        ("Execution decisions", execution_gate.main, args.execution_dataset,
         args.results_dir / "execution_gate"),
    ]
    statuses = []
    print("Running both benchmarks sequentially. Submitted Python/SQL is never executed.", flush=True)
    for index, (name, entry, dataset, results_dir) in enumerate(suites, 1):
        print(f"\n[{index}/{len(suites)}] Starting {name}...", flush=True)
        status = entry(shared + ["--dataset", str(dataset), "--results-dir", str(results_dir)])
        statuses.append(status)
        print(f"[{index}/{len(suites)}] {name}: exit code {status}", flush=True)
    exit_code = max(statuses)
    print(f"\nBoth suites finished. Overall exit code: {exit_code}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
