"""Run CPG seed cases against OpenAI-compatible Azure AI Foundry deployments."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any

import jsonschema
import yaml
from dotenv import load_dotenv

from evaluator.metrics import calculate_cost, evaluate_records, load_jsonl, validate_pricing
from evaluator.policy import load_policy
from evaluator.workers import completed_jobs, positive_workers

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
GPT_PROMPTS_PATH = ROOT / "config" / "gpt_intent_classification.yml"
DATASET_PATH = ROOT / "test_data" / "cases" / "intent_cases.jsonl"
SCHEMA_PATH = ROOT / "test_data" / "schemas" / "intent_output.schema.json"
DEFAULT_RESULTS_DIR = ROOT / "results"


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read config {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError(f"{path}: expected a JSON object")
    deployments = config.get("deployment_names")
    if not isinstance(deployments, list) or any(
        not isinstance(name, str) or not name.strip() for name in deployments
    ):
        raise ValueError(f"{path}: deployment_names must be an array of non-empty strings")
    if len(set(deployments)) != len(deployments):
        raise ValueError(f"{path}: deployment_names must not contain duplicates")
    repeat_count = config.get("repeat_count", 1)
    if not isinstance(repeat_count, int) or isinstance(repeat_count, bool) or repeat_count < 1:
        raise ValueError(f"{path}: repeat_count must be a positive integer")
    return config


def _foundry_client(deployment: str = ""):
    load_dotenv(ROOT / ".env", override=False)
    endpoint_variable = (
        "AZURE_AI_FOUNDRY_DECISION_ENDPOINT"
        if deployment.casefold() == "decision-1"
        else "AZURE_AI_FOUNDRY_ENDPOINT"
    )
    endpoint = os.environ.get(endpoint_variable, "").strip()
    api_key = os.environ.get("AZURE_AI_FOUNDRY_API_KEY", "").strip()
    if not endpoint:
        raise ValueError(f"Set {endpoint_variable} in the environment or project-root .env file.")
    if not api_key:
        raise ValueError("Set AZURE_AI_FOUNDRY_API_KEY in the environment or project-root .env file.")
    if deployment.casefold() == "decision-1":
        from typesafe_sdk import TypeSafeClient

        return TypeSafeClient(
            api_key=api_key,
            base_url=endpoint,
            model=deployment,
            timeout=60,
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The OpenAI SDK is required. Install project dependencies with pip.") from exc
    return OpenAI(
        api_key=api_key,
        base_url=endpoint.rstrip("/") + "/",
        default_headers={"api-key": api_key},
    )


def _load_gpt_prompts() -> dict[str, str]:
    try:
        prompts = yaml.safe_load(GPT_PROMPTS_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Unable to read GPT prompts {GPT_PROMPTS_PATH}: {exc}") from exc
    if not isinstance(prompts, dict) or set(prompts) != {"system_prompt", "user_prompt"}:
        raise ValueError(f"{GPT_PROMPTS_PATH}: expected system_prompt and user_prompt")
    for name, value in prompts.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{GPT_PROMPTS_PATH}: {name} must be a non-empty string")
    template = Template(prompts["user_prompt"])
    placeholders = {
        "classification_policy", "output_schema", "conversation_history", "test_case_id", "question",
    }
    if not template.is_valid() or set(template.get_identifiers()) != placeholders:
        raise ValueError(
            f"{GPT_PROMPTS_PATH}: user_prompt must use exactly these placeholders: "
            f"{', '.join(sorted(placeholders))}; escape literal dollar signs as $$"
        )
    return prompts


def _prompt(
    question: str,
    case_id: str,
    schema: dict[str, Any],
    context: list[dict[str, str]] | None = None,
    *,
    template: str | None = None,
) -> str:
    output_schema = {
        "type": "object",
        "properties": {
            "test_case_id": schema["properties"]["test_case_id"],
            "intent": schema["properties"]["intent"],
        },
        "required": ["test_case_id", "intent"],
        "additionalProperties": False,
    }
    if template is None:
        template = _load_gpt_prompts()["user_prompt"]
    return Template(template).substitute(
        classification_policy=json.dumps(load_policy(include_context=bool(context)), ensure_ascii=False),
        output_schema=json.dumps(output_schema, ensure_ascii=False),
        conversation_history=json.dumps(context or []),
        test_case_id=case_id,
        question=question,
    )


def _predict(
    client,
    deployment: str,
    question: str,
    case_id: str,
    schema: dict[str, Any],
    context: list[dict[str, str]] | None = None,
) -> tuple[str, int, int, int | None]:
    if deployment.casefold() == "decision-1":
        from evaluator.decision import predict

        if context:
            return predict(client, question, case_id, schema, context=context)
        return predict(client, question, case_id, schema)
    prompts = _load_gpt_prompts()
    response = client.chat.completions.create(
        model=deployment,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": prompts["system_prompt"],
            },
            {
                "role": "user",
                "content": _prompt(question, case_id, schema, context, template=prompts["user_prompt"]),
            },
        ],
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content:
        raise ValueError("Model returned no JSON content")
    usage = response.usage
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cached_input_tokens = getattr(prompt_details, "cached_tokens", None)
    if cached_input_tokens is not None:
        cached_input_tokens = int(cached_input_tokens)
    return content, input_tokens, output_tokens, cached_input_tokens


def _load_cases(path: Path, schema: dict[str, Any]) -> list[dict[str, Any]]:
    cases = load_jsonl(path)
    if not cases:
        raise ValueError(f"{path}: dataset contains no cases")
    seen: set[str] = set()
    for line_number, case in enumerate(cases, start=1):
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("question"), str)
            or not case["question"].strip()
        ):
            raise ValueError(f"{path}:{line_number}: each case needs a question string")
        context = case.get("context", [])
        if not isinstance(context, list) or any(
            not isinstance(turn, dict)
            or set(turn) != {"role", "content"}
            or turn["role"] not in ("user", "assistant")
            or not isinstance(turn["content"], str)
            or not turn["content"].strip()
            for turn in context
        ):
            raise ValueError(f"{path}:{line_number}: context must contain user/assistant messages")
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise ValueError(f"{path}:{line_number}: each case needs an expected object")
        try:
            jsonschema.validate(expected, schema)
        except jsonschema.ValidationError as exc:
            raise ValueError(f"{path}:{line_number}: invalid ground truth: {exc.message}") from exc
        case_id = expected["test_case_id"]
        if context and expected.get("turn_index") != 1 + sum(
            turn["role"] == "user" for turn in context
        ):
            raise ValueError(f"{path}:{line_number}: turn_index does not match conversation history")
        if case_id in seen:
            raise ValueError(f"{path}:{line_number}: duplicate test_case_id {case_id!r}")
        seen.add(case_id)
    return cases


def run(
    config: dict[str, Any],
    cases: list[dict[str, Any]],
    schema: dict[str, Any],
    client,
    results_dir: Path,
    *,
    workers: int = 10,
) -> tuple[Path, Path, int]:
    deployments: list[str] = config["deployment_names"]
    if not deployments:
        raise ValueError(
            "No deployments configured. Add names to evaluator/config.json "
            '(for example "decision-1" and "gpt-5.4").'
        )
    repeats = config.get("repeat_count", 1)
    pricing = config.get("pricing_usd_per_1m_tokens", {})
    if not isinstance(pricing, dict):
        raise ValueError("pricing_usd_per_1m_tokens must be a JSON object")
    validate_pricing(pricing)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    results_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"run-{timestamp}.jsonl"
    records = []
    failures = 0
    print(f"Intent evaluation: up to {workers} concurrent calls per model.", flush=True)

    def evaluate(job):
        deployment, case, repeat_index = job
        deployment_client = client[deployment] if isinstance(client, dict) else client
        started = time.perf_counter()
        expected = case["expected"]
        actual = raw_output = error = None
        input_tokens = output_tokens = 0
        cached_input_tokens = None
        schema_valid = False
        try:
            context_args = {"context": case["context"]} if case.get("context") else {}
            raw_output, input_tokens, output_tokens, cached_input_tokens = _predict(
                deployment_client, deployment, case["question"], expected["test_case_id"],
                schema, **context_args,
            )
            actual = json.loads(raw_output)
            if isinstance(actual, dict) and "turn_index" in expected:
                actual.setdefault("turn_index", expected["turn_index"])
                if actual["turn_index"] != expected["turn_index"]:
                    raise ValueError("Model returned the wrong turn_index")
            if actual.get("test_case_id") != expected["test_case_id"]:
                raise ValueError("Model returned a test_case_id that does not match the case")
            jsonschema.validate(actual, schema)
            schema_valid = True
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
        record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "deployment": deployment, "repeat_index": repeat_index,
            "question": case["question"], "expected": expected, "actual": actual,
            "latency_ms": (time.perf_counter() - started) * 1000,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
            "schema_valid": schema_valid, "error": error, "entity_evaluation": False,
            "workers": workers,
        }
        if raw_output is not None and not schema_valid:
            record["raw_output"] = raw_output
        if deployment.casefold() == "decision-1":
            from evaluator.decision import PROMPT_VERSION
            record["adapter_prompt_version"] = PROMPT_VERSION
        else:
            record["adapter_prompt_version"] = "intent-only-chat-v2-compact"
        for metadata in (
            "category", "context", "spec_version", "annotation_status",
            "paraphrase_group", "evaluation_scope",
        ):
            if metadata in case:
                record[metadata] = case[metadata]
        record.update(calculate_cost(record, pricing.get(deployment)))
        return record

    with result_path.open("w", encoding="utf-8", newline="\n") as output:
        for deployment in deployments:
            jobs = ((deployment, case, repeat) for case in cases for repeat in range(1, repeats + 1))

            def waiting():
                print(f"Waiting: {deployment}, workers={workers}; overall completed {len(records)}, "
                      f"pending {len(deployments) * len(cases) * repeats - len(records)}", flush=True)

            for _, future in completed_jobs(evaluate, jobs, workers, on_wait=waiting):
                record = future.result()
                error = record["error"]
                failures += error is not None
                records.append(record)
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()
                status = f"ERROR {error['type']}: {error['message'].splitlines()[0]}" if error else "OK"
                print(
                    f"[{len(records)}/{len(deployments) * len(cases) * repeats}] {deployment} "
                    f"{record['expected']['test_case_id']} repeat={record['repeat_index']} "
                    f"{status} ({record['latency_ms']:.0f} ms)", flush=True,
                )

    intent_labels = schema["properties"]["intent"]["enum"]
    entity_names = schema.get("$defs", {}).get("entities", {}).get("properties", {}).keys()
    summaries = {
        deployment: evaluate_records(
            [record for record in records if record["deployment"] == deployment],
            intent_labels,
            list(entity_names),
            pricing,
        )
        for deployment in deployments
    }
    summary_path = results_dir / f"summary-{timestamp}.json"
    summary_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result_path, summary_path, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CPG benchmark against configured Foundry deployments.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--deployment", help="run only this configured deployment")
    parser.add_argument("--workers", type=positive_workers, default=10)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.deployment:
            if args.deployment not in config["deployment_names"]:
                raise ValueError(f"Deployment {args.deployment!r} is not configured")
            config["deployment_names"] = [args.deployment]
        if not config["deployment_names"]:
            raise ValueError(
                "No deployments configured. Add names to evaluator/config.json "
                '(for example "decision-1" and "gpt-5.4").'
            )
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cases = _load_cases(args.dataset, schema)
        with ExitStack() as stack:
            client = {
                deployment: stack.enter_context(_foundry_client(deployment))
                for deployment in config["deployment_names"]
            }
            result_path, summary_path, failures = run(
                config, cases, schema, client, args.results_dir, workers=args.workers,
            )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Results: {result_path}")
    print(f"Summary: {summary_path}")
    print(f"Question executions: {len(cases) * len(config['deployment_names']) * config.get('repeat_count', 1)}")
    print(f"Failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
