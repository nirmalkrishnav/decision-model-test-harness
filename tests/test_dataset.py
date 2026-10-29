import json
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import jsonschema

from evaluator.metrics import evaluate_records, load_jsonl
from evaluator.run import DATASET_PATH, ROOT, SCHEMA_PATH, _load_cases, _predict, _prompt, run
from scripts.build_dataset import COUNTS, build_cases


class BenchmarkDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.cases = _load_cases(DATASET_PATH, cls.schema)

    def test_exact_counts_schema_ids_and_reproducibility(self):
        self.assertEqual(len(self.cases), 155)
        self.assertEqual(Counter(case["category"] for case in self.cases), COUNTS)
        self.assertEqual(self.cases, build_cases())
        self.assertEqual(len({case["expected"]["test_case_id"] for case in self.cases}), 155)
        self.assertEqual(
            len({(case["question"], json.dumps(case.get("context", []))) for case in self.cases}),
            155,
        )
        self.assertEqual(
            {case["expected"]["intent"] for case in self.cases},
            set(self.schema["properties"]["intent"]["enum"]),
        )
        self.assertTrue(set(COUNTS).isdisjoint({
            "multiple_entities", "entity_normalization", "missing_null_entities",
            "datasource_selection",
        }))
        self.assertFalse((ROOT / "test_data" / "cases" / "benchmark_250.jsonl").exists())

    def test_labels_are_intent_only_with_draft_metadata(self):
        for case in self.cases:
            with self.subTest(case=case["expected"]["test_case_id"]):
                self.assertEqual(case["spec_version"], "0.1.0")
                self.assertEqual(case["annotation_status"], "draft")
                self.assertTrue(case["rationale"])
                self.assertEqual(case["evaluation_scope"], "intent_only")
                self.assertNotIn("entities", case["expected"])
                if case["category"] == "multi_turn_context":
                    self.assertTrue(case["context"])
                    self.assertGreater(case["expected"]["turn_index"], 1)

    def test_intent_schema_rejects_entity_output_and_bad_intents(self):
        expected = self.cases[0]["expected"]
        for invalid in (
            {**expected, "entities": {}},
            {**expected, "intent": "not_an_intent"},
        ):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(invalid, self.schema)
        seeds = _load_cases(ROOT / "test_data" / "cases" / "seed_cases.jsonl", self.schema)
        self.assertTrue(all("entities" not in seed["expected"] for seed in seeds))

    def test_paraphrase_groups_have_equivalent_labels(self):
        groups = defaultdict(list)
        for case in self.cases:
            if "paraphrase_group" in case:
                groups[case["paraphrase_group"]].append(case)
        self.assertEqual(len(groups), 5)
        for cases in groups.values():
            self.assertEqual(len(cases), 4)
            self.assertEqual(len({
                json.dumps({key: value for key, value in case["expected"].items() if key != "test_case_id"},
                           sort_keys=True)
                for case in cases
            }), 1)

    def test_invalid_context_and_turn_index_are_rejected(self):
        for context, turn_index in [
            ([{"role": "system", "content": "not allowed"}], 2),
            ([{"role": "user", "content": "previous question"}], 3),
        ]:
            case = {**self.cases[0], "context": context,
                    "expected": {**self.cases[0]["expected"], "turn_index": turn_index}}
            with TemporaryDirectory() as temporary_directory:
                path = Path(temporary_directory) / "invalid.jsonl"
                path.write_text(json.dumps(case) + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    _load_cases(path, self.schema)

    def test_both_model_routes_receive_context_without_gold_labels(self):
        case = next(case for case in self.cases if "context" in case)
        context = case["context"]
        prompt = _prompt(case["question"], "BENCH-001", self.schema, context)
        self.assertIn(json.dumps(context), prompt)
        self.assertIn('"context_rules"', prompt)
        self.assertNotIn('"expected":', prompt)
        self.assertIn("Do not extract entities or select datasources.", prompt)
        self.assertNotIn('"entity_taxonomy"', prompt)
        self.assertNotIn('"entities":', prompt)
        with patch("evaluator.decision.predict", return_value=("{}", 1, 1, None)) as typed:
            _predict(Mock(), "Decision-1", case["question"], "BENCH-001", self.schema, context)
            self.assertEqual(typed.call_args.kwargs["context"], context)

    def test_full_dataset_offline_runner_preserves_categories_and_context(self):
        expected_by_id = {case["expected"]["test_case_id"]: case["expected"] for case in self.cases}

        def fake_predict(client, deployment, question, case_id, schema, **kwargs):
            return json.dumps(expected_by_id[case_id]), 10, 2, 0

        with TemporaryDirectory() as temporary_directory, patch(
            "evaluator.run._predict", side_effect=fake_predict
        ) as predictor:
            result_path, summary_path, failures = run(
                {"deployment_names": ["offline-fixture"]}, self.cases, self.schema, Mock(),
                Path(temporary_directory),
            )
            records = load_jsonl(result_path)
            report = json.loads(summary_path.read_text(encoding="utf-8"))["offline-fixture"]
        self.assertEqual(failures, 0)
        self.assertEqual(len(records), 155)
        self.assertEqual(Counter(row["category"] for row in records), COUNTS)
        self.assertEqual(sum(bool(row.get("context")) for row in records), 30)
        self.assertEqual(sum(bool(call.kwargs.get("context")) for call in predictor.call_args_list), 30)
        self.assertEqual(
            {category: score["question_count"] for category, score in report["by_category"].items()},
            COUNTS,
        )
        self.assertEqual(report["intent"]["accuracy"], 1.0)
        self.assertIsNone(report["entities"]["exact_match"])
        self.assertTrue(all(row["entity_evaluation"] is False for row in records))
        self.assertEqual(report, evaluate_records(
            records, self.schema["properties"]["intent"]["enum"],
            [],
        ))


if __name__ == "__main__":
    unittest.main()
