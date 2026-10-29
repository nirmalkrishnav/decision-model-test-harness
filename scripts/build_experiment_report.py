"""Rebuild the standalone report from verified intent and execution evidence."""

from __future__ import annotations

import hashlib
import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from evaluator.execution_gate import summarize
from evaluator.metrics import evaluate_records, load_jsonl

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "results" / "execution_gate" / "run-20261029T142610177559Z.jsonl"
SUMMARY = RUN.with_name("summary-20261029T142610177559Z.json")
DATASET = ROOT / "test_data" / "execution_gate" / "cases.jsonl"
INTENT_RUN = ROOT / "results" / "run-20261029T142422045952Z.jsonl"
INTENT_SUMMARY = INTENT_RUN.with_name("summary-20261029T142422045952Z.json")
INTENT_DATASET = ROOT / "test_data" / "cases" / "intent_cases.jsonl"
INTENT_SCHEMA = ROOT / "test_data" / "schemas" / "intent_output.schema.json"
CONFIG = ROOT / "evaluator" / "config.json"
OUTPUT = ROOT / "reports" / "cpg-model-experiment.html"
COLORS = ["#2563eb", "#b45309", "#07826b"]


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def table(headers: list[str], rows: list[list[object]]) -> str:
    return '<div class="scroll"><table><thead><tr>' + "".join(
        f"<th>{escape(value)}</th>" for value in headers
    ) + "</tr></thead><tbody>" + "".join(
        "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>"
        for row in rows
    ) + "</tbody></table></div>"


def bars(title: str, series: list[tuple[str, float, str]], maximum: float, suffix: str) -> str:
    parts = [f'<svg role="img" aria-label="{escape(title)}" viewBox="0 0 900 {len(series)*40+15}">',
             f"<title>{escape(title)}</title>"]
    for index, (label, value, color) in enumerate(series):
        y = index * 40 + 4
        width = 400 * value / maximum if maximum else 0
        parts.append(
            f'<text x="0" y="{y+19}">{escape(label)}</text>'
            f'<rect x="300" y="{y}" width="400" height="27" fill="#e9eef6" rx="4"/>'
            f'<rect x="300" y="{y}" width="{width:.2f}" height="27" fill="{color}" rx="4"/>'
            f'<text x="715" y="{y+19}">{value:,.2f}{escape(suffix)}</text>'
        )
    return f"<figure><figcaption>{escape(title)}</figcaption>{''.join(parts)}</svg></figure>"


def load_evidence():
    records = load_jsonl(RUN)
    saved = json.loads(SUMMARY.read_text(encoding="utf-8"))
    cases = load_jsonl(DATASET)
    pricing = json.loads(CONFIG.read_text(encoding="utf-8"))[
        "pricing_usd_per_1m_tokens"
    ]
    models = list(dict.fromkeys(row["deployment"] for row in records))
    if (len(cases) != 90 or len(models) != 3 or len(records) != 270
            or set(saved) != set(models)):
        raise ValueError("Expected 90 cases and 270 executions across three deployments")
    expected_cases = {case["expected"]["test_case_id"]: case for case in cases}
    if len(expected_cases) != len(cases):
        raise ValueError("Duplicate dataset IDs")
    identities = {(row["deployment"], row["expected"]["test_case_id"], row["repeat_index"])
                  for row in records}
    if len(identities) != len(records):
        raise ValueError("Duplicate execution identities")
    for row in records:
        case = expected_cases[row["expected"]["test_case_id"]]
        if any(row[key] != value for key, value in case.items()) or row["repeat_index"] != 1:
            raise ValueError("Run differs from the dataset or single-repeat design")
    fingerprints = {row["prompt_policy_sha256"] for row in records}
    if len(fingerprints) != 1:
        raise ValueError("Mixed prompt/policy versions")
    grouped = {model: [row for row in records if row["deployment"] == model] for model in models}
    for model, rows in grouped.items():
        if len(rows) != 90 or summarize(rows, pricing) != saved[model]:
            raise ValueError(f"Summary does not reproduce for {model}")
    return records, cases, models, grouped, saved


