"""Generate a standalone, evidence-checked HTML report from the saved benchmark."""

from __future__ import annotations

import hashlib
import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from evaluator.metrics import evaluate_records, load_jsonl

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
RUN = RESULTS / "combined-20261029-decision-compact.jsonl"
SUMMARY = RESULTS / "combined-20261029-decision-compact-summary.json"
PROVENANCE = RESULTS / "combined-20261029-decision-compact-provenance.json"
OUTPUT = ROOT / "reports" / "cpg-model-experiment.html"
MODELS = ["gpt-5.4", "Decision-1", "gpt-6.1-terra"]
COLORS = ["#2563eb", "#b45309", "#07826b"]
CATEGORIES = {
    "simple_intent": "Simple intent",
    "multiple_entities": "Multiple entities",
    "entity_normalization": "Entity normalization",
    "missing_null_entities": "Missing / null entities",
    "ambiguous_questions": "Ambiguous questions",
    "multi_intent": "Multi-intent",
    "multi_turn_context": "Multi-turn / context",
    "paraphrases": "Paraphrases",
    "adversarial_boundary": "Adversarial / boundary",
}


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def table(headers: list[str], rows: list[list[object]]) -> str:
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + "".join(f"<th scope='col'>{escape(header)}</th>" for header in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
        + "</tbody></table></div>"
    )


def bars(title: str, series: list[tuple[str, float, str]], maximum: float, suffix: str) -> str:
    height = 50 + len(series) * 46
    content = [
        f'<svg role="img" aria-label="{escape(title)}" viewBox="0 0 820 {height}">',
        f"<title>{escape(title)}</title>",
    ]
    for index, (label, value, color) in enumerate(series):
        y = 16 + index * 46
        width = 430 * value / maximum if maximum else 0
        content.extend([
            f'<text x="0" y="{y + 18}" fill="#24334b" font-size="15">{escape(label)}</text>',
            f'<rect x="260" y="{y}" width="430" height="27" rx="4" fill="#edf1f7"/>',
            f'<rect x="260" y="{y}" width="{width:.2f}" height="27" rx="4" fill="{color}"/>',
            f'<text x="704" y="{y + 18}" fill="#24334b" font-size="15">{value:,.2f}{escape(suffix)}</text>',
        ])
    content.append("</svg>")
    return f'<figure><figcaption>{escape(title)}</figcaption>{"".join(content)}</figure>'


