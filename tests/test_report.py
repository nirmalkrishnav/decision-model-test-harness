import unittest
from copy import deepcopy
from unittest.mock import patch

from scripts.build_experiment_report import (
    INTENT_RUN, RUN, load_evidence, load_intent_evidence, render,
)


class ExperimentReportTests(unittest.TestCase):
    def test_completed_run_reproduces_and_renders_safety_context(self):
        records, cases, models, grouped, scores = load_evidence()
        self.assertEqual(len(records), 270)
        self.assertEqual(len(cases), 90)
        self.assertEqual(
            {model: scores[model]["safety"]["unsafe_allow_count"] for model in models},
            {"gpt-5.4": 1, "Decision-1": 4, "gpt-6.1-terra": 0},
        )
        document = render()
        self.assertEqual(document.count('<svg role="img"'), 5)
        self.assertEqual(document.count("<details>"), 34)
        self.assertIn("735</strong>classification calls", document)
        self.assertIn("5 parallel workers", document)
        self.assertIn("154/155", document)
        self.assertIn("138/155", document)
        self.assertIn("87/90", document)
        self.assertIn("All 19 incorrect intent predictions", document)
        self.assertIn("All 15 incorrect execution predictions", document)
        self.assertIn("We can look at MarketNest and Basketly.", document)
        self.assertNotIn("There is no full 155-case", document)
        self.assertIn("Unsafe allow / expected block", document)
        self.assertIn("0</strong>submitted programs executed", document)
        self.assertIn("not proof of a zero underlying rate", document)
        self.assertNotIn("<script", document)
        self.assertNotIn('src="http', document)

    def test_mutated_evidence_is_rejected(self):
        records, *_ = load_evidence()
        changed = deepcopy(records)
        changed[0]["state"]["code"] = "changed code"
        from scripts.build_experiment_report import load_jsonl

        with patch(
            "scripts.build_experiment_report.load_jsonl",
            side_effect=lambda path: changed if path == RUN else load_jsonl(path),
        ):
            with self.assertRaisesRegex(ValueError, "Run differs"):
                load_evidence()

    def test_intent_evidence_reproduces_and_rejects_context_mutation(self):
        _, _, models, _, _ = load_evidence()
        records, cases, _, scores = load_intent_evidence(models)
        self.assertEqual((len(records), len(cases)), (465, 155))
        self.assertAlmostEqual(scores["Decision-1"]["intent"]["accuracy"], 138 / 155)
        changed = deepcopy(records)
        next(row for row in changed if row.get("context"))["context"][0]["content"] = "changed"
        from scripts.build_experiment_report import load_jsonl

        with patch(
            "scripts.build_experiment_report.load_jsonl",
            side_effect=lambda path: changed if path == INTENT_RUN else load_jsonl(path),
        ):
            with self.assertRaisesRegex(ValueError, "Intent run differs"):
                load_intent_evidence(models)

    def test_intent_summary_and_duplicate_predictions_are_rejected(self):
        _, _, models, _, _ = load_evidence()
        records, *_ = load_intent_evidence(models)
        from scripts.build_experiment_report import load_jsonl

        for duplicate in (True, False):
            with self.subTest(duplicate=duplicate):
                changed = deepcopy(records)
                if duplicate:
                    changed[1] = deepcopy(changed[0])
                else:
                    changed[0]["input_tokens"] += 1
                with patch(
                    "scripts.build_experiment_report.load_jsonl",
                    side_effect=lambda path: changed if path == INTENT_RUN else load_jsonl(path),
                ):
                    with self.assertRaisesRegex(ValueError, "Duplicate intent|does not reproduce"):
                        load_intent_evidence(models)


if __name__ == "__main__":
    unittest.main()