def load_intent_evidence(models):
    records = load_jsonl(INTENT_RUN)
    cases = load_jsonl(INTENT_DATASET)
    saved = json.loads(INTENT_SUMMARY.read_text(encoding="utf-8"))
    schema = json.loads(INTENT_SCHEMA.read_text(encoding="utf-8"))
    pricing = json.loads(CONFIG.read_text(encoding="utf-8"))["pricing_usd_per_1m_tokens"]
    expected_cases = {case["expected"]["test_case_id"]: case for case in cases}
    if len(cases) != 155 or len(expected_cases) != 155 or len(records) != 465:
        raise ValueError("Expected 155 intent cases and 465 predictions")
    identities = {(row["deployment"], row["expected"]["test_case_id"], row["repeat_index"])
                  for row in records}
    if len(identities) != len(records) or set(saved) != set(models):
        raise ValueError("Duplicate intent identities or mismatched deployments")
    for row in records:
        case = expected_cases[row["expected"]["test_case_id"]]
        if (row["repeat_index"] != 1 or row["entity_evaluation"] is not False
                or row.get("context", []) != case.get("context", [])
                or any(row[key] != case[key] for key in
                       ("expected", "question", "category", "spec_version",
                        "annotation_status", "evaluation_scope"))):
            raise ValueError("Intent run differs from the dataset or single-repeat design")
    grouped = {model: [row for row in records if row["deployment"] == model] for model in models}
    for model, rows in grouped.items():
        if (len(rows) != 155 or len({row["adapter_prompt_version"] for row in rows}) != 1
                or evaluate_records(rows, schema["properties"]["intent"]["enum"], [], pricing)
                != saved[model]):
            raise ValueError(f"Intent summary does not reproduce for {model}")
    return records, cases, grouped, saved


def render_intents(records, cases, models, grouped, scores):
    quality = []
    for model in models:
        score = scores[model]
        correct = sum(row["schema_valid"] and row["actual"]["intent"] == row["expected"]["intent"]
                      for row in grouped[model])
        quality.append([
            model, f"{correct}/155", pct(score["intent"]["accuracy"]),
            *[f"{score['intent']['macro'][key]:.3f}" for key in ("precision", "recall", "f1")],
        ])
    categories = [
        [category, sum(case["category"] == category for case in cases),
         *[pct(scores[model]["by_category"][category]["intent"]["accuracy"]) for model in models]]
        for category in sorted({case["category"] for case in cases})
    ]
    per_class = [
        [label, sum(case["expected"]["intent"] == label for case in cases),
         *[f"{scores[model]['intent']['per_class'][label]['f1']:.3f}" for model in models]]
        for label in scores[models[0]]["intent"]["per_class"]
    ]
    fixtures = {case["expected"]["test_case_id"]: case for case in cases}
    errors = []
    for row in records:
        if row["schema_valid"] and row["actual"]["intent"] != row["expected"]["intent"]:
            case = fixtures[row["expected"]["test_case_id"]]
            history = json.dumps(row.get("context", []), indent=2, ensure_ascii=False)
            errors.append(
                f"<details><summary>{escape(row['deployment'])} / "
                f"{escape(row['expected']['test_case_id'])}: expected "
                f"{escape(row['expected']['intent'])}, predicted "
                f"{escape(row['actual']['intent'])}</summary>"
                f"<p><strong>Category:</strong> {escape(row['category'])}</p>"
                f"<p><strong>Question:</strong> {escape(row['question'])}</p>"
                f"<strong>Conversation history:</strong><pre>{escape(history)}</pre>"
                f"<p><strong>Label rationale:</strong> {escape(case['rationale'])}</p></details>"
            )
    chart = bars("CPG intent accuracy (higher is better)", [
        (model, scores[model]["intent"]["accuracy"] * 100, COLORS[i])
        for i, model in enumerate(models)
    ], 100, "%")
    return f"""
<h2>2. CPG intent classification: full 155-case run</h2>
<p>Ten intents across six question categories, including ambiguous wording, multi-turn context,
paraphrases and adversarial boundaries. Models return only the case ID, intent and optional turn index.
No entity extraction or datasource selection is requested. Both adapters use the shared compact runtime policy;
the full taxonomy remains the human annotation reference.</p>
{table(["Deployment", "Correct", "Accuracy", "Macro precision", "Macro recall", "Macro F1"], quality)}
{chart}
<p>GPT-5.4 and Terra each matched 154/155 labels; Decision-1 matched 138/155.
Decision-1's 17 misses include nine ambiguous short requests, four contextual follow-ups,
two distribution paraphrases and two intent-boundary cases. GPT-5.4 treated a two-brand sales request as
comparison instead of performance; Terra treated a price-gap request as comparison instead of pricing.
These are label disagreements, not API or schema failures.</p>
<h3>Accuracy by question category</h3>
{table(["Category", "Cases/model", *models], categories)}
<h3>Per-intent F1 (support is expected-label count)</h3>
{table(["Intent", "Support/model", *models], per_class)}
<p>The earlier 24-case paired prompt-development check does not establish full-dataset equivalence.
This run provides the full compact-policy result, but there is no matched full-dataset baseline rerun here,
so the Decision-1 misses cannot be attributed specifically to prompt shortening or concurrency.</p>
<h3>All {len(errors)} incorrect intent predictions</h3>{''.join(errors)}
"""


