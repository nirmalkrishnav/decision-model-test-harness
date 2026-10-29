"""Build the intent-only draft benchmark from explicit, model-independent labels."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
COUNTS = {
    "simple_intent": 40,
    "ambiguous_questions": 25,
    "multi_intent": 25,
    "multi_turn_context": 30,
    "paraphrases": 20,
    "adversarial_boundary": 15,
}
BRANDS = ["FreshFizz", "ColaMax", "PureSpring", "SnackJoy", "CrunchCo"]


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    source_index = 0

    def add(
        category: str,
        question: str,
        intent: str,
        _labels: dict[str, list[str]],
        _rationale: str,
        *,
        context: list[dict[str, str]] | None = None,
        paraphrase_group: str | None = None,
    ) -> None:
        nonlocal source_index
        source_index += 1
        if category not in COUNTS:
            return
        expected: dict[str, Any] = {
            "test_case_id": f"BENCH-{source_index:03d}",
            "intent": intent,
        }
        case: dict[str, Any] = {
            "category": category,
            "question": question,
            "spec_version": "0.1.0",
            "annotation_status": "draft",
            "evaluation_scope": "intent_only",
            "rationale": f"Classify the effective request as {intent} using intent precedence and context rules.",
            "expected": expected,
        }
        if context:
            case["context"] = context
            expected["turn_index"] = 1 + sum(turn["role"] == "user" for turn in context)
        if paraphrase_group:
            case["paraphrase_group"] = paraphrase_group
        cases.append(case)

    simple = [
        ("Show sales for {b}.", "performance_analysis", "sales"),
        ("What was promotion ROI for {b}?", "promotion_analysis", "roi"),
        ("What is the regular price of {b}?", "pricing_analysis", "base_price"),
        ("Report numeric distribution for {b}.", "distribution_analysis", "numeric_distribution"),
        ("How many SKUs does {b} have?", "assortment_analysis", "sku_count"),
        ("Show sales growth for {b}.", "trend_analysis", "sales"),
        ("Forecast sales for {b}.", "forecast", "sales"),
        ("Should we increase distribution for {b}?", "recommendation", "distribution"),
    ]
    for template, intent, metric in simple:
        for brand in BRANDS:
            add("simple_intent", template.format(b=brand), intent,
                {"brand": [brand], "metric": [metric]},
                "One explicit analytic request; only the named brand and measure are extracted.")

    products = [
        ("FreshFizz Zero", "FreshFizz"),
        ("ColaMax Cherry", "ColaMax"),
        ("PureSpring Sparkling Lime", "PureSpring"),
        ("SnackJoy Tortilla Rounds", "SnackJoy"),
        ("CrunchCo Sea Salt", "CrunchCo"),
    ]
    detailed = [
        ("Show unit sales for {p} in 500ml cans at MarketNest in Northland last quarter.",
         "performance_analysis", {"pack_size": ["500ml"], "pack_type": ["can"], "retailer": ["MarketNest"],
                                  "geography": ["Northland"], "date_range": ["last quarter"], "metric": ["unit_sales"]}),
        ("Report ROI for 20% off BOGO promotions on {p} in grocery in Coastal Region during 2025.",
         "promotion_analysis", {"discount": ["20%"], "promotion_type": ["bogo"], "channel": ["grocery"],
                                "geography": ["Coastal Region"], "date_range": ["2025"], "metric": ["roi"]}),
        ("Show the base price of {p} in 150g bags at Basketly in Southmere last month.",
         "pricing_analysis", {"pack_size": ["150g"], "pack_type": ["bag"], "retailer": ["Basketly"],
                              "geography": ["Southmere"], "date_range": ["last month"], "metric": ["base_price"]}),
        ("Report weighted distribution of {p} in convenience at QuickNook in Lakeshire this year.",
         "distribution_analysis", {"channel": ["convenience"], "retailer": ["QuickNook"],
                                   "geography": ["Lakeshire"], "date_range": ["this year"], "metric": ["weighted_distribution"]}),
        ("Show share of shelf for {p} in grocery at MarketNest in Highlands Region in 2025-Q1.",
         "assortment_analysis", {"channel": ["grocery"], "retailer": ["MarketNest"], "geography": ["Highlands Region"],
                                 "date_range": ["2025-Q1"], "metric": ["shelf_share"]}),
        ("Show sales growth for {p} in 6x330ml cans in ecommerce in Metro Region since january.",
         "trend_analysis", {"pack_size": ["6x330ml"], "pack_type": ["can"], "channel": ["ecommerce"],
                            "geography": ["Metro Region"], "date_range": ["since january"], "metric": ["sales"]}),
        ("Forecast net revenue for {p} at CartCloud in Eastvale next quarter.",
         "forecast", {"retailer": ["CartCloud"], "geography": ["Eastvale"], "date_range": ["next quarter"],
                      "metric": ["net_revenue"]}),
        ("Compare sales for {p} at MarketNest versus Basketly in Northland during 2025.",
         "comparison", {"retailer": ["MarketNest", "Basketly"], "geography": ["Northland"],
                        "date_range": ["2025"], "metric": ["sales"]}),
    ]
    for template, intent, labels in detailed:
        for product, brand in products:
            add("multiple_entities", template.format(p=product), intent,
                {"brand": [brand], "product": [product], **labels},
                "Brand is literal within the product; no implied category, manufacturer or retailer channel.")

    normalization = [
        ("Show offtake for Fresh Fizz.", "performance_analysis", {"brand": ["FreshFizz"], "metric": ["sales"]}),
        ("Show units sold for Cola Max.", "performance_analysis", {"brand": ["ColaMax"], "metric": ["unit_sales"]}),
        ("Show net sales for Pure Spring.", "performance_analysis", {"brand": ["PureSpring"], "metric": ["net_revenue"]}),
        ("Show dollar share for Snack Joy.", "performance_analysis", {"brand": ["SnackJoy"], "metric": ["value_share"]}),
        ("Show rate of sale for Crunch Co.", "performance_analysis", {"brand": ["CrunchCo"], "metric": ["sales_velocity"]}),
        ("Show sales for FreshFizz Zero Sugar.", "performance_analysis",
         {"brand": ["FreshFizz"], "product": ["FreshFizz Zero"], "metric": ["sales"]}),
        ("Show sales for FreshFizz Classic.", "performance_analysis",
         {"brand": ["FreshFizz"], "product": ["FreshFizz Original"], "metric": ["sales"]}),
        ("Show sales for CrunchCo Barbecue.", "performance_analysis",
         {"brand": ["CrunchCo"], "product": ["CrunchCo BBQ"], "metric": ["sales"]}),
        ("Show sales for Northpeak.", "performance_analysis", {"manufacturer": ["Northpeak Drinks Co."], "metric": ["sales"]}),
        ("Show sales at Crateway.", "performance_analysis", {"retailer": ["Crateway Wholesale"], "metric": ["sales"]}),
        ("Show sales in Coastal.", "performance_analysis", {"geography": ["Coastal Region"], "metric": ["sales"]}),
        ("Show sales for CSD in supermarkets.", "performance_analysis",
         {"category": ["carbonated_soft_drinks"], "channel": ["grocery"], "metric": ["sales"]}),
        ("Show sales for savory snacks online.", "performance_analysis",
         {"category": ["salty_snacks"], "channel": ["ecommerce"], "metric": ["sales"]}),
        ("Show sales for crisps in c-stores.", "performance_analysis",
         {"sub_category": ["potato_chips"], "channel": ["convenience"], "metric": ["sales"]}),
        ("Show sales for yoghurt in pharmacies.", "performance_analysis",
         {"sub_category": ["yogurt"], "channel": ["drug"], "metric": ["sales"]}),
        ("Show sales of FreshFizz 500 ml PET bottles.", "performance_analysis",
         {"brand": ["FreshFizz"], "pack_size": ["500ml"], "pack_type": ["plastic_bottle"], "metric": ["sales"]}),
        ("Show sales of PureSpring half litre bottles.", "performance_analysis",
         {"brand": ["PureSpring"], "pack_size": ["0.5L"], "pack_type": ["bottle"], "metric": ["sales"]}),
        ("Show sales of ColaMax 6 x 330 ml cans.", "performance_analysis",
         {"brand": ["ColaMax"], "pack_size": ["6x330ml"], "pack_type": ["can"], "metric": ["sales"]}),
        ("Show sales of FreshFizz six-pack.", "performance_analysis",
         {"brand": ["FreshFizz"], "pack_size": ["6pk"], "metric": ["sales"]}),
        ("Show sales of PureSpring 1.50L bottles.", "performance_analysis",
         {"brand": ["PureSpring"], "pack_size": ["1.5L"], "pack_type": ["bottle"], "metric": ["sales"]}),
        ("Show ROI for buy one get one offers.", "promotion_analysis", {"promotion_type": ["bogo"], "metric": ["roi"]}),
        ("Show ROI for temporary price reductions.", "promotion_analysis", {"promotion_type": ["tpr"], "metric": ["roi"]}),
        ("Show promotion ROI for 20 percent off.", "promotion_analysis", {"discount": ["20%"], "metric": ["roi"]}),
        ("Show promotion ROI for at least 20% off.", "promotion_analysis", {"discount": [">=20%"], "metric": ["roi"]}),
        ("Show promotion ROI for half price offers.", "promotion_analysis", {"discount": ["50%"], "metric": ["roi"]}),
        ("Show sales in Q1 2025.", "performance_analysis", {"date_range": ["2025-Q1"], "metric": ["sales"]}),
        ("Show sales in fiscal 2025.", "performance_analysis", {"date_range": ["FY2025"], "metric": ["sales"]}),
        ("Show sales YTD.", "performance_analysis", {"date_range": ["year to date"], "metric": ["sales"]}),
        ("Show sales in March 2026.", "performance_analysis", {"date_range": ["2026-03"], "metric": ["sales"]}),
        ("Show ACV for FreshFizz over the past 12 months.", "distribution_analysis",
         {"brand": ["FreshFizz"], "date_range": ["last 12 months"], "metric": ["weighted_distribution"]}),
    ]
    for question, intent, labels in normalization:
        add("entity_normalization", question, intent, labels,
            "Apply the taxonomy alias or format rule; preserve meaning without unit conversion or inferred parents.")

    missing = [
        ("What was the ROI?", "promotion_analysis", {"metric": ["roi"]}),
        ("Show sales.", "performance_analysis", {"metric": ["sales"]}),
        ("Report the base price.", "pricing_analysis", {"metric": ["base_price"]}),
        ("Show weighted distribution.", "distribution_analysis", {"metric": ["weighted_distribution"]}),
        ("How many SKUs are listed?", "assortment_analysis", {"metric": ["sku_count"]}),
        ("Forecast sales.", "forecast", {"metric": ["sales"]}),
        ("Show sales growth.", "trend_analysis", {"metric": ["sales"]}),
        ("Recommend a promotion mechanic.", "recommendation", {}),
        ("Show the performance.", "performance_analysis", {}),
        ("Give me the numbers.", "performance_analysis", {}),
        ("Show results for all brands.", "performance_analysis", {}),
        ("Show sales by retailer.", "performance_analysis", {"metric": ["sales"]}),
        ("Show sales across all channels.", "performance_analysis", {"metric": ["sales"]}),
        ("Show sales nationally.", "performance_analysis", {"metric": ["sales"]}),
        ("Show sales for large packs.", "performance_analysis", {"metric": ["sales"]}),
        ("Show sales for family size packaging.", "performance_analysis", {"metric": ["sales"]}),
        ("Show sales for packs over 1L.", "performance_analysis", {"metric": ["sales"]}),
        ("Show weekly sales.", "performance_analysis", {"metric": ["sales"]}),
        ("Show sales for our brands.", "performance_analysis", {"metric": ["sales"]}),
        ("Show sales except FreshFizz.", "performance_analysis", {"metric": ["sales"]}),
        ("Show sales excluding online.", "performance_analysis", {"metric": ["sales"]}),
        ("Show sales except Dollarden.", "performance_analysis", {"metric": ["sales"]}),
        ("Report promotion effectiveness.", "promotion_analysis", {}),
        ("Show sales for packs.", "performance_analysis", {"metric": ["sales"]}),
        ("What was the regular price last quarter?", "pricing_analysis",
         {"metric": ["base_price"], "date_range": ["last quarter"]}),
    ]
    for question, intent, labels in missing:
        add("missing_null_entities", question, intent, labels,
            "Unstated, universal, negated or ambiguous entity values are []; never use JSON null or defaults.")

    ambiguous = [
        ("How is {b} doing?", {}),
        ("Give me the numbers for {b}.", {}),
        ("Show {b} results.", {}),
        ("{b} last month", {"date_range": ["last month"]}),
        ("Show {b} performance in stores.", {}),
    ]
    for template, extra in ambiguous:
        for brand in BRANDS:
            add("ambiguous_questions", template.format(b=brand), "performance_analysis",
                {"brand": [brand], **extra},
                "Vague but in-scope: default performance_analysis; performance/numbers/stores are not specific entities.")

    multi_intent = [
        ("Show sales and recommend whether to extend BOGO for {b}.", "recommendation",
         {"metric": ["sales"], "promotion_type": ["bogo"]}),
        ("Show ROI and forecast sales for {b} next quarter.", "forecast",
         {"metric": ["roi", "sales"], "date_range": ["next quarter"]}),
        ("Compare base price and sales for {b} at MarketNest versus Basketly.", "comparison",
         {"metric": ["base_price", "sales"], "retailer": ["MarketNest", "Basketly"]}),
        ("Show sales growth and promotion ROI for {b}.", "trend_analysis",
         {"metric": ["sales", "roi"]}),
        ("Show promotion ROI and regular price for {b}.", "promotion_analysis",
         {"metric": ["roi", "base_price"]}),
    ]
    for template, intent, extra in multi_intent:
        for brand in BRANDS:
            add("multi_intent", template.format(b=brand), intent, {"brand": [brand], **extra},
                "Select the highest-precedence applicable intent; extract entities from all supported clauses.")

    for index, brand in enumerate(BRANDS[:3]):
        other = BRANDS[(index + 1) % len(BRANDS)]
        base = f"Show sales for {brand} at MarketNest in Northland last quarter."
        context = [{"role": "user", "content": base}]
        common = {"retailer": ["MarketNest"], "geography": ["Northland"],
                  "date_range": ["last quarter"], "metric": ["sales"]}
        add("multi_turn_context", f"What about {other}?", "performance_analysis",
            {"brand": [other], **common}, "MT-8/MT-9: replace product scope; keep market, date and metric.",
            context=context)
        add("multi_turn_context", "And last month?", "performance_analysis",
            {"brand": [brand], **common, "date_range": ["last month"]},
            "MT-4/MT-7: inherit descriptive intent; replace the prior date.",
            context=context)
        add("multi_turn_context", f"Also include {other}.", "performance_analysis",
            {"brand": [brand, other], **common},
            "MT-7/MT-9: additive cue appends brand while retaining prior filters.", context=context)
        add("multi_turn_context", "Now show ROI there.", "promotion_analysis",
            {"brand": [brand], **common, "metric": ["roi"]},
            "MT-4/MT-7/MT-10: explicit ROI changes intent and metric; there resolves to Northland.",
            context=context)
        add("multi_turn_context", f"New question: show regular price for {other}.", "pricing_analysis",
            {"brand": [other], "metric": ["base_price"]},
            "MT-11: explicit reset clears every prior filter.", context=context)
        add("multi_turn_context", f"What about {other} only?", "performance_analysis",
            {"brand": [other], **common},
            "MT-3: unsupported user turns neither contribute nor clear prior supported context.",
            context=context + [{"role": "user", "content": "Thank you!"}])
        add("multi_turn_context", "What about QuickNook?", "performance_analysis",
            {"brand": [brand], "retailer": ["QuickNook"], "date_range": ["last quarter"], "metric": ["sales"]},
            "MT-8: retailer substitution clears the whole market_scope group, including prior geography.",
            context=context)
        add("multi_turn_context", f"What about {other} instead?", "performance_analysis",
            {"brand": [other], **common},
            "MT-9: changing brand removes the earlier brand and stated category.",
            context=[{"role": "user", "content":
                      f"Show sales for {brand} in the soda category at MarketNest in Northland last quarter."}])
        add("multi_turn_context", "MarketNest.", "performance_analysis",
            {"brand": [brand], "retailer": ["MarketNest"], "date_range": ["last quarter"], "metric": ["sales"]},
            "MT-10: merge the answer to an assistant clarification into the pending request.",
            context=[
                {"role": "user", "content": f"Show sales for {brand} last quarter."},
                {"role": "assistant", "content": "Which retailer?"},
            ])
        add("multi_turn_context", "Compare the two retailers.", "comparison",
            {"brand": [brand], "retailer": ["MarketNest", "Basketly"],
             "date_range": ["last quarter"], "metric": ["sales"]},
            "MT-10: an explicit reference resolves retailer values named by the assistant.",
            context=[
                {"role": "user", "content": f"Show sales for {brand} last quarter."},
                {"role": "assistant", "content": "We can look at MarketNest and Basketly."},
            ])

    paraphrases = [
        ("promotion_analysis", "roi", [
            "What was promotion ROI for FreshFizz last quarter?",
            "Report last quarter's return on investment for FreshFizz promotions.",
            "For FreshFizz promotions last quarter, give the ROI.",
            "Provide the return on investment of FreshFizz promotions last quarter.",
        ]),
        ("pricing_analysis", "base_price", [
            "What was FreshFizz's regular price last quarter?",
            "Show the base price of FreshFizz last quarter.",
            "Report FreshFizz shelf price for last quarter.",
            "Give the everyday price for FreshFizz in the last quarter.",
        ]),
        ("distribution_analysis", "weighted_distribution", [
            "Show FreshFizz weighted distribution last quarter.",
            "What was ACV for FreshFizz last quarter?",
            "Report FreshFizz WD in the last quarter.",
            "Give weighted dist for FreshFizz for last quarter.",
        ]),
        ("performance_analysis", "unit_sales", [
            "Show units sold for FreshFizz last quarter.",
            "How many units of FreshFizz were sold last quarter?",
            "Report FreshFizz unit sales last quarter.",
            "Give FreshFizz packs sold for last quarter.",
        ]),
        ("trend_analysis", "sales", [
            "Show sales growth for FreshFizz last quarter.",
            "Report the decline in FreshFizz sales last quarter.",
            "How did FreshFizz sales change last quarter?",
            "Give the sales trend for FreshFizz during last quarter.",
        ]),
    ]
    for group_index, (intent, metric, questions) in enumerate(paraphrases, start=1):
        for question in questions:
            add("paraphrases", question, intent,
                {"brand": ["FreshFizz"], "metric": [metric], "date_range": ["last quarter"]},
                "Equivalent intent and entities across wording variants; change direction does not alter trend intent.",
                paraphrase_group=f"PARA-{group_index:02d}")

    boundary = [
        ("Write a slogan for FreshFizz at MarketNest.", "unsupported", {}),
        ("Forecast the weather in Northland.", "unsupported", {}),
        ("Change ColaMax's price at Basketly to $1.", "unsupported", {}),
        ("What about the other one?", "unsupported", {}),
        ("Hello, FreshFizz!", "unsupported", {}),
        ("Show sales for FreshFizz and email the results to Basketly.", "performance_analysis",
         {"brand": ["FreshFizz"], "metric": ["sales"]}),
        ("Show sales for FreshFizz, not ColaMax, in grocery, not online.", "performance_analysis",
         {"brand": ["FreshFizz"], "channel": ["grocery"], "metric": ["sales"]}),
        ("Show sales for WellCorner Pharmacy.", "performance_analysis",
         {"retailer": ["WellCorner Pharmacy"], "metric": ["sales"]}),
        ("Show sales for Clearwave Home Care.", "performance_analysis",
         {"manufacturer": ["Clearwave Home Care"], "metric": ["sales"]}),
        ("Show sales for FreshFizz Zero.", "performance_analysis",
         {"brand": ["FreshFizz"], "product": ["FreshFizz Zero"], "metric": ["sales"]}),
        ("Show FreshFizz sales in Q1 2025 versus Q1 2026.", "trend_analysis",
         {"brand": ["FreshFizz"], "date_range": ["2025-Q1", "2026-Q1"], "metric": ["sales"]}),
        ("Show sales for FreshFizz and ColaMax.", "performance_analysis",
         {"brand": ["FreshFizz", "ColaMax"], "metric": ["sales"]}),
        ("What is the price gap between FreshFizz and ColaMax?", "pricing_analysis",
         {"brand": ["FreshFizz", "ColaMax"], "metric": ["price_gap"]}),
        ("Show ROI for FreshFizz BOGO; do not assume a 50% discount.", "promotion_analysis",
         {"brand": ["FreshFizz"], "promotion_type": ["bogo"], "metric": ["roi"]}),
        ("Show FreshFizz sales where market share exceeds 20%.", "performance_analysis",
         {"brand": ["FreshFizz"], "metric": ["sales", "market_share"]}),
    ]
    for question, intent, labels in boundary:
        add("adversarial_boundary", question, intent, labels,
            "Apply unsupported/exclusion/longest-name/contrast rules; do not infer entities from lexical traps.")

    actual_counts = Counter(case["category"] for case in cases)
    if actual_counts != COUNTS:
        raise ValueError(f"Incorrect category counts: {actual_counts}")
    return cases


def main() -> None:
    schema = json.loads(
        (ROOT / "test_data" / "schemas" / "intent_output.schema.json").read_text(encoding="utf-8")
    )
    cases = build_cases()
    for case in cases:
        jsonschema.validate(case["expected"], schema)
    output = ROOT / "test_data" / "cases" / "intent_cases.jsonl"
    output.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8"
    )
    seed_path = ROOT / "test_data" / "cases" / "seed_cases.jsonl"
    seeds = [json.loads(line) for line in seed_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for seed in seeds:
        seed["evaluation_scope"] = "intent_only"
        seed["expected"].pop("entities", None)
    seed_path.write_text(
        "".join(json.dumps(seed, ensure_ascii=False) + "\n" for seed in seeds), encoding="utf-8"
    )
    print(f"Wrote {len(cases)} schema-valid draft cases to {output}")
    for category, count in COUNTS.items():
        print(f"  {category}: {count}")


if __name__ == "__main__":
    main()
