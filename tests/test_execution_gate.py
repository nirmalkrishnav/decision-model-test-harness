import json
import io
import unittest
from collections import Counter
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from evaluator.execution_gate import (
    DATASET_PATH, load_cases, load_setup, predict, run, summarize,
)
from evaluator.metrics import load_jsonl
from scripts.build_execution_dataset import build_cases


class ExecutionGateTests(unittest.TestCase):
    def setUp(self):
        self.policy, self.prompts, self.schema = load_setup()
        self.cases = load_cases(DATASET_PATH, self.schema)

    def test_dataset_reproducibility_and_all_permission_combinations(self):
        self.assertEqual(self.cases, build_cases())
        self.assertEqual(len(self.cases), 90)
        self.assertEqual(len({case["expected"]["test_case_id"] for case in self.cases}), 90)
        self.assertEqual(Counter(case["state"]["language"] for case in self.cases), {"python": 44, "sql": 46})
        profiles = {"none", "R", "W", "U", "RW", "RU", "WU", "RWU"}
        matrix = [case for case in self.cases if case["category"].startswith("permission_")]
        self.assertEqual(len(matrix), 64)
        for language in ("python", "sql"):
            for operation, required in (
                ("read", {"read"}), ("create", {"create"}), ("update", {"update"}),
                ("read_update", {"read", "update"}),
            ):
                group = [case for case in matrix if case["state"]["language"] == language
                         and case["category"] == f"permission_{operation}"]
                self.assertEqual({case["permission_profile"] for case in group}, profiles)
                for case in group:
                    grant = case["state"]["permissions"][0]
                    expected = "allow" if all(grant[key] for key in required) else "block"
                    self.assertEqual(case["expected"]["decision"], expected)

    def test_adapter_payloads_do_not_leak_labels_or_execute_code(self):
        case = deepcopy(self.cases[0])
        case["state"]["code"] = "raise RuntimeError('THIS CODE MUST NEVER RUN')"
        case["rationale"] = "GOLD_RATIONALE_NOT_FOR_MODEL"
        client = Mock()
        client.system_one.return_value = SimpleNamespace(
            choices={"decision": SimpleNamespace(choice="block")},
            usage=SimpleNamespace(input_tokens=42, output_tokens=1),
        )
        raw, *usage = predict(client, "Decision-1", case, self.policy, self.prompts, self.schema)
        self.assertEqual(json.loads(raw), {"test_case_id": "EXEC-001", "decision": "block"})
        self.assertEqual(usage, [42, 1, None])
        request = client.system_one.call_args.kwargs
        self.assertEqual(set(request["questions"]), {"decision"})
        self.assertEqual(request["state"], {"policy": self.policy, "submission": case["state"]})
        self.assertNotIn("GOLD_RATIONALE_NOT_FOR_MODEL", json.dumps(request["state"]))
        client.chat.completions.create.assert_not_called()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw))],
            usage=SimpleNamespace(prompt_tokens=42, completion_tokens=1, prompt_tokens_details=None),
        )
        predict(client, "gpt-5.4", case, self.policy, self.prompts, self.schema)
        request = client.chat.completions.create.call_args.kwargs
        prompt = request["messages"][1]["content"]
        self.assertIn(json.dumps(case["state"]), prompt)
        self.assertIn(json.dumps(self.policy), prompt)
        self.assertNotIn("GOLD_RATIONALE_NOT_FOR_MODEL", prompt)
        self.assertNotIn('"expected"', prompt)

    def test_full_mocked_run_and_metrics(self):
        def fake_predict(client, deployment, case, policy, prompts, schema):
            return json.dumps(case["expected"]), 42, 1, 0

        with TemporaryDirectory() as directory, patch(
            "evaluator.execution_gate.predict", side_effect=fake_predict
        ):
            path, summary_path, failures = run(
                {"deployment_names": ["Decision-1", "gpt-5.4"], "repeat_count": 2},
                self.cases, {"Decision-1": Mock(), "gpt-5.4": Mock()}, Path(directory),
            )
            records = load_jsonl(path)
            summaries = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 360)
        self.assertEqual(failures, 0)
        for score in summaries.values():
            self.assertEqual(score["decision"]["accuracy"], 1)
            self.assertEqual(score["repeat_consistency"], 1)
            self.assertEqual(score["safety"]["unsafe_allow_count"], 0)
            self.assertEqual(score["safety"]["false_block_count"], 0)
            self.assertEqual(set(score["by_language"]), {"python", "sql"})
            self.assertEqual(len(score["by_permission_profile"]), 8)

    def test_wrong_decisions_and_failures_are_distinguished(self):
        cases = [
            next(case for case in self.cases if case["expected"]["decision"] == label)
            for label in ("block", "allow", "block")
        ]
        rows = []
        for case, decision in zip(cases, ("allow", "block", None)):
            rows.append({
                **case, "actual": {"decision": decision} if decision else None,
                "schema_valid": decision is not None, "deployment": "test",
                "error": None if decision else {"message": "failed"},
            })
        score = summarize(rows, {})
        self.assertEqual(score["decision"]["accuracy"], 0)
        self.assertEqual(score["safety"]["unsafe_allow_count"], 1)
        self.assertEqual(score["safety"]["unsafe_allow_rate"], 0.5)
        self.assertEqual(score["safety"]["false_block_rate"], 1)
        self.assertAlmostEqual(score["failure_rate_pct"], 100 / 3)

    def test_invalid_predictions_and_api_errors_are_persisted(self):
        outputs = [
            '{"test_case_id": "EXEC-999", "decision": "allow"}',
            '{"test_case_id": "EXEC-001", "decision": "maybe"}',
            "not JSON",
        ]
        for raw in outputs:
            with self.subTest(raw=raw), TemporaryDirectory() as directory, patch(
                "evaluator.execution_gate.predict", return_value=(raw, 5, 1, None)
            ):
                path, _, failures = run({"deployment_names": ["test"]}, self.cases[:1],
                                        {"test": Mock()}, Path(directory))
                row = load_jsonl(path)[0]
                self.assertEqual(failures, 1)
                self.assertFalse(row["schema_valid"])
                self.assertEqual(row["raw_output"], raw)
        with TemporaryDirectory() as directory, patch(
            "evaluator.execution_gate.predict", side_effect=TimeoutError("timeout")
        ):
            path, _, failures = run({"deployment_names": ["test"]}, self.cases[:1],
                                    {"test": Mock()}, Path(directory))
            self.assertEqual(failures, 1)
            self.assertEqual(load_jsonl(path)[0]["error"]["type"], "TimeoutError")

    def test_bad_grants_and_duplicate_cases_rejected(self):
        case = deepcopy(self.cases[0])
        case["state"]["permissions"][0]["read"] = "yes"
        for cases in ([case], [self.cases[0], self.cases[0]]):
            with TemporaryDirectory() as directory:
                path = Path(directory) / "cases.jsonl"
                path.write_text("".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_cases(path, self.schema)

    def test_progress_is_immediate_throttled_and_counts_repeats_and_failures(self):
        cases = self.cases[:10] + self.cases[32:34]
        call_count = 0
        output = io.StringIO()

        def fake_predict(client, deployment, case, policy, prompts, schema):
            nonlocal call_count
            if call_count == 0:
                self.assertIn("completed 0/24, pending 24", output.getvalue())
                self.assertIn("python: completed 0, pending 20", output.getvalue())
                self.assertIn("sql: completed 0, pending 4", output.getvalue())
            call_count += 1
            if call_count == 1:
                raise TimeoutError("simulated timeout")
            return json.dumps(case["expected"]), 42, 1, None

        with TemporaryDirectory() as directory, redirect_stdout(output), patch(
            "evaluator.execution_gate.predict", side_effect=fake_predict
        ):
            _, _, failures = run(
                {"deployment_names": ["model-a", "model-b"], "repeat_count": 2},
                cases, {"model-a": Mock(), "model-b": Mock()}, Path(directory), workers=1,
            )
        log = output.getvalue()
        self.assertEqual(failures, 1)
        self.assertIn("model-a: completed 24/24, pending 0, errors 1", log)
        self.assertIn("model-b: completed 24/24, pending 0, errors 0", log)
        self.assertIn("Overall 48/48, pending 0", log)
        self.assertIn("ERROR model-a EXEC-001: TimeoutError: simulated timeout", log)
        for category in {case["category"] for case in cases}:
            total = sum(case["category"] == category for case in cases) * 2
            self.assertIn(f"{category} {total}/0", log)
        self.assertNotIn("EXEC-002", log)  # No per-question success spam.
        self.assertLess(len(log.splitlines()), 35)



if __name__ == "__main__":
    unittest.main()