def main() -> None:
    records = load_jsonl(RUN)
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    config = json.loads((ROOT / "evaluator" / "config.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (ROOT / "test_data" / "schemas" / "canonical_output.schema.json").read_text(encoding="utf-8")
    )
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    for prefix in ("previous", "replacement"):
        source = Path(provenance[f"{prefix}_run"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != provenance[f"{prefix}_sha256"]:
            raise ValueError(f"{prefix} source hash differs from merge provenance")
    previous = load_jsonl(Path(provenance["previous_run"]))
    if [r for r in previous if r["deployment"] != "Decision-1"] != [
        r for r in records if r["deployment"] != "Decision-1"
    ]:
        raise ValueError("Combined GPT records differ from the original run")
    if len(records) != 750 or len({
        (r["deployment"], r["expected"]["test_case_id"], r["repeat_index"]) for r in records
    }) != 750:
        raise ValueError("Expected 750 unique executions")
    grouped = {model: [r for r in records if r["deployment"] == model] for model in MODELS}
    for model, rows in grouped.items():
        computed = evaluate_records(
            rows, schema["properties"]["intent"]["enum"],
            list(schema["$defs"]["entities"]["properties"]), config["pricing_usd_per_1m_tokens"],
        )
        if len(rows) != 250 or computed != summary[model]:
            raise ValueError(f"Saved metrics do not reproduce for {model}")

    decision = grouped["Decision-1"]
    errors = Counter()
    extra_types = Counter()
    tp = fp = fn = 0
    for record in decision:
        case_fp = case_fn = 0
        for name, gold in record["expected"]["entities"].items():
            expected = set(gold)
            actual = set(record["actual"]["entities"][name])
            tp += len(expected & actual)
            case_fp += len(actual - expected)
            case_fn += len(expected - actual)
            extra_types[name] += len(actual - expected)
        fp += case_fp
        fn += case_fn
        errors["exact" if not case_fp and not case_fn else
               "extras only" if not case_fn else "missing only" if not case_fp else "both"] += 1
    counts = Counter(r["category"] for r in grouped["gpt-5.4"])
    formats = sum(extra_types[name] for name in ("pack_size", "discount", "date_range"))
    context_unsupported = sum(
        r["category"] == "multi_turn_context" and r["actual"]["intent"] == "unsupported"
        for r in decision
    )
    sample = next(r for r in decision if r["expected"]["test_case_id"] == "BENCH-001")
    gold_sample = {k: v for k, v in sample["expected"]["entities"].items() if v}
    actual_sample = {k: v for k, v in sample["actual"]["entities"].items() if v}
    quality_rows = []
    usage_rows = []
    latency_rows = []
    joint = {}
    for model in MODELS:
        score = summary[model]
        rows = grouped[model]
        joint[model] = sum(
            r["schema_valid"] and r["actual"]["intent"] == r["expected"]["intent"]
            and all(set(r["actual"]["entities"][k]) == set(v)
                    for k, v in r["expected"]["entities"].items())
            for r in rows
        )
        quality_rows.append([
            model, pct(score["intent"]["accuracy"]), pct(score["entities"]["exact_match"]),
            pct(score["entities"]["micro"]["precision"]), pct(score["entities"]["micro"]["recall"]),
            pct(score["entities"]["micro"]["f1"]), f'{score["schema_valid_pct"]:.1f}%',
            sum(bool(r.get("error")) for r in rows), f"{joint[model]}/250",
        ])
        tokens = score["tokens"]
        usage_rows.append([
            model, f'{tokens["input_total"]:,}', f'{tokens["output_total"]:,}',
            f'{tokens["cached_input_total"]:,}' if tokens["cached_input_total"] is not None else "Unknown / incomplete",
            f'{tokens["input_mean"]:,.1f}', f'{tokens["output_mean"]:,.1f}',
        ])
        latency_rows.append([
            model, f'{score["latency_ms"]["p50"] / 1000:.3f}',
            f'{score["latency_ms"]["p95"] / 1000:.3f}',
            f'{max(r["latency_ms"] for r in rows) / 1000:,.1f}',
        ])
    heatmap_rows = []
    for category, label in CATEGORIES.items():
        cells = [f"<th scope='row'>{escape(label)} ({counts[category]})</th>"]
        for model in MODELS:
            value = summary[model]["by_category"][category]["entities"]["exact_match"]
            cells.append(
                f'<td style="background:hsl({int(140 * value)} 65% 91%)">{pct(value)}</td>'
            )
        heatmap_rows.append("<tr>" + "".join(cells) + "</tr>")
    heatmap = (
        '<div class="table-wrap"><table><thead><tr><th>Category (n)</th>'
        + "".join(f"<th>{escape(model)}</th>" for model in MODELS)
        + "</tr></thead><tbody>" + "".join(heatmap_rows) + "</tbody></table></div>"
    )
    quality_chart = bars("Intent accuracy vs whole-record entity exact match", [
        (f"{model} - {metric}", 100 * value, COLORS[index])
        for index, model in enumerate(MODELS)
        for metric, value in (
            ("intent", summary[model]["intent"]["accuracy"]),
            ("entities", summary[model]["entities"]["exact_match"]),
        )
    ], 100, "%")
    token_chart = bars("Reported input tokens per scored question (includes cached input)", [
        (model, summary[model]["tokens"]["input_mean"], COLORS[index])
        for index, model in enumerate(MODELS)
    ], max(summary[m]["tokens"]["input_mean"] for m in MODELS), "")
    error_chart = bars("Decision-1 false-positive entity values", [
        ("Date range", extra_types["date_range"], "#b45309"),
        ("Pack size", extra_types["pack_size"], "#d97706"),
        ("Discount", extra_types["discount"], "#f59e0b"),
        ("All other entity types", fp - formats, "#64748b"),
    ], max(extra_types.values()), "")
    latency_chart = bars("P50 / P95 request latency (seconds)", [
        (f"{model} - {percentile.upper()}", summary[model]["latency_ms"][percentile] / 1000, COLORS[index])
        for index, model in enumerate(MODELS) for percentile in ("p50", "p95")
    ], max(summary[m]["latency_ms"]["p95"] / 1000 for m in MODELS), " s")
    source_links = [
        ("Combined summary", "../results/" + SUMMARY.name),
        ("Combined execution records", "../results/" + RUN.name),
        ("Merge provenance and source hashes", "../results/" + PROVENANCE.name),
        ("Decision retry provenance", "../results/run-20261029T015332297689Z-recovered.provenance.json"),
        ("250-case draft dataset", "../test_data/cases/benchmark_250.jsonl"),
        ("Annotation guide", "../test_data/taxonomy/annotation_guide.md"),
        ("Metric implementation", "../evaluator/metrics.py"),
        ("Decision-1 adapter", "../evaluator/decision.py"),
        ("Configured token prices", "../evaluator/config.json"),
    ]
    report = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CPG intent and entity extraction - experiment report</title>
<style>
:root{{font-family:Segoe UI,Arial,sans-serif;color:#17233a;background:#edf2f8;line-height:1.6}}
body{{margin:0}}main{{max-width:1180px;margin:auto;background:white;padding:44px}}
h1{{font-size:2.4rem;line-height:1.15}}h2{{margin-top:42px;border-top:1px solid #dce4ef;padding-top:24px}}
h3{{margin-top:24px}}p,li{{max-width:100ch}}.eyebrow{{text-transform:uppercase;letter-spacing:.13em;color:#35618d;font-size:.8rem;font-weight:700}}
.muted,small{{color:#53647c}}.callout{{padding:18px 22px;background:#eff6ff;border-left:5px solid #2563eb;border-radius:4px;margin:22px 0}}
.warning{{background:#fff7ed;border-color:#b45309}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}
.card{{background:#f3f6fb;padding:18px;border-radius:10px}}.card strong{{display:block;font-size:1.7rem}}
figure{{margin:24px 0;padding:20px;border:1px solid #e0e6f0;border-radius:10px}}
figcaption{{font-weight:650;margin-bottom:14px}}svg{{width:100%;height:auto;display:block}}
table{{border-collapse:collapse;width:100%;font-size:.89rem}}td,th{{padding:10px 12px;border-bottom:1px solid #dce4ef;text-align:left}}
thead{{background:#f0f4fa}}.table-wrap{{overflow-x:auto;margin:18px 0}}a{{color:#1756af}}
pre{{background:#122139;color:#e5edf8;padding:18px;border-radius:8px;overflow:auto;line-height:1.45;font-size:.85rem}}
.flow{{display:flex;align-items:stretch;gap:8px;flex-wrap:wrap;margin:20px 0}}
.flow div{{background:#eff6ff;border:1px solid #c9dbf2;border-radius:8px;flex:1;min-width:120px;padding:14px}}
.arrow{{align-self:center;color:#476483;font-size:22px}}.tag{{display:inline-block;background:#e8eef8;padding:3px 10px;border-radius:20px;font-size:.8rem}}
@media(max-width:700px){{main{{padding:22px}}.cards{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:1.9rem}}}}
@media print{{:root{{background:white}}main{{padding:0}}figure,table,.callout{{break-inside:avoid}}a{{color:inherit}}}}
</style></head><body><main>
<div class="eyebrow">Experiment report | Runs collected 28-29 October 2026</div>
<h1>CPG intent classification<br>and entity extraction</h1>
<p class="muted">Decision-1 typed decisions versus GPT chat-completions deployments. An exploratory adapter benchmark, not a controlled model-ranking study.</p>
<div class="cards"><div class="card"><strong>250</strong>draft target questions</div>
<div class="card"><strong>3</strong>deployment identifiers</div><div class="card"><strong>750</strong>retained scored records</div>
<div class="card"><strong>9</strong>test categories</div></div>

<h2>1. Abstract</h2>
<p>This experiment investigates whether Azure AI Foundry models can translate consumer packaged goods (CPG)
analytics questions into one canonical intent and fourteen normalized entity arrays. A synthetic, fictional-brand
dataset covers direct requests, normalization, ambiguity, multiple intents, context, paraphrases and boundaries.
Each of three deployments was evaluated on 250 target turns, with schema validation, separate intent and entity
metrics, latency and provider-reported usage. GPT-5.4 achieved 99.6% intent accuracy and 91.6% entity exact match;
gpt-6.1-terra achieved 96.8% and 95.2%. Decision-1's recovered compact-prompt run achieved 92.4% intent accuracy
but only 2.4% entity exact match. Its principal error was over-extraction: {fp:,} false-positive entity values,
with {pct(formats / fp)} from pack size, discount and date candidates. These findings diagnose the current
candidate-verification adapter; they do not establish an intrinsic limitation of Decision-1.</p>
<div class="callout"><strong>Central finding:</strong> valid typed output is not necessarily correct extraction.
Decision-1 produced 100% schema-valid outputs after recovery, yet only 6 of 250 entity objects exactly matched
the expected labels. Intent, entity correctness and operational reliability must be reported separately.</div>

<h2>2. What the experiment is about</h2>
<p>The application goal is an analytics-question understanding layer: identify the business operation requested,
extract filters and measures, and pass normalized output to downstream systems. Example: <em>Show unit sales for
FreshFizz in Northland last quarter</em> should identify performance_analysis, brand FreshFizz, geography
Northland, date last quarter and metric unit_sales, without inventing a manufacturer, channel or pack size.</p>
<p><strong>Tested:</strong> intent classification, entity selection/normalization, structured-output compliance,
context interpretation and operational performance. <strong>Not tested:</strong> answering analytics questions,
query execution, database correctness, access controls, or datasource selection. Datasource selection using a
Choice question was discussed only as a proposed design. No separately identified SLM baseline was evaluated.</p>

<h2>3. What was tested</h2>
{table(["Category", "Target turns"], [[label, counts[key]] for key, label in CATEGORIES.items()] + [["Total", 250]])}
<p>The ten canonical intents are performance_analysis, promotion_analysis, pricing_analysis,
distribution_analysis, assortment_analysis, comparison, trend_analysis, forecast, recommendation and unsupported.
The fourteen entity types are brand, manufacturer, product, category, sub_category, pack_size, pack_type, channel,
retailer, geography, promotion_type, discount, date_range and metric. Every key is required; absent values are
<code>[]</code>, not JSON null. Values are not inferred from product, retailer or geography relationships.</p>
<p>Thirty context cases score the current user turn with fixed preceding messages; history messages are not
additional scored cases. Twenty paraphrases form five groups of four. Labels are specification-derived drafts,
not independently double-annotated ground truth. Repeated templates across fictional brands limit independence
and diversity. The original ten-case smoke set is separate from the 250-case report.</p>

<h2>4. How it was tested</h2>
<div class="flow" aria-label="Evaluation pipeline">
<div><strong>Dataset</strong><br>Question + optional history<br>Expected labels held back</div><span class="arrow">&#8594;</span>
<div><strong>Adapter</strong><br>Prompt / typed questions<br>Taxonomy and policy</div><span class="arrow">&#8594;</span>
<div><strong>Foundry model</strong><br>API-key authentication<br>Provider usage + timing</div><span class="arrow">&#8594;</span>
<div><strong>Validation</strong><br>Canonical JSON schema<br>Explicit error records</div><span class="arrow">&#8594;</span>
<div><strong>Evaluation</strong><br>Gold comparison<br>Overall + category metrics</div></div>
{table(["Deployment", "Interface", "Extraction approach"], [
["gpt-5.4", "OpenAI-compatible chat completions; JSON object mode", "Generate intent and entity strings from question/history, guide and canonical schema"],
["Decision-1", "TypeSafe system_one; compact-v1", "One intent Choice plus roughly 197 candidate membership Nouls; accept probability >= 0.5"],
["gpt-6.1-terra", "OpenAI-compatible chat completions; JSON object mode", "Same generative adapter as GPT-5.4"],
])}
<p>Deployment names are the configured identifiers; underlying model builds, versions and hardware were not
independently verified. Decision-1 receives predefined catalog candidates plus format examples and matching text
spans; GPT generates strings. This is a comparison of <strong>different complete adapters</strong>, not identical
prompts or equivalent open-world extraction. Expected records are not sent as inputs. However, the GPT guide
includes worked labeled examples and the synthetic benchmark reuses the same domain/rules; prompt-example overlap
is a potential source of optimistic performance, not evidence of an independent blind evaluation.</p>
<h3>Execution and recovery</h3>
<ol><li>Early Decision-1 chat-completions calls returned 404; the TypeSafe interface resolved that protocol mismatch.</li>
<li>The first 250-case, three-deployment run retained 250 GPT-5.4 results, 250 Decision-1 context-limit failures,
and 250 gpt-6.1-terra results.</li>
<li>Decision-1's guide/catalog payload exceeded its 32,768-token per-question limit. A compact revision omitted
worked examples and redundant instructions while retaining normative rules and aliases.</li>
<li>A Decision-1-only rerun completed all 250 cases. Four connection failures were retried successfully; recovered
records retain retry provenance. The combined file replaces only the old Decision-1 records and preserves GPT
records unchanged.</li></ol>
<p>Thus, 750 is the number of retained scored records, <strong>not total API attempts</strong>: the main original run,
Decision rerun and four recovery requests alone represent 1,004 case-level executions, excluding earlier smoke
tests and possible SDK retries. The financial tables below do not claim to sum all historical experimental spend.</p>
<h3>Metric definitions</h3>
<ul><li>Intent accuracy: correct primary intent / all 250 executions, with failed or schema-invalid predictions unusable.</li>
<li>Entity exact match: all fourteen entity sets match, with no extras or missing values. Array ordering is not scored.</li>
<li>Entity micro precision/recall/F1: aggregate matches, extras and omissions across typed entity-value pairs.</li>
<li>Joint correctness: usable prediction with both correct intent and exact entities; this is stricter than entity-only exact match.</li>
<li>Schema-valid percentage is not semantic accuracy. Failure rate includes API exceptions and validation errors.</li>
<li>Latency is local elapsed execution time; P50/P95 include recorded failures/retries where represented, but recovered cases retain only successful-attempt latency.</li></ul>

<h2>5. Findings: quality and reliability</h2>
{quality_chart}
{table(["Deployment", "Intent accuracy", "Entity exact match", "Entity precision", "Entity recall", "Entity F1", "Schema valid", "Failures /250", "Joint correct"], quality_rows)}
<p>GPT-5.4's one failure was pack-size formatting (<code>1.50L</code> rather than canonical <code>1.5L</code>).
Its 99.6% intent accuracy also reflects the evaluator discarding that entire invalid prediction, not necessarily
an intent-classification mistake. gpt-6.1-terra retained five connection failures. Decision-1's zero final failures
is <strong>post-recovery reliability</strong>, not a directly comparable first-attempt failure rate.</p>
<h3>Entity exact match by category</h3>
{heatmap}
<p>The best entity exact match among these retained results is gpt-6.1-terra (238/250). The best intent accuracy is
GPT-5.4 (249/250 usable correct predictions). Decision-1 achieves 100% intent accuracy in several direct-question
categories, but only 60% in context cases. A single overall model ranking would hide these differences.</p>

<h2>6. Why Decision-1 entity extraction failed</h2>
{error_chart}
{table(["Decision-1 diagnostic", "Observed value"], [
["True-positive entity values", f"{tp:,}"],
["False-positive entity values", f"{fp:,}"],
["False-negative entity values", f"{fn:,}"],
["Cases failing from extra values only", errors["extras only"]],
["Cases failing from missing values only", errors["missing only"]],
["Cases with both errors", errors["both"]],
["Exactly matching entity objects", errors["exact"]],
["Context cases classified unsupported", f"{context_unsupported}/30"],
])}
<p>Example <strong>BENCH-001: Show sales for FreshFizz.</strong></p>
<p>Expected non-empty entities:</p><pre>{escape(json.dumps(gold_sample, indent=2))}</pre>
<p>Actual non-empty entities:</p><pre>{escape(json.dumps(actual_sample, indent=2))}</pre>
<p><strong>Evidence:</strong> many recurring incorrect values are found in the reference catalogs and normalization
examples but not the user question. The candidate builder supplies catalog values even when unmentioned and seeds
format candidates from examples. Each independent Noul probability >= 0.5 is accepted without requiring a source
span. These facts explain the adapter's exposure to false positives.</p>
<p><strong>Hypothesis, not proven internal behavior:</strong> reference-content contamination causes the model to
treat candidate/rule mentions as user-request evidence. The run did not retain per-candidate probabilities, so
confidence calibration and threshold-only remedies cannot be evaluated retrospectively. High recall (89.0%) with
very low precision (13.8%) identifies over-extraction as the dominant problem; missing candidate coverage was not
the main observed problem on this dataset. Independent binary decisions also offer no global constraint against
accepting many unrelated values.</p>

<h2>7. Token usage and estimated cost</h2>
{token_chart}
{table(["Deployment", "Input tokens", "Output tokens", "Cached input tokens", "Mean input /question", "Mean output /question"], usage_rows)}
<p>Input totals <strong>include</strong> cached tokens; do not add cached input a second time. GPT-5.4 reported
{pct(summary["gpt-5.4"]["tokens"]["cached_input_total"] / summary["gpt-5.4"]["tokens"]["input_total"])} of its input as cached.
Decision-1's request contains roughly 198 typed questions for a simple case, and its reported mean input is
{summary["Decision-1"]["tokens"]["input_mean"] / summary["gpt-5.4"]["tokens"]["input_mean"]:.2f} times GPT-5.4's.
Reported usage is provider-specific; a shared state sent once does not imply it is billed once independently of
the typed-question processing. Actual Decision input usage ranged from {min(r["input_tokens"] for r in decision):,}
to {max(r["input_tokens"] for r in decision):,} per case after compaction.</p>
{table(["Deployment", "Configured rates per 1M tokens", "Known cost components", "Complete total", "Cost /question"], [
["gpt-5.4", "Input $2.50; cached input $0.25; output $15.00",
 "Uncached input $0.146975 + cached input $0.801536 + output $0.307620", "$1.256131", "$0.005024524"],
["Decision-1", "Input $0.042; output rate unspecified",
 f'Input ${summary["Decision-1"]["cost_breakdown_usd"]["input_cost_usd"]:.9f}; output unknown', "Unknown", "Known input only: $0.001034824"],
["gpt-6.1-terra", "No configured rates", "Unknown", "Unknown", "Unknown"],
])}
<p>GPT-5.4 cost calculation:
<code>(58,790 x $2.50 + 3,206,144 x $0.25 + 20,508 x $15.00) / 1,000,000 = $1.256131</code>.
Decision known input cost:
<code>6,159,664 x $0.042 / 1,000,000 = $0.258705888</code>.</p>
<div class="callout warning"><strong>Not a complete experiment bill:</strong> rates are user-supplied configuration,
not independently verified Azure invoice rates. Decision-1 output pricing and all gpt-6.1-terra pricing are
unknown. Reported costs exclude earlier discarded runs, smoke calls, unreported usage and failed/replaced attempts.
Failed-call usage can be recorded as zero when the provider did not report it; zero recorded tokens is not proof
of zero billing. Do not rank full end-to-end costs using the Decision input-only figure.</div>

<h2>8. Performance and operational observations</h2>
{latency_chart}
{table(["Deployment", "P50 seconds", "P95 seconds", "Maximum recorded seconds"], latency_rows)}
<p>Decision-1 had a lower median than GPT-5.4 in this configuration, but not lower than gpt-6.1-terra.
These are request-level distributions, not guaranteed model service times. Network outages, sequential execution,
different interfaces, different prompt sizes, cache behavior and recovery affect comparisons. The maximum
gpt-6.1-terra elapsed time was about 7 hours; this outlier does not appear in P95. The available data cannot
separate network, SDK retry, host suspension or server processing contributions to that delay.</p>
<p>Repeat count was one. Repeat consistency is therefore <strong>unmeasured</strong>; retries after failure are not
a controlled consistency experiment. The runner now prints per-case status/latency and flushes JSONL records
immediately, addressing the original silent-console progress issue.</p>

<h2>9. Analysis, limitations and next experiments</h2>
<ol><li><strong>Ground entity candidates in the actual question/history.</strong> Remove unrelated catalog/format-example
candidates. Preserve relevant negated/historical spans for context-aware rejection. Add span extraction for
unknown names if generic entity extraction is required; a Noul cannot generate arbitrary text.</li>
<li><strong>Separate evidence from reference metadata.</strong> Supply narrow candidate-specific instructions and
aliases. Require a supporting user/history mention; do not accept reference examples as evidence.</li>
<li><strong>Resolve context explicitly.</strong> Test carry-over, substitutions, assistant references, resets and
unsupported-turn skipping independently. Twelve supported follow-ups were incorrectly rejected.</li>
<li><strong>Capture probabilities and run held-out ablations.</strong> Compare compact-v1, mention-grounded candidates
and focused rules. Calibrate thresholds on a separate development split rather than optimizing all 250 test labels.</li>
<li><strong>Improve evaluation rigor.</strong> Independently double-annotate and adjudicate labels, expand beyond
repeated templates, test unseen names, keep related template/paraphrase families together in splits, control
prompt-example overlap, repeat trials and report uncertainty.</li>
<li><strong>Test datasource routing separately.</strong> Known-source Choice selection is a natural finite-option
task, unlike open-world entity discovery. Add its own metadata registry, routing labels, abstention and multi-source
cases before claiming datasource-selection performance.</li></ol>
<p>None of the above improvements has been measured in this report. The compact prompt changed Decision-1's
representation while GPT retained the full guide/schema. Recovery was asymmetric across deployments. These factors
prevent interpreting the table as a fair, definitive comparison of model capability or production economics.</p>

<h2>10. Conclusion</h2>
<p>The harness successfully separates intent, entity correctness, typed validity and operational measurements.
The GPT adapters performed strongly on this small synthetic draft set. Decision-1 demonstrated usable intent
classification and fast typical requests after protocol/context fixes, but the current all-candidate entity
verification design failed precision and exact match. The next justified step is an evidence-grounded adapter
ablation, not a conclusion that typed models cannot perform extraction. Full cost superiority and datasource
routing capability remain unestablished.</p>

<h2>Sources and reproducibility</h2>
<ul>{"".join(f'<li><a href="{escape(path)}">{escape(label)}</a></li>' for label, path in source_links)}</ul>
<p>This report checks source hashes, verifies unchanged GPT records, enforces 750 unique execution identities,
and recomputes all saved per-model/category summaries from the raw records before rendering.</p>
<pre>venv\\Scripts\\python.exe -m scripts.build_experiment_report</pre>
<p class="muted">Generated {escape(datetime.now(timezone.utc).isoformat())}.
Charts are embedded SVG and HTML; no external libraries, tracking, network access or model calls are required.</p>
</main></body></html>"""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(report, encoding="utf-8")
    print(f"Report: {OUTPUT}")
    print("Verified 750 records, merge-source hashes, unchanged GPT records and recomputed summaries.")
    print(f"Decision diagnostics: TP={tp}, FP={fp}, FN={fn}; format extras={formats}.")


if __name__ == "__main__":
    main()
