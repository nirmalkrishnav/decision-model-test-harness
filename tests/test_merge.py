import copy
import unittest

from evaluator.merge import merge_records


def record(deployment, case_id="BENCH-001", **updates):
    return {
        "deployment": deployment,
        "expected": {"test_case_id": case_id, "intent": "unsupported", "entities": {}},
        "question": "Hello",
        "repeat_index": 1,
        **updates,
    }


class MergeTests(unittest.TestCase):
    def test_only_selected_model_replaced_and_order_preserved(self):
        gpt = record("gpt", error=None)
        old = record("Decision-1", error={"type": "422"})
        new = record("Decision-1", error=None)
        previous = [gpt, old]
        before = copy.deepcopy(previous)
        merged = merge_records(previous, [new], "Decision-1")
        self.assertEqual(merged, [gpt, new])
        self.assertIs(merged[0], gpt)
        self.assertEqual(previous, before)

    def test_rejects_missing_extra_duplicate_or_changed_records(self):
        previous = [record("gpt"), record("Decision-1")]
        invalid_runs = [
            [],
            [record("gpt")],
            [record("Decision-1", "BENCH-002")],
            [record("Decision-1"), record("Decision-1")],
            [record("Decision-1", question="changed question")],
            [record("Decision-1", context=[{"role": "user", "content": "changed history"}])],
        ]
        for replacement in invalid_runs:
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                merge_records(previous, replacement, "Decision-1")


if __name__ == "__main__":
    unittest.main()
