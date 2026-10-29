import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from typesafe_sdk import Choice

from evaluator.decision import PROMPT_VERSION, predict
from evaluator.metrics import evaluate_records, load_jsonl
from evaluator.run import DATASET_PATH, SCHEMA_PATH, _load_cases, _predict, run


class DecisionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.client = Mock()
        self.client.system_one.return_value = SimpleNamespace(
            choices={"intent": SimpleNamespace(choice="performance_analysis")},
            usage=SimpleNamespace(input_tokens=42, output_tokens=1),
        )

    def test_only_intent_question_and_no_entity_catalog_for_every_case(self):
        for case in _load_cases(DATASET_PATH, self.schema):
            output, *usage = predict(
                self.client, case["question"], case["expected"]["test_case_id"],
                self.schema, case.get("context"),
            )
            request = self.client.system_one.call_args.kwargs
            with self.subTest(case=case["expected"]["test_case_id"]):
                self.assertEqual(set(request["questions"]), {"intent"})
                self.assertIsInstance(request["questions"]["intent"], Choice)
                self.assertEqual(set(request["state"]), {
                    "question", "context", "intent_taxonomy", "boundary_rules", "version",
                } | ({"context_rules"} if case.get("context") else set()))
                self.assertEqual(request["state"]["context"], case.get("context", []))
                self.assertNotIn("annotation_rules", request["state"])
                self.assertNotIn("entities", json.loads(output))
                self.assertEqual(usage, [42, 1, None])

    def test_missing_usage_is_explicit_failure(self):
        self.client.system_one.return_value.usage.input_tokens = None
        with self.assertRaisesRegex(ValueError, "token usage"):
            predict(self.client, "Show sales.", "CPG-001", self.schema)

    def test_decision_dispatch_never_uses_chat_completions(self):
        expected = ("{}", 10, 1, None)
        with patch("evaluator.decision.predict", return_value=expected) as typed:
            self.assertEqual(_predict(self.client, "Decision-1", "question", "CPG-001", {}), expected)
        typed.assert_called_once_with(self.client, "question", "CPG-001", {})
        self.client.chat.completions.create.assert_not_called()

    def test_runner_and_metrics_exclude_entities_including_failures(self):
        case = _load_cases(DATASET_PATH, self.schema)[0]
        with TemporaryDirectory() as directory:
            path, summary_path, failures = run(
                {"deployment_names": ["Decision-1"]}, [case], self.schema, self.client,
                Path(directory),
            )
            records = load_jsonl(path)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))["Decision-1"]
        self.assertEqual(failures, 0)
        self.assertEqual(records[0]["adapter_prompt_version"], PROMPT_VERSION)
        self.assertIs(records[0]["entity_evaluation"], False)
        self.assertTrue(records[0]["schema_valid"])
        self.assertEqual(summary["intent"]["accuracy"], 1.0)
        self.assertIsNone(summary["entities"]["exact_match"])
        self.assertIsNone(summary["entities"]["micro"])
        self.assertIsNone(summary["entities"]["per_type"])
        self.assertEqual(summary["entities"]["evaluated_count"], 0)
        self.assertIsNone(summary["by_category"][case["category"]]["entities"]["exact_match"])

        labels = self.schema["properties"]["intent"]["enum"]
        names = ["brand"]
        historical_output = {**case["expected"], "entities": {"brand": []}}
        historical = {**records[0], "expected": historical_output, "actual": historical_output}
        del historical["entity_evaluation"]
        historical_score = evaluate_records([historical], labels, names)
        self.assertEqual(historical_score["entities"]["exact_match"], 1.0)
        self.assertNotIn("excluded_count", historical_score["entities"])
        mixed = evaluate_records(records + [historical], labels, names)
        self.assertEqual(mixed["entities"]["exact_match"], 1.0)
        self.assertEqual(mixed["entities"]["evaluated_count"], 1)
        failed = {**records[0], "actual": None, "schema_valid": False, "error": {"message": "failed"}}
        score = evaluate_records([failed], labels, names)
        self.assertIsNone(score["entities"]["exact_match"])
        self.assertEqual(score["failure_rate_pct"], 100)


if __name__ == "__main__":
    unittest.main()
