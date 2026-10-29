import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from evaluator.metrics import calculate_cost, evaluate_records, load_jsonl
from evaluator.run import _foundry_client, _load_cases, run


class _FakeClient:
    def __init__(self, prediction):
        message = SimpleNamespace(content=json.dumps(prediction))
        usage = SimpleNamespace(
            prompt_tokens=5,
            completion_tokens=6,
            prompt_tokens_details=SimpleNamespace(cached_tokens=2),
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
        )
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: response)
        )


class EvaluateRecordsTests(unittest.TestCase):
    def test_decision_client_uses_separate_endpoint_for_both_name_casings(self):
        with (
            patch("evaluator.run.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "AZURE_AI_FOUNDRY_ENDPOINT": "https://gpt.example/openai/v1/",
                    "AZURE_AI_FOUNDRY_DECISION_ENDPOINT": "https://decision.example/providers/microsoft",
                    "AZURE_AI_FOUNDRY_API_KEY": "test-key",
                },
                clear=True,
            ),
            patch("typesafe_sdk.TypeSafeClient") as factory,
        ):
            for name in ("decision-1", "Decision-1"):
                with self.subTest(deployment=name):
                    factory.reset_mock()
                    _foundry_client(name)
                    factory.assert_called_once_with(
                        api_key="test-key",
                        base_url="https://decision.example/providers/microsoft",
                        model=name,
                        timeout=60,
                    )

    def test_decision_endpoint_is_required_without_gpt_fallback(self):
        with (
            patch("evaluator.run.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "AZURE_AI_FOUNDRY_ENDPOINT": "https://gpt.example/openai/v1/",
                    "AZURE_AI_FOUNDRY_API_KEY": "test-key",
                },
                clear=True,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "AZURE_AI_FOUNDRY_DECISION_ENDPOINT"):
                _foundry_client("Decision-1")

    def test_foundry_client_loads_project_root_dotenv(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".env").write_text(
                "AZURE_AI_FOUNDRY_ENDPOINT=https://foundry.example/openai/v1/\n"
                "AZURE_AI_FOUNDRY_API_KEY=dotenv-test-key\n",
                encoding="utf-8",
            )
            with (
                patch("evaluator.run.ROOT", root),
                patch.dict("os.environ", {}, clear=True),
                patch("openai.OpenAI") as factory,
            ):
                self.assertIs(_foundry_client(), factory.return_value)
                factory.assert_called_once_with(
                    api_key="dotenv-test-key",
                    base_url="https://foundry.example/openai/v1/",
                    default_headers={"api-key": "dotenv-test-key"},
                )

    def test_foundry_client_preserves_environment_overrides(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".env").write_text(
                "AZURE_AI_FOUNDRY_ENDPOINT=https://dotenv.example/openai/v1/\n"
                "AZURE_AI_FOUNDRY_API_KEY=dotenv-test-key\n",
                encoding="utf-8",
            )
            with (
                patch("evaluator.run.ROOT", root),
                patch.dict(
                    "os.environ",
                    {
                        "AZURE_AI_FOUNDRY_ENDPOINT": "https://environment.example/openai/v1",
                        "AZURE_AI_FOUNDRY_API_KEY": "environment-test-key",
                    },
                    clear=True,
                ),
                patch("openai.OpenAI") as factory,
            ):
                _foundry_client()
                factory.assert_called_once_with(
                    api_key="environment-test-key",
                    base_url="https://environment.example/openai/v1/",
                    default_headers={"api-key": "environment-test-key"},
                )

    def test_seed_ground_truth_matches_canonical_schema(self):
        root = Path(__file__).resolve().parent.parent
        schema = json.loads(
            (root / "test_data" / "schemas" / "intent_output.schema.json").read_text(encoding="utf-8")
        )
        cases = _load_cases(root / "test_data" / "cases" / "seed_cases.jsonl", schema)
        self.assertEqual(len(cases), 10)
        self.assertEqual(
            {case["expected"]["intent"] for case in cases},
            set(schema["properties"]["intent"]["enum"]),
        )

    def test_runner_writes_a_valid_record_with_usage(self):
        root = Path(__file__).resolve().parent.parent
        schema = json.loads(
            (root / "test_data" / "schemas" / "intent_output.schema.json").read_text(encoding="utf-8")
        )
        cases = _load_cases(root / "test_data" / "cases" / "seed_cases.jsonl", schema)[:1]
        with TemporaryDirectory() as temporary_directory:
            result_path, summary_path, failures = run(
                {"deployment_names": ["fake-model"], "repeat_count": 1},
                cases,
                schema,
                _FakeClient(cases[0]["expected"]),
                Path(temporary_directory),
            )
            result = load_jsonl(result_path)[0]
            summary = json.loads(summary_path.read_text(encoding="utf-8"))["fake-model"]
        self.assertEqual(failures, 0)
        self.assertTrue(result["schema_valid"])
        self.assertEqual(result["input_tokens"], 5)
        self.assertEqual(result["output_tokens"], 6)
        self.assertEqual(result["cached_input_tokens"], 2)
        self.assertEqual(summary["intent"]["accuracy"], 1.0)
        self.assertEqual(summary["tokens"]["input_total"], 5)
        self.assertEqual(summary["tokens"]["output_total"], 6)
        self.assertEqual(summary["tokens"]["cached_input_total"], 2)

    def test_runner_routes_each_deployment_to_its_own_client(self):
        root = Path(__file__).resolve().parent.parent
        schema = json.loads(
            (root / "test_data" / "schemas" / "intent_output.schema.json").read_text(encoding="utf-8")
        )
        cases = _load_cases(root / "test_data" / "cases" / "seed_cases.jsonl", schema)[:1]
        clients = {"gpt-5.4": object(), "Decision-1": object()}
        with (
            TemporaryDirectory() as temporary_directory,
            patch(
                "evaluator.run._predict",
                return_value=(json.dumps(cases[0]["expected"]), 5, 6, 0),
            ) as predict,
        ):
            _, _, failures = run(
                {"deployment_names": list(clients)},
                cases,
                schema,
                clients,
                Path(temporary_directory),
            )
        self.assertEqual(failures, 0)
        self.assertEqual(
            [(call.args[0], call.args[1]) for call in predict.call_args_list],
            [(clients[name], name) for name in clients],
        )

    def test_usage_cost_includes_cached_tokens_and_marks_unknown_rates(self):
        gpt_cost = calculate_cost(
            {
                "input_tokens": 2_000_000,
                "output_tokens": 1_000_000,
                "cached_input_tokens": 100_000,
            },
            {
                "input_usd_per_1m_tokens": 2.5,
                "output_usd_per_1m_tokens": 15.0,
                "cached_input_usd_per_1m_tokens": 0.25,
            },
        )
        self.assertAlmostEqual(gpt_cost["cost_usd"], 19.775)
        self.assertTrue(gpt_cost["cost_estimate_complete"])

        decision_cost = calculate_cost(
            {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000,
                "cached_input_tokens": 0,
            },
            {
                "input_usd_per_1m_tokens": 0.042,
                "output_usd_per_1m_tokens": None,
            },
        )
        self.assertAlmostEqual(decision_cost["input_cost_usd"], 0.042)
        self.assertIsNone(decision_cost["output_cost_usd"])
        self.assertIsNone(decision_cost["cost_usd"])
        self.assertFalse(decision_cost["cost_estimate_complete"])

        failed_call_cost = calculate_cost(
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": None,
                "error": {"type": "NotFoundError"},
            },
            {
                "input_usd_per_1m_tokens": 0.042,
                "output_usd_per_1m_tokens": None,
            },
        )
        self.assertIsNone(failed_call_cost["cost_usd"])
        self.assertFalse(failed_call_cost["cost_estimate_complete"])

    def test_metrics_keep_intent_and_entity_accuracy_separate(self):
        entities = {"brand": ["FreshFizz"], "channel": []}
        records = [
            {
                "deployment": "model-a",
                "expected": {
                    "test_case_id": "CPG-001",
                    "intent": "promotion_analysis",
                    "entities": entities,
                },
                "actual": {
                    "test_case_id": "CPG-001",
                    "intent": "promotion_analysis",
                    "entities": {"brand": ["ColaMax"], "channel": []},
                },
                "schema_valid": True,
                "latency_ms": 100,
                "input_tokens": 10,
                "output_tokens": 5,
                "error": None,
            }
        ]
        result = evaluate_records(
            records,
            ["promotion_analysis", "unsupported"],
            ["brand", "channel"],
        )
        self.assertEqual(result["intent"]["accuracy"], 1.0)
        self.assertEqual(result["entities"]["exact_match"], 0.0)
        self.assertEqual(result["entities"]["micro"]["f1"], 0.0)
        self.assertEqual(result["schema_valid_pct"], 100.0)
        self.assertIsNone(result["repeat_consistency"])
        self.assertIsNone(result["cost_usd"])

    def test_counts_failures_as_wrong_and_measures_repeat_consistency(self):
        expected = {
            "test_case_id": "CPG-001",
            "intent": "unsupported",
            "entities": {"brand": []},
        }
        records = [
            {
                "deployment": "model-a",
                "expected": expected,
                "actual": None,
                "schema_valid": False,
                "latency_ms": 100,
                "input_tokens": 0,
                "output_tokens": 0,
                "error": {"type": "ValueError"},
            },
            {
                "deployment": "model-a",
                "expected": expected,
                "actual": {
                    "test_case_id": "CPG-001",
                    "intent": "unsupported",
                    "entities": {"brand": []},
                },
                "schema_valid": True,
                "latency_ms": 200,
                "input_tokens": 10,
                "output_tokens": 5,
                "error": None,
            },
        ]
        result = evaluate_records(records, ["unsupported"], ["brand"])
        self.assertEqual(result["failure_rate_pct"], 50.0)
        self.assertEqual(result["intent"]["accuracy"], 0.5)
        self.assertEqual(result["entities"]["exact_match"], 0.5)
        self.assertEqual(result["repeat_consistency"], 0.0)
        self.assertEqual(result["latency_ms"]["p50"], 150.0)


if __name__ == "__main__":
    unittest.main()
