import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from evaluator.__main__ import main
from evaluator import execution_gate, run
from evaluator.metrics import load_jsonl


class CommonEntrypointTests(unittest.TestCase):
    def test_defaults_run_both_suites_in_order(self):
        calls = []

        def capture(name):
            def invoke(args):
                calls.append((name, args))
                return 0
            return invoke

        with patch("evaluator.run.main", side_effect=capture("intent")), patch(
            "evaluator.execution_gate.main", side_effect=capture("execution")
        ), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([]), 0)
        self.assertEqual([name for name, _ in calls], ["intent", "execution"])
        self.assertEqual(calls[0][1], [
            "--config", str(run.CONFIG_PATH), "--workers", "10", "--dataset", str(run.DATASET_PATH),
            "--results-dir", str(run.DEFAULT_RESULTS_DIR),
        ])
        self.assertEqual(calls[1][1], [
            "--config", str(run.CONFIG_PATH), "--workers", "10", "--dataset", str(execution_gate.DATASET_PATH),
            "--results-dir", str(run.DEFAULT_RESULTS_DIR / "execution_gate"),
        ])
        self.assertIn("[1/2] Starting Intent classification", output.getvalue())
        self.assertIn("[2/2] Starting Execution decisions", output.getvalue())

    def test_shared_and_suite_specific_overrides(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("evaluator.run.main", return_value=0) as intent, patch(
                "evaluator.execution_gate.main", return_value=0
            ) as execution, redirect_stdout(io.StringIO()):
                self.assertEqual(main([
                    "--config", str(root / "config.json"), "--deployment", "Decision-1",
                    "--workers", "3",
                    "--intent-dataset", str(root / "intent.jsonl"),
                    "--execution-dataset", str(root / "execution.jsonl"),
                    "--results-dir", str(root / "results"),
                ]), 0)
            shared = ["--config", str(root / "config.json"), "--workers", "3", "--deployment", "Decision-1"]
            intent.assert_called_once_with(shared + [
                "--dataset", str(root / "intent.jsonl"), "--results-dir", str(root / "results"),
            ])
            execution.assert_called_once_with(shared + [
                "--dataset", str(root / "execution.jsonl"),
                "--results-dir", str(root / "results" / "execution_gate"),
            ])

    def test_second_suite_runs_after_failure_and_exit_status_is_preserved(self):
        for first, second, expected in ((1, 0, 1), (2, 0, 2), (0, 1, 1), (1, 2, 2)):
            with self.subTest(first=first, second=second), patch(
                "evaluator.run.main", return_value=first
            ), patch("evaluator.execution_gate.main", return_value=second) as execution, redirect_stdout(
                io.StringIO()
            ):
                self.assertEqual(main([]), expected)
                execution.assert_called_once()

    def test_keyboard_interrupt_does_not_start_second_suite(self):
        with patch("evaluator.run.main", side_effect=KeyboardInterrupt), patch(
            "evaluator.execution_gate.main"
        ) as execution, redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                main([])
            execution.assert_not_called()

    def test_real_suite_entrypoints_persist_separate_outputs(self):
        intent_case = load_jsonl(run.DATASET_PATH)[0]
        execution_case = load_jsonl(execution_gate.DATASET_PATH)[0]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                json.dumps({"deployment_names": ["test-model"]}), encoding="utf-8"
            )
            for name, case in (("intent", intent_case), ("execution", execution_case)):
                (root / f"{name}.jsonl").write_text(json.dumps(case) + "\n", encoding="utf-8")
            with patch("evaluator.run._foundry_client"), patch(
                "evaluator.execution_gate._foundry_client"
            ), patch("evaluator.run._predict", return_value=(
                json.dumps(intent_case["expected"]), 1, 1, None,
            )), patch("evaluator.execution_gate.predict", return_value=(
                json.dumps(execution_case["expected"]), 1, 1, None,
            )), redirect_stdout(io.StringIO()):
                self.assertEqual(main([
                    "--config", str(root / "config.json"),
                    "--intent-dataset", str(root / "intent.jsonl"),
                    "--execution-dataset", str(root / "execution.jsonl"),
                    "--results-dir", str(root / "results"),
                ]), 0)
            intent_results = list((root / "results").glob("run-*.jsonl"))
            execution_results = list((root / "results" / "execution_gate").glob("run-*.jsonl"))
            self.assertEqual(len(intent_results), 1)
            self.assertEqual(len(execution_results), 1)
            self.assertEqual(load_jsonl(intent_results[0])[0]["actual"], intent_case["expected"])
            self.assertEqual(load_jsonl(execution_results[0])[0]["actual"], execution_case["expected"])
            self.assertEqual(len(list((root / "results").glob("summary-*.json"))), 1)
            self.assertEqual(len(list((root / "results" / "execution_gate").glob("summary-*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
