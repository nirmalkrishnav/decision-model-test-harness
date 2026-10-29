import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from evaluator.decision import predict
from evaluator.policy import POLICY_PATH, ROOT, load_policy
from evaluator.run import SCHEMA_PATH, _prompt


class CompactPolicyTests(unittest.TestCase):
    def test_policy_matches_reference_labels_and_precedence_without_full_rules(self):
        taxonomy_path = ROOT / "test_data" / "taxonomy" / "intents.yaml"
        taxonomy = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        policy = load_policy()
        definitions = policy["intent_taxonomy"]["definitions"]
        self.assertEqual(set(definitions), {entry["name"] for entry in taxonomy["intents"]})
        self.assertEqual(set(definitions), set(schema["properties"]["intent"]["enum"]))
        self.assertEqual(policy["intent_taxonomy"]["precedence"], taxonomy["precedence"])
        self.assertLess(len(json.dumps(policy)), 5000)
        self.assertNotIn("context_rules", policy)
        for field in ("include_when", "exclude_when", "examples", "typical_metrics", "entities"):
            self.assertNotIn(f'"{field}"', json.dumps(policy))
        contextual = load_policy(include_context=True)
        self.assertTrue(contextual["context_rules"])
        del contextual["context_rules"]
        self.assertEqual(contextual, policy)

    def test_same_policy_reaches_both_adapters_and_context_is_conditional(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        for context in (None, [], [{"role": "user", "content": "Show sales for FreshFizz."}]):
            with self.subTest(context=context):
                client = Mock()
                client.system_one.return_value = SimpleNamespace(
                    choices={"intent": SimpleNamespace(choice="performance_analysis")},
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                )
                predict(client, "What about ColaMax?", "BENCH-001", schema, context)
                state = dict(client.system_one.call_args.kwargs["state"])
                del state["question"], state["context"]
                expected = load_policy(include_context=bool(context))
                self.assertEqual(state, expected)
                self.assertEqual(
                    client.system_one.call_args.kwargs["questions"]["intent"].criteria,
                    expected["intent_taxonomy"]["definitions"],
                )
                prompt = _prompt("What about ColaMax?", "BENCH-001", schema, context)
                self.assertIn(json.dumps(expected, ensure_ascii=False), prompt)
                self.assertEqual('"context_rules"' in prompt, bool(context))

    def test_invalid_runtime_policies_fail_explicitly(self):
        valid = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
        invalid = [
            [], {}, {**valid, "version": ""},
            {**valid, "definitions": {}},
            {**valid, "precedence": valid["precedence"][:-1]},
            {**valid, "boundary_rules": []},
            {**valid, "context_rules": [""]},
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "policy.yml"
            with patch("evaluator.policy.POLICY_PATH", path):
                with self.assertRaisesRegex(ValueError, "Unable to read runtime policy"):
                    load_policy()
                for document in invalid:
                    path.write_text(yaml.safe_dump(document), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_policy()


if __name__ == "__main__":
    unittest.main()