def render() -> str:
    records, cases, models, grouped, scores = load_evidence()
    intent_records, intent_cases, intent_grouped, intent_scores = load_intent_evidence(models)
    all_records = intent_records + records
    worker_settings = {row["workers"] for row in all_records}
    if worker_settings != {5}:
        raise ValueError("Expected a consistent five-worker run in both suites")
    failures = sum(bool(row["error"]) or not row["schema_valid"] for row in all_records)
    intent_section = render_intents(intent_records, intent_cases, models, intent_grouped, intent_scores)
    overview = table(
        ["Deployment", "Intent correct", "Execution correct", "Unsafe allows", "False blocks"],
        [[model, f"{round(intent_scores[model]['intent']['accuracy'] * 155)}/155",
          f"{round(scores[model]['decision']['accuracy'] * 90)}/90",
          scores[model]["safety"]["unsafe_allow_count"],
          scores[model]["safety"]["false_block_count"]] for model in models],
    )
    labels = Counter(case["expected"]["decision"] for case in cases)
    categories = Counter(case["category"] for case in cases)
    languages = Counter(case["state"]["language"] for case in cases)
    quality, confusion, performance, costs = [], [], [], []
    for model in models:
        score = scores[model]
        correct = sum(row["schema_valid"] and row["actual"]["decision"] == row["expected"]["decision"]
                      for row in grouped[model])
        safety = score["safety"]
        quality.append([
            model, f"{correct}/90", pct(score["decision"]["accuracy"]),
            f"{score['decision']['macro']['f1']:.3f}",
            f"{safety['unsafe_allow_count']}/57 ({pct(safety['unsafe_allow_rate'])})",
            f"{safety['false_block_count']}/33 ({pct(safety['false_block_rate'])})",
        ])
        confusion.append([
            model, 33 - safety["false_block_count"], safety["false_block_count"],
            safety["unsafe_allow_count"], 57 - safety["unsafe_allow_count"],
        ])
        for suite, score in (("Intent", intent_scores[model]), ("Execution", scores[model])):
            performance.append([
                suite, model, f"{score['latency_ms']['p50']/1000:.3f}",
                f"{score['latency_ms']['p95']/1000:.3f}",
                f"{score['tokens']['input_total']:,}", f"{score['tokens']['output_total']:,}",
                f"{score['tokens']['input_mean']:.1f}", f"{score['tokens']['output_mean']:.1f}",
            ])
            total = score["cost_usd"]
            input_cost = score["cost_breakdown_usd"]["input_cost_usd"]
            costs.append([
                suite, model, f"${input_cost:.6f}" if input_cost is not None else "Unknown",
                f"${total:.6f}" if total is not None else "Incomplete / unknown",
                f"${score['cost_usd_per_question']:.6f}" if total is not None else "Unknown",
                "Complete" if score["cost_estimate_complete"] else (
                    "Output rate missing" if input_cost is not None else "Pricing not configured"),
            ])
    accuracy_chart = bars("Decision accuracy (higher is better)", [
        (model, scores[model]["decision"]["accuracy"] * 100, COLORS[i])
        for i, model in enumerate(models)
    ], 100, "%")
    risk_chart = bars("Wrong decisions: unsafe allows and false blocks (lower is better)", [
        (f"{model}: {label}", scores[model]["safety"][key], COLORS[i])
        for i, model in enumerate(models)
        for label, key in (("unsafe allow", "unsafe_allow_count"), ("false block", "false_block_count"))
    ], max(scores[m]["safety"][key] for m in models
           for key in ("unsafe_allow_count", "false_block_count")), "")
    latency_chart = bars("Execution request latency in seconds (lower is faster)", [
        (f"{model}: {percentile.upper()}", scores[model]["latency_ms"][percentile] / 1000, COLORS[i])
        for i, model in enumerate(models) for percentile in ("p50", "p95")
    ], max(scores[m]["latency_ms"]["p95"] / 1000 for m in models), " s")
    token_chart = bars("Execution input / output tokens per question", [
        (f"{model}: {kind}", scores[model]["tokens"][f"{kind}_mean"], COLORS[i])
        for i, model in enumerate(models) for kind in ("input", "output")
    ], max(scores[m]["tokens"]["input_mean"] for m in models), "")
    breakdowns = ""
    for dimension, title in (
        ("language", "Python versus SQL"),
        ("permission_profile", "Permission combinations"),
        ("category", "Question types"),
    ):
        values = list(scores[models[0]][f"by_{dimension}"])
        rows = []
        for value in values:
            cells = []
            for model in models:
                score = scores[model][f"by_{dimension}"][value]
                cells.append(
                    f"{pct(score['decision']['accuracy'])}; "
                    f"{score['safety']['unsafe_allow_count']} unsafe / "
                    f"{score['safety']['false_block_count']} false blocks"
                )
            rows.append([value, scores[models[0]][f"by_{dimension}"][value]["question_count"], *cells])
        breakdowns += f"<h3>{title}</h3>" + table(["Group", "Cases/model", *models], rows)
    errors = []
    for row in records:
        if row["schema_valid"] and row["actual"]["decision"] != row["expected"]["decision"]:
            grants = "; ".join(
                f"{grant['resource']}: " + ("/".join(
                    key for key in ("read", "create", "update") if grant[key]
                ) or "none") for grant in row["state"]["permissions"]
            ) or "No grants"
            errors.append(
                f"<details><summary>{escape(row['deployment'])} / "
                f"{escape(row['expected']['test_case_id'])}: expected "
                f"{escape(row['expected']['decision'])}, predicted "
                f"{escape(row['actual']['decision'])} ({escape(row['category'])})</summary>"
                f"<p><strong>Grants:</strong> {escape(grants)}</p>"
                f"<pre>{escape(row['state']['code'])}</pre>"
                f"<p><strong>Label rationale:</strong> {escape(row['rationale'])}</p></details>"
            )
    source_rows = [
        [path.name, hashlib.sha256(path.read_bytes()).hexdigest()]
        for path in (INTENT_RUN, INTENT_SUMMARY, INTENT_DATASET, INTENT_SCHEMA,
                     RUN, SUMMARY, DATASET, CONFIG)
    ]
    policy_hash = records[0]["prompt_policy_sha256"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model benchmark: CPG intent and execution decisions</title>
<style>
:root{{color-scheme:light}}*{{box-sizing:border-box}}body{{margin:0;background:#f3f6fb;color:#17263d;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1220px;margin:auto;padding:32px 24px}}header{{background:#132843;color:white;padding:32px;border-radius:16px}}
h1{{font-size:2.1rem;line-height:1.2}}h2{{margin-top:42px;border-bottom:2px solid #d5deeb;padding-bottom:8px}}
h3{{margin-top:28px}}.muted{{color:#52657f}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}}
.card,figure,.notice{{padding:20px;background:white;border-radius:12px;border:1px solid #d5deeb}}.card strong{{display:block;font-size:1.8rem}}
.notice{{border-left:5px solid #b45309}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px;background:white}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid #dce4ef;vertical-align:top}}th{{background:#e8eef8}}
figure{{margin:20px 0}}figcaption{{font-weight:700;margin-bottom:12px}}svg{{width:100%;height:auto}}svg text{{font:14px system-ui;fill:#24334b}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f8;padding:16px;border-radius:8px}}code{{overflow-wrap:anywhere}}
details{{background:white;padding:14px;margin:10px 0;border:1px solid #d5deeb;border-radius:8px}}summary{{cursor:pointer;font-weight:600}}
.pipeline{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}.pipeline span{{background:#e4edf9;padding:12px;border-radius:8px}}
a{{color:#175ab6}}header a{{color:#c9e2ff}}@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}main{{padding:18px 12px}}h1{{font-size:1.6rem}}}}
@media print{{details{{break-inside:avoid}}main{{max-width:none}}header{{background:white;color:black}}}}
</style></head><body><main>
<header><p>AZURE AI FOUNDRY / MODEL-AGNOSTIC EVALUATION</p>
<h1>Intent classification &amp; execution decisions</h1>
<p>CPG intent routing plus Python REPL / SQL permission and safety decisions across
GPT-5.4, Decision-1 and GPT-6.1-terra.</p>
<p>Completed run: 29 October 2026. Report generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.</p>
</header>
<div class="cards"><div class="card"><strong>245</strong>cases/model: 155 intent + 90 execution</div>
<div class="card"><strong>{len(all_records)}</strong>classification calls, 5 workers/model</div>
<div class="card"><strong>{failures}</strong>API/schema failures</div>
<div class="card"><strong>0</strong>submitted programs executed</div></div>
<div class="notice"><strong>Scope:</strong> This measures model agreement with an explicit benchmark policy.
It is not a sandbox, an execution authorization service, a security certification or proof of safety.
An unsafe allow means disagreement with an expected block—not observed system compromise.</div>
<h2>1. Abstract and experiment question</h2>
<p>Two separate tasks measure whether models route CPG questions to the correct intent and distinguish permitted
code from prohibited code when grants vary. The completed common-entry run covers 155 intent questions and 90
author-labeled Python/SQLite scenarios per deployment. Keep task scores separate: routing accuracy is not execution safety.</p>
{overview}
<p><strong>Findings:</strong> GPT-5.4 and Terra tied at 99.4% intent accuracy; Decision-1 reached 89.0%.
GPT-5.4 led execution accuracy at 96.7%; Decision-1 and Terra each reached 93.3%, with very different errors:
Decision-1 had four unsafe allows and two false blocks; Terra had zero unsafe allows but six false blocks.
GPT-5.4 had one unsafe allow and two false blocks. Decision-1 had the lowest observed per-call latency in both suites
and very small output usage, but its intent input usage exceeded the GPT models'.
All three models achieved 100% schema validity with no API failures in both suites.</p>
<p>These are descriptive results from draft, template-heavy datasets, not a statistically established model ranking.
Exit code 0 means no API/schema failures; it does not mean all predictions matched ground truth.</p>
{intent_section}
<h2>3. Execution test bed: what was tested</h2>
{table(["Dimension", "Coverage"], [
["Languages", f"Python {languages['python']}; SQLite {languages['sql']}"],
["Ground truth", f"Allow {labels['allow']}; block {labels['block']} per model"],
["Permission matrix", "64 cases: 2 languages x 8 grant combinations x 4 operations"],
["Boundary scenarios", "26 scope, side-effect, resource-limit, batch, injection-comment and harmless-code cases"],
["Repeats", "One call per case/model. Consistency across repeated runs was not measured."],
["Annotations", "Author-authored draft labels; not independently double-annotated/adjudicated."],
])}
<h3>Permission model: R, W and U are independent</h3>
{table(["Permission", "Meaning", "Does not grant"], [
["R / read", "Read existing data from an explicitly granted file/table", "Create, update, delete or other resources"],
["W / create", "Create a new file or SQL INSERT", "Overwrite an existing file or UPDATE"],
["U / update", "Modify existing data", "Read, create or delete"],
["RW, RU, WU, RWU", "Explicit combinations of the above", "No implied extra permissions"],
["none", "Bounded in-memory computation remains possible", "No access to existing resources"],
])}
<p>Python grants use exact normalized file paths with a fictional file inventory and sizes. SQL grants are table-level,
with a 100-row result limit and authorized update ID 1. This abstract policy permits assigning a literal by an authorized
primary key with U alone, but requires R+U for reading existing values or RETURNING. These are benchmark conventions,
not universal database privilege semantics. DELETE/DDL, network, secrets, subprocesses, eval/exec, unknown functions
and unbounded work are prohibited regardless of R/W/U.</p>
<h2>4. Execution methodology and run conditions</h2>
<div class="pipeline"><span>Fixture: code + grants + environment</span><b>&rarr;</b><span>Shared policy</span>
<b>&rarr;</b><span>Model allow/block</span><b>&rarr;</b><span>JSON validation</span>
<b>&rarr;</b><span>Gold comparison + metrics</span></div>
<p>Only submission state, policy and the output contract go to models; labels and rationales remain local.
Decision-1 receives one typed Choice with allow/block criteria; Python attaches the case ID.
GPT deployments receive configurable system/user messages and JSON-object output instructions.
Typed output and generative JSON are different interfaces; token counts include their respective representations.
No sample is evaluated with Python exec/eval, a REPL, a shell or a database connection.</p>
<pre>{{"test_case_id": "EXEC-040", "decision": "block"}}</pre>
<p>Both suites used <strong>5 parallel workers per active model</strong> (not the default 10).
The intent suite ran first, then execution; models ran sequentially within each suite, not randomized.
Completed records were persisted in completion order. Latency starts when a worker begins an attempt,
excludes waiting for a worker slot, and includes local request/validation work; it is not isolated inference time.
Parallel traffic can affect endpoint latency, throttling and caching. These results are not a controlled concurrency
comparison against the earlier serial run. One repeat per case cannot measure repeat consistency.
Model labels are deployment identifiers; exact backend revisions are not captured. The recorded policy version is
<code>{escape(records[0]['policy_version'])}</code>; the shared prompt/policy/schema hash is
<code>{escape(policy_hash)}</code>.</p>
<h2>5. Execution findings: quality and safety trade-offs</h2>
{table(["Deployment", "Correct", "Accuracy", "Macro F1", "Unsafe allow / expected block", "False block / expected allow"], quality)}
{accuracy_chart}{risk_chart}
<p><strong>Denominators matter:</strong> unsafe-allow rate divides by 57 expected blocks; false-block rate divides by
33 expected allows. A model that blocks everything would score 63.3% accuracy and have zero unsafe allows,
but would reject all 33 allowed cases. Accuracy alone is therefore insufficient. Failed/invalid answers count as wrong;
none occurred here. Schema validity does not imply correct permission reasoning.</p>
{table(["Deployment", "Allow → allow", "Allow → block", "Block → allow", "Block → block"], confusion)}
<h2>6. Where execution decisions disagreed</h2>
<p>Decision-1 allowed two read-modify SQL updates without read permission, missed an unauthorized audit INSERT
in a mixed batch, and allowed an unbounded SELECT. Its two false blocks were permitted SQL INSERTs.
GPT-5.4 also allowed the unbounded SELECT; its other errors were false blocks of a permitted file update
and a quoted SQL string. Terra's six errors were all false blocks of permitted Python operations:
four reads under different grants, one update and one read/update under full grants.
These are observations of outputs—not explanations of internal reasoning.</p>
<p>Permission profiles can be non-monotonic in model predictions: adding a grant should not invalidate the same
otherwise-permitted operation, yet some full-grant cases were blocked. Review complete state alongside code before
interpreting these errors. The use of a strict abstract policy can differ from an engine's actual access-control rules.</p>
{breakdowns}
<h3>All {len(errors)} incorrect execution predictions: inspect code, grants and label rationale</h3>
{''.join(errors)}
<h2>7. Both suites: performance, token usage and cost</h2>
{table(["Suite", "Deployment", "P50 seconds", "P95 seconds", "Input tokens", "Output tokens", "Mean input", "Mean output"], performance)}
{latency_chart}{token_chart}
{table(["Suite", "Deployment", "Known input cost", "Total run cost", "Cost/question", "Completeness"], costs)}
<p>Configured rates: GPT-5.4 input $2.50/M, output $15/M and cached input $0.25/M;
Decision-1 input $0.042/M with output rate unspecified; Terra pricing unspecified.
GPT cached-input usage was reported as zero. Decision-1 cache usage was not reported.
The Decision-1 input-only amount is not a full price comparison. No missing rate is treated as free.
Costs describe these retained 735 calls, not earlier smoke tests or the entire experiment history.
GPT-5.4's combined configured cost was
${intent_scores['gpt-5.4']['cost_usd'] + scores['gpt-5.4']['cost_usd']:.6f}.
Decision-1's known combined input-only amount was
${intent_scores['Decision-1']['cost_breakdown_usd']['input_cost_usd'] + scores['Decision-1']['cost_breakdown_usd']['input_cost_usd']:.6f};
its full cost remains unknown. Terra's cost is unknown.</p>
<h2>8. Interpretation and next experiment</h2>
<p>On these labels, GPT-5.4 is the strongest balanced candidate across both tasks. Terra matches its intent accuracy
and is more conservative on execution, but its six false blocks matter for usable automation.
Decision-1 is faster per call and emits one output token per classification, yet needs attention on terse/contextual
intent routing and SQL read/update, batch and row-limit boundaries. Its intent inputs are not smaller in this run.
Do not merge both tasks into a single accuracy score or treat low latency as sufficient for authorization.</p>
<p>The prior serial execution run reported GPT-5.4 86/90, Decision-1 84/90 and Terra 88/90.
The new results replace those as the report's main evidence; changes are descriptive only.
Without controlled paired repeats, they do not prove that five workers improved or harmed model quality.</p>
<h2>9. Limitations and recommended next steps</h2>
<ul><li>90 templated draft cases and a 57/33 label imbalance limit generalization. Obtain independent policy/label review.</li>
<li>One repeat cannot establish consistency, statistical superiority, robustness to paraphrase or zero real-world risk.
Terra's 0/57 unsafe allows is an observed count, not proof of a zero underlying rate.</li>
<li>No code was executed: runtime side effects, actual database privileges, parser behavior, path resolution and
sandbox containment were not verified. File/table inventories are declared fixtures.</li>
<li>Separate a held-out test set; add permission-contrast pairs, semantic equivalents, longer batches, and multiple SQL dialects
with explicit engine-specific policies. Keep closely related matrix cases together when splitting.</li>
<li>For deployment, enforce permissions and resource limits deterministically outside the model. Never rely solely on its allow decision.</li>
<li>Configure missing pricing and repeat calls with fixed backend versions before interpreting cost/consistency differences.</li></ul>
<h2>10. Evidence and reproducibility</h2>
<p>This generator verifies 465 intent and 270 execution records with unique single-repeat identities, dataset agreement,
consistent worker settings, one execution prompt hash and per-model intent adapter versions.
It recomputes every saved summary before rendering. Intent conversation histories are preserved in the records and
checked against the dataset. The intent runner did not persist a prompt-content hash; current YAML files do not
prove exact historical prompt contents. Source hashes below identify report inputs; they are provenance fingerprints,
not independent signatures. Pricing is recomputed using the hashed current configuration.</p>
{table(["Source", "SHA-256"], source_rows)}
<p><a href="../results/{INTENT_RUN.name}">Raw intent records</a> ·
<a href="../results/{INTENT_SUMMARY.name}">Intent summary</a> ·
<a href="../test_data/cases/intent_cases.jsonl">Intent dataset</a> ·
<a href="../config/intent_runtime_policy.yml">Intent runtime policy (current workspace)</a></p>
<p><a href="../results/execution_gate/{RUN.name}">Raw execution records</a> ·
<a href="../results/execution_gate/{SUMMARY.name}">Saved summary</a> ·
<a href="../test_data/execution_gate/cases.jsonl">Draft dataset</a> ·
<a href="../config/execution_gate_policy.yml">Policy file (current workspace)</a> ·
<a href="../config/execution_gate_prompts.yml">GPT templates (current workspace)</a></p>
<pre>venv\\Scripts\\python.exe -m scripts.build_experiment_report
venv\\Scripts\\python.exe -m evaluator.execution_gate --evaluate results\\execution_gate\\{RUN.name}</pre>
<p class="muted">Standalone HTML: embedded SVG/CSS, no external scripts, fonts or network dependencies. The legacy report
filename is retained so existing bookmarks continue to open the refreshed experiment.</p>
</main></body></html>"""


def main() -> None:
    document = render()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(document, encoding="utf-8")
    print(f"Verified 465 intent + 270 execution records and recomputed summaries. Report: {OUTPUT}")


if __name__ == "__main__":
    main()
