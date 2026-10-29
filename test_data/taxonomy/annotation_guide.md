# CPG Intent and Entity Annotation Guide

## Purpose, scope and status

**Specification version:** 0.1.0

This guide is the source of truth for creating ground-truth labels for the CPG (consumer packaged goods) decision-model test harness. It defines how a human annotator assigns exactly one primary intent and a set of normalized entities to each user turn.

The guide works together with three machine-readable artifacts:

| Artifact | Role |
| --- | --- |
| `test_data/taxonomy/intents.yaml` | Closed set of intent labels, their include/exclude boundaries and the precedence order. |
| `test_data/taxonomy/entities.yaml` | Entity types, datatypes, nullability, normalization strategies, vocabularies, aliases and formats. |
| `test_data/schemas/canonical_output.schema.json` | Model-independent JSON contract that every label (and, later, every normalized model prediction) must satisfy. |
| `test_data/taxonomy/annotation_guide.md` | This document: the labeling procedure, tie-breaking rules and worked examples. |

If the documents disagree, `entities.yaml` and `intents.yaml` win for allowed values, this guide wins for procedure, and the disagreement must be raised as a change request (see [Adjudication and change control](#adjudication-and-change-control)).

All brands, manufacturers, products, retailers and geographies are fictional. The domain is deliberately generic and contains no real-company, client or program-specific terminology.

Status: This guide retains the original intent/entity annotation policy for historical experiments. The active ten-case seed set is in `test_data/cases/seed_cases.jsonl`; 155 intent-only draft target turns are in `test_data/cases/intent_cases.jsonl`. Active labels and predictions omit entities and use `test_data/schemas/intent_output.schema.json`. Models are not asked to extract entities or select datasources. The draft benchmark requires independent double annotation and adjudication before it is treated as reviewed ground truth.

## Core principles

1. **Ground truth is defined by this guide, not by a model.** Never copy, accept or "correct towards" any model output when labeling. Annotators must not see model predictions while labeling. A label is correct because this guide supports it, not because a model agrees with it.
2. **Annotate what the user asked, not what they probably meant.** Do not infer an intent or entity that the text (or established conversation context) does not support.
3. **Exactly one primary intent per user turn.** Multi-part questions are resolved with the precedence order, never with two labels.
4. **Extract only stated or contextually established entities.** Never fill in defaults, parents, siblings or likely filters.
5. **Empty is a valid answer.** An entity the user did not state is `[]`. Producing `[]` correctly is as important as extracting a value.
6. **Meaning-preserving normalization only.** Normalization changes surface form (casing, spacing, aliases, formats) and never broadens, narrows, converts or resolves meaning.
7. **Independent double annotation.** Each case is labeled independently by two annotators and disagreements are adjudicated against this guide. Every ground-truth record stores the guide version it was labeled with.

## Output format

Each annotated user turn produces one canonical instance that validates against `canonical_output.schema.json`:

- `test_case_id`: a stable identifier such as `CPG-001` (uppercase segments joined by hyphens).
- `turn_index`: only for multi-turn cases. It is the 1-based index of the user turn being annotated and counts user turns only.
- `intent`: exactly one value from `intents.yaml`.
- `entities`: an object containing **all 14 entity keys**, always. Each value is an array of canonical strings, unique, in order of first mention. A not-stated entity is `[]`.

The contract contains no model-specific fields. Probabilities, scores, abstentions, reasoning text, raw responses, token counts, latency and cost belong in the later execution-result schema, not in ground truth.

Canonical instance for GUIDE-001 ("Show promotion ROI for FreshFizz 500ml last quarter."):

```json
{
  "test_case_id": "GUIDE-001",
  "intent": "promotion_analysis",
  "entities": {
    "brand": ["FreshFizz"],
    "manufacturer": [],
    "product": [],
    "category": [],
    "sub_category": [],
    "pack_size": ["500ml"],
    "pack_type": [],
    "channel": [],
    "retailer": [],
    "geography": [],
    "promotion_type": [],
    "discount": [],
    "date_range": ["last quarter"],
    "metric": ["roi"]
  }
}
```

Canonical instance for an unsupported turn (GUIDE-024). Every entity is empty:

```json
{
  "test_case_id": "GUIDE-024",
  "intent": "unsupported",
  "entities": {
    "brand": [],
    "manufacturer": [],
    "product": [],
    "category": [],
    "sub_category": [],
    "pack_size": [],
    "pack_type": [],
    "channel": [],
    "retailer": [],
    "geography": [],
    "promotion_type": [],
    "discount": [],
    "date_range": [],
    "metric": []
  }
}
```

In this guide's worked examples the same content is written in a compact form. `Entities: brand = FreshFizz; pack_size = 500ml` lists only the non-empty entities, and every unlisted entity is `[]`. `Entities: none` means every entity is `[]`.

## Intent rules

### Procedure

1. Resolve the **effective request**. For a single-turn case it is the question itself. For a multi-turn case, apply the [multi-turn rules](#multi-turn-conversations) first.
2. Walk the `precedence` list in `intents.yaml` from top to bottom. Assign the **first** intent whose `include_when` rules match the effective request and whose `exclude_when` rules do not.
3. If the request is an in-scope CPG analytics request but nothing more specific matches, assign the default `performance_analysis`.
4. Check the `confusable_with` rules for the chosen intent before finalizing.

Precedence (highest first) and the cue that triggers each intent:

| Rank | Intent | Kind | Typical cue |
| --- | --- | --- | --- |
| 1 | `unsupported` | out of scope | No answerable CPG analytics request (out of domain, execute an action, chit-chat, content generation, uninterpretable). |
| 2 | `recommendation` | operation | "should we", "what is the best", "which ... should", "recommend". |
| 3 | `forecast` | operation | Future periods, "will", "expected", "what if", "if we run ...". |
| 4 | `comparison` | operation | Contrast language plus two or more named non-temporal comparands, or one comparand against a named benchmark. |
| 5 | `trend_analysis` | operation | Change over time: growth, decline, trend, YoY, "since", period versus period. |
| 6 | `promotion_analysis` | topic | Promotion effectiveness, ROI, lift, incremental, baseline, trade spend, promo depth or frequency, a promotion mechanic as subject. |
| 7 | `pricing_analysis` | topic | Regular, base, shelf or average price, price per unit, price gap, price index, elasticity. |
| 8 | `distribution_analysis` | topic | Numeric or weighted distribution, store count, listings, out-of-stocks. |
| 9 | `assortment_analysis` | topic | SKU counts, range composition, SKU productivity, cannibalization, share of shelf. |
| 10 | `performance_analysis` | topic (default) | Sales, volume, units, revenue, share, profit, margin, velocity, rankings, vague in-scope questions. |

Operation intents describe what kind of analysis is requested. Topic intents describe which business domain a descriptive question is about. An operation intent always beats a topic intent.

### Key boundaries

- **Level versus change.** A KPI for one period is the topic intent. Change across periods (growth, decline, trend, YoY, "since", a period-by-period series) is `trend_analysis`. The word "growth" is a change signal, not a metric. A rolling window stated as one period ("lift over the last 12 months") is still a single period.
- **Contrast versus filter.** `comparison` needs contrast language and two or more named comparands of the same non-temporal entity type, or one comparand against a named benchmark. Values listed only as a filter ("for MarketNest and Basketly") and rankings of unnamed members ("top five brands") are not comparisons.
- **Time-only contrast.** Comparands that differ only by period ("Q1 2025 versus Q1 2026") are `trend_analysis`.
- **Single relational measure.** A price gap, price index or share between two parties is one measure. It stays with its topic intent (`pricing_analysis` or `performance_analysis`).
- **Promotion versus price.** Temporary price reductions and other promotional mechanics are `promotion_analysis`. Regular, base, shelf and average price levels are `pricing_analysis`. When both appear, `promotion_analysis` wins by precedence.
- **Distribution versus assortment.** How widely an item is available is `distribution_analysis`. Which items make up a range, and how productive the range is, is `assortment_analysis`.
- **Forecast versus recommendation.** "What will happen if ..." is `forecast`. "What should we do / which option is best" is `recommendation`.
- **Recommendation versus unsupported.** Asking for advice on a CPG commercial decision is `recommendation`. Asking the system to execute an action ("raise the price", "send an email") is `unsupported`.

### Multi-intent questions

Some questions contain more than one analytic request ("Show sales and recommend whether to extend the BOGO"). For this MVP there is still exactly one primary intent, chosen by precedence over the whole effective request. Entities are extracted from **all** analytic clauses, not only the clause that decided the intent (see GUIDE-018 and GUIDE-027). Phase 2 multi-intent cases are labeled this way.

### Do not infer an unsupported intent

Label only the intent the text supports. "How is FreshFizz doing?" is `performance_analysis`, not `trend_analysis` (no change is asked for) and not `recommendation` (no action is asked for). A question that mentions a promotion only as a filter does not become a forecast or a recommendation because those would be useful next steps.

## Unsupported questions

Use `unsupported` when the turn contains **no answerable CPG commercial-analytics request**:

- Out-of-domain questions: weather, HR, IT support, general knowledge, stock prices, news.
- Requests to execute an action or transaction: change a price, place an order, send an email, book a meeting.
- Chit-chat, greetings, thanks, or content generation (poems, slogans) with no analytic request.
- Requests for data this domain does not hold: consumer personal data, competitors' confidential plans, social-media sentiment.
- Follow-ups that cannot be interpreted even with conversation context ("What about the other one?" with no antecedent).

Rules:

- When the intent is `unsupported`, **every entity is `[]`**, even if brand or retailer names appear (see GUIDE-025 and GUIDE-026). The schema enforces this.
- If a turn mixes a supported analytic request with an unsupported action, label the analytic request and ignore the action, including any entities that belong only to the action (GUIDE-023).
- Words such as "forecast" or "recommend" do not make a request in scope. A weather forecast is `unsupported` (GUIDE-024).
- A vague but in-scope question is **not** unsupported. Label it `performance_analysis` (or the applicable intent) with only the stated entities (GUIDE-015, GUIDE-016).

## Ambiguity and partial information

### Ambiguous intent

1. If exactly one reading is supported by the text, label it.
2. If several intents match, apply precedence. Precedence is the tie-breaker, not annotator preference.
3. If the question is in scope but carries no specific intent signal ("How is FreshFizz doing?", "Give me the numbers for MarketNest"), label `performance_analysis`.
4. Never pick a more specific intent because it seems more useful.

### Ambiguous entities

- Terms listed under `ambiguous_terms` in `entities.yaml` ("performance", "numbers", "growth", "pay back", "promo", "drinks", "stores") span several values and are **not extracted**. They are also not escalated.
- A controlled-vocabulary mention that matches no allowed value or alias (for example an unlisted abbreviation) is **not forced** to the nearest value. Leave it out of the label and raise a taxonomy change request.
- A name that could belong to more than one entity type, or a misspelling that could match more than one registered value, must not be guessed. Flag the case for adjudication.
- Words inside a matched proper name are not matched again as other entities. "WellCorner Pharmacy" is only a retailer, "Clearwave Home Care" is only a manufacturer and "ColaMax" never yields `cola`. The one exception is a brand that appears inside a product name ("FreshFizz Zero" yields brand FreshFizz and product FreshFizz Zero).

### Partial information

- Label exactly what is stated and leave everything else `[]`. A question with only a metric ("What was the ROI?") is labeled with only that metric (GUIDE-019).
- Never add default filters: the current year, the total market, a home country, all channels or a typical metric for the intent.
- A keyword-only request ("FreshFizz MarketNest last month") is an in-scope request with no specific intent signal: `performance_analysis` (GUIDE-030).
- When the assistant asks a clarification question, the user's answer is merged into the pending request (GUIDE-102).

### When annotators cannot decide

Do not force a label. Record the case as `needs_adjudication` in the annotation record (outside the canonical output), with a short note citing the rules in conflict. The adjudicator decides and, if the guide was unclear, raises a guide change request.

## Entity rules

### Grounding

- Extract a value only if it is **explicitly stated** in the current user turn, or **established** earlier in the conversation and carried forward by the multi-turn rules.
- **Never infer an entity from another entity.** The catalog attributes in `entities.yaml` (a brand's manufacturer and category, a product's sub-category, a retailer's channel, a city's region) are authoring aids only.

| Stated | Do not add |
| --- | --- |
| brand FreshFizz | manufacturer Silverleaf Beverages, category carbonated_soft_drinks |
| product CrunchCo Sea Salt | sub_category potato_chips, category salty_snacks |
| retailer QuickNook | channel convenience |
| city Lakeshire | geography Highlands Region or Northland |
| sub_category yogurt | category dairy |

- First-person references ("we", "our brands", "my company") are not entity values.
- Extract from the whole effective request, including clauses that do not decide the intent, but not from text that belongs only to an unsupported action.

### Exclusions

Do not extract:

- Excluded or negated values ("except Dollarden", "excluding cola", "not online").
- Breakdown dimensions and quantifiers ("by retailer", "which region", "all retailers", "every channel", "top five").
- Thresholds and scenario magnitudes that are not entity values ("price up 5%", "share above 20%").
- Words that only name an entity type or object type ("brands", "SKUs", "promotions", "stores").
- Period-over-period bases with no named period ("YoY", "versus a year ago") and time granularity words ("weekly", "by month").

### Entity types

All 14 entities are nullable, multi-valued lists of strings:

| Entity | Strategy | Notes |
| --- | --- | --- |
| `brand` | preserve | Registered spelling (FreshFizz). Also extracted from a product name that contains it. |
| `manufacturer` | preserve | Only when the company is named. Never implied by a brand. |
| `product` | preserve | Brand plus variant (FreshFizz Zero). Pack size and pack type are separate entities. |
| `category` | controlled_vocabulary | Top-level category (salty_snacks). Never implied by a brand or sub-category. |
| `sub_category` | controlled_vocabulary | Segment within a category (cola, yogurt). |
| `pack_size` | pack_size_format | 500ml, 2L, 6x330ml, 12pk. Units are never converted. |
| `pack_type` | controlled_vocabulary | can, bottle, plastic_bottle, multipack. Never implied by a pack size. |
| `channel` | controlled_vocabulary | grocery, convenience, ecommerce. Never implied by a retailer. |
| `retailer` | preserve | MarketNest, Crateway Wholesale. |
| `geography` | preserve | Country, region or city exactly as stated. No roll-up. |
| `promotion_type` | controlled_vocabulary | tpr, bogo, feature, display. Generic "promotion" or "promo" is ambiguous. |
| `discount` | discount_format | 20%, 10%-20%, >=20%, $0.50, EUR 2. Only stated promotional depths. |
| `date_range` | date_expression | last quarter, 2025-Q1, FY2025, march. Relative dates stay relative. |
| `metric` | controlled_vocabulary | roi, sales, market_share, sku_count. Generic values stay generic. |

### Multi-value

- Every entity holds all distinct values of that type in the effective request, in order of first mention.
- Conjunctions and enumerations produce several values ("feature and display" gives `promotion_type = feature, display`).
- Different aliases of the same value collapse to one canonical value.
- Comparands in a comparison or temporal contrast are several values of the same entity.
- Values form a flat set per entity. The output does not record which value attaches to which other entity.

## Null versus empty

- Every entity key is **always present**.
- A not-stated entity is the empty list `[]`. It means "the user did not mention or restrict this entity". It does **not** mean "all values" or "zero".
- JSON `null`, the empty string `""` and placeholder strings (`"unknown"`, `"n/a"`, `"none"`, `"all"`, `"any"`, `"total"`) are **never** valid. The schema rejects `null`, `""` and padded strings, and annotators must not use placeholders.
- Universal quantifiers ("all retailers"), excluded values ("except Dollarden"), ambiguous terms ("the numbers") and unsupported turns all produce `[]`.
- A mention that is stated but cannot be mapped (an unknown controlled term) is not written as `""` or as a guess. It is left out and escalated.

Invalid outputs (never produce these):

```text
"retailer": null                 -> must be []
"retailer": [""]                 -> must be []
"retailer": ["all"]              -> "all retailers" is a quantifier, must be []
"metric": "roi"                  -> must be a list: ["roi"]
"brand": ["FreshFizz", "Fresh Fizz"]  -> aliases collapse: ["FreshFizz"]
```

## Normalization quick reference

| Entity | User text | Canonical value |
| --- | --- | --- |
| brand | "fresh fizz", "FreshFizz's" | FreshFizz |
| manufacturer | "Northpeak" | Northpeak Drinks Co. |
| product | "FreshFizz Zero Sugar" | FreshFizz Zero |
| retailer | "Crateway" | Crateway Wholesale |
| geography | "the Coastal Region", "Coastal" | Coastal Region |
| category | "savory snacks", "CSD" | salty_snacks, carbonated_soft_drinks |
| sub_category | "yoghurt", "crisps" | yogurt, potato_chips |
| pack_size | "500 ml", "half litre", "6 x 330 ml", "six-pack" | 500ml, 0.5L, 6x330ml, 6pk |
| pack_type | "PET bottles", "cans" | plastic_bottle, can |
| channel | "supermarkets", "c-stores", "online" | grocery, convenience, ecommerce |
| promotion_type | "TPRs", "price promotions", "buy one get one" | tpr, tpr, bogo |
| discount | "20 percent off", "at least 20%", "between 10 and 20 percent off", "half price" | 20%, >=20%, 10%-20%, 50% |
| date_range | "Q1 2025", "fiscal 2025", "March", "YTD", "past 12 months" | 2025-Q1, FY2025, march, year to date, last 12 months |
| metric | "ROI", "promotion ROI", "ACV", "share of shelf", "volume" | roi, roi, weighted_distribution, shelf_share, sales_volume |

Rules that are easy to get wrong:

- Matching is case-insensitive. Plurals equal the singular. When aliases overlap, the longest match wins ("incremental volume" is `incremental_volume`, not `sales_volume`).
- Generic metrics stay generic. "sales" is `sales`, never `sales_value` or `sales_volume`.
- Relative dates are never resolved, and missing years are never added ("Q1" stays `Q1`, "March" stays `march`).
- Units are never converted (0.5L stays `0.5L`, 16oz stays `16oz`).
- Promotion mechanics are never converted to depths (a BOGO is not `50%`). Only a stated depth is a discount.

## Multi-turn conversations

A multi-turn case has one `test_case_id`. Each user turn is annotated separately, with its own `turn_index`.

- **MT-1 (annotate user turns).** Annotate every user turn. `turn_index` is the 1-based count of user turns. Assistant turns are context only and are never annotated.
- **MT-2 (effective request).** Each annotation is the complete effective request after context resolution, not only the words added in that turn.
- **MT-3 (context source).** Context is the effective request of the most recent supported user turn. Unsupported turns are skipped: they neither contribute to nor clear the context.
- **MT-4 (intent).** A turn with its own intent signal is labeled from that signal, applying precedence to the effective request. A turn with no intent signal ("What about CrunchCo?", "ColaMax.") inherits the previous intent.
- **MT-5 (carry-over).** Inherited entities carry forward unless MT-6 to MT-11 change them.
- **MT-6 (new type).** A value for an entity type that is not in the context is added.
- **MT-7 (same type).** A new value for an entity type already in the context replaces the inherited values of that type ("next quarter" replaces "last quarter"; "FreshFizz only" replaces two brands). If an additive cue is present ("also", "add", "too", "as well", "versus", "compare with"), the new value is appended instead.
- **MT-8 (substitution group).** A substitution cue ("what about", "how about", "instead", "switch to", "rather than") with a new value replaces every entity in that value's group, as listed in `entity_groups` in `entities.yaml`. Entities in other groups carry forward.
- **MT-9 (product hierarchy).** Any new product_scope value (manufacturer, brand, product, category, sub_category) replaces the whole product_scope group, with or without a substitution cue, unless an additive cue is present. This prevents incompatible combinations such as a new brand with an inherited product of another brand.
- **MT-10 (references and assistant content).** Explicit references ("the two", "both", "there", "that brand", "it") resolve to the values they point to anywhere earlier in the conversation, including values named only by the assistant. Values named only by the assistant are otherwise **not** inherited. An answer to an assistant clarification question is merged into the pending request.
- **MT-11 (reset).** The context resets when the user gives an explicit reset cue ("new question", "forget that", "start over", "different topic"), or when the turn is standalone. A turn is standalone when it has no continuation cue or reference (turn-initial "and", "now", "then" or "also"; "only"; "what about"; "the same"; pronouns or "there" referring back), and it states its own product_scope value and its own metric or analytic request.
- **MT-12 (no antecedent).** A follow-up that cannot be resolved because it has no antecedent ("What about the other one?" as the first turn) is `unsupported`.

The canonical instance for GUIDE-101 turn 3 shows how `turn_index` is used:

```json
{
  "test_case_id": "GUIDE-101",
  "turn_index": 3,
  "intent": "comparison",
  "entities": {
    "brand": ["FreshFizz", "CrunchCo"],
    "manufacturer": [],
    "product": [],
    "category": [],
    "sub_category": [],
    "pack_size": [],
    "pack_type": [],
    "channel": [],
    "retailer": ["MarketNest"],
    "geography": ["Northland"],
    "promotion_type": [],
    "discount": [],
    "date_range": ["last quarter"],
    "metric": ["sales"]
  }
}
```

## Worked examples

Every example below is validated by `scripts/validate_spec.py`. It checks that each label is a schema-valid canonical instance, that every open-class value is a registered canonical value, and that every brand, manufacturer, product, retailer, geography and controlled-vocabulary value can be traced to the conversation text. Unlisted entities are `[]`.

### Single-turn examples

#### GUIDE-001: Promotion ROI with brand, size and period

- **Question:** Show promotion ROI for FreshFizz 500ml last quarter.
- Intent: promotion_analysis
- Entities: brand = FreshFizz; pack_size = 500ml; date_range = last quarter; metric = roi
- Rationale: ROI is a promotion-effectiveness measure. "promotion ROI" is an alias of `roi`, and the generic word "promotion" alone is not a promotion_type. No manufacturer, category or pack_type is inferred.

#### GUIDE-002: Performance with several filters

- **Question:** What were CrunchCo sales in the grocery channel at MarketNest in Northland in 2025?
- Intent: performance_analysis
- Entities: brand = CrunchCo; channel = grocery; retailer = MarketNest; geography = Northland; date_range = 2025; metric = sales
- Rationale: A single-period sales level. The grocery channel is extracted because it is stated, not because MarketNest is a grocer.

#### GUIDE-003: Ranking of unnamed members

- **Question:** Which five brands had the highest unit sales at MarketNest last month?
- Intent: performance_analysis
- Entities: retailer = MarketNest; date_range = last month; metric = unit_sales
- Rationale: A ranking of unnamed brands is not a comparison. "brands" names an entity type and "five" is a ranking size, so brand stays `[]`.

#### GUIDE-004: Single relational price measure

- **Question:** What's the price gap between FreshFizz and ColaMax 330ml cans at Basketly?
- Intent: pricing_analysis
- Entities: brand = FreshFizz, ColaMax; pack_size = 330ml; pack_type = can; retailer = Basketly; metric = price_gap
- Rationale: A price gap is one relational measure between two parties, so it stays pricing_analysis rather than comparison. Both brands are extracted.

#### GUIDE-005: Heavy normalization

- **Question:** What is the average price of pure spring 6 x 500 ml PET bottles at Crateway in fiscal 2025?
- Intent: pricing_analysis
- Entities: brand = PureSpring; pack_size = 6x500ml; pack_type = plastic_bottle; retailer = Crateway Wholesale; date_range = FY2025; metric = average_price
- Rationale: Case-insensitive alias "pure spring" becomes PureSpring, "6 x 500 ml" becomes 6x500ml, "PET bottles" becomes plastic_bottle, "Crateway" becomes Crateway Wholesale and "fiscal 2025" becomes FY2025.

#### GUIDE-006: Product name containing a brand

- **Question:** What is the weighted distribution of FreshFizz Zero in convenience stores across the Coastal Region?
- Intent: distribution_analysis
- Entities: brand = FreshFizz; product = FreshFizz Zero; channel = convenience; geography = Coastal Region; metric = weighted_distribution
- Rationale: The brand word appears literally inside the product name, so both are extracted. The sub_category cola is not inferred from the product, and Northland is not inferred from the region.

#### GUIDE-007: Countable measure without a brand

- **Question:** How many cola SKUs does Basketly carry?
- Intent: assortment_analysis
- Entities: sub_category = cola; retailer = Basketly; metric = sku_count
- Rationale: "how many SKUs" unambiguously asks for `sku_count`. "cola" is a sub-category; carbonated_soft_drinks is not inferred.

#### GUIDE-008: Explicit comparison with promotion detail

- **Question:** Compare FreshFizz and ColaMax promo lift on 20% TPRs at MarketNest last quarter.
- Intent: comparison
- Entities: brand = FreshFizz, ColaMax; retailer = MarketNest; promotion_type = tpr; discount = 20%; date_range = last quarter; metric = promo_lift
- Rationale: Contrast language plus two named brands makes this a comparison, which beats promotion_analysis by precedence. "TPRs" is the plural of `tpr`.

#### GUIDE-009: Growth is a change signal, not a metric

- **Question:** Show sales growth for FreshFizz 500ml bottles in supermarkets last quarter.
- Intent: trend_analysis
- Entities: brand = FreshFizz; pack_size = 500ml; pack_type = bottle; channel = grocery; date_range = last quarter; metric = sales
- Rationale: "growth" asks for change across periods, so the intent is trend_analysis, and "growth" is an ambiguous term rather than a metric. "supermarkets" is an alias of grocery. This example supersedes the conceptual Phase 1 planning example that labeled the same question performance_analysis with scalar values.

#### GUIDE-010: Contrast between periods only

- **Question:** Compare MorningGrain sales in Q1 2025 versus Q1 2026.
- Intent: trend_analysis
- Entities: brand = MorningGrain; date_range = 2025-Q1, 2026-Q1; metric = sales
- Rationale: The comparands differ only by period, so this is trend_analysis, not comparison. Both named periods are date_range values.

#### GUIDE-011: Scenario forecast

- **Question:** What will ColaMax volume be in the Metro Region next quarter if we run a 25% TPR?
- Intent: forecast
- Entities: brand = ColaMax; geography = Metro Region; promotion_type = tpr; discount = 25%; date_range = next quarter; metric = sales_volume
- Rationale: A projected value under a hypothetical promotion. "volume" is an alias of `sales_volume`. The planned 25% TPR is a stated promotional depth.

#### GUIDE-012: Recommendation with no metric

- **Question:** Which promotion mechanic should we use for SnackJoy at QuickNook during the holiday season?
- Intent: recommendation
- Entities: brand = SnackJoy; retailer = QuickNook; date_range = holiday season
- Rationale: Asks which option to choose. "promotion mechanic" names an entity type, so promotion_type stays `[]`. No metric is stated.

#### GUIDE-013: Several promotion types

- **Question:** How much incremental volume did BrightWash get from feature and display at MarketNest in March?
- Intent: promotion_analysis
- Entities: brand = BrightWash; retailer = MarketNest; promotion_type = feature, display; date_range = march; metric = incremental_volume
- Rationale: The longest match "incremental volume" wins over "volume". "March" has no year, so no year is added.

#### GUIDE-014: Discount range

- **Question:** What lift did CrunchCo get from TPRs between 10 and 20 percent off in convenience over the last 12 months?
- Intent: promotion_analysis
- Entities: brand = CrunchCo; channel = convenience; promotion_type = tpr; discount = 10%-20%; date_range = last 12 months; metric = promo_lift
- Rationale: A stated discount range normalizes to low%-high%. "lift" is an alias of `promo_lift`. The rolling window is one aggregate period, not a time series, so this is not trend_analysis.

#### GUIDE-015: Vague in-scope question

- **Question:** How is FreshFizz doing?
- Intent: performance_analysis
- Entities: brand = FreshFizz
- Rationale: In scope but no specific signal, so the default intent applies. "doing" is an ambiguous term, so metric is `[]`. Neither trend nor recommendation is inferred.

#### GUIDE-016: Filter list without contrast

- **Question:** Give me the numbers for MarketNest and Basketly.
- Intent: performance_analysis
- Entities: retailer = MarketNest, Basketly
- Rationale: Two retailers are listed as a filter without contrast language, so this is not a comparison. "numbers" is an ambiguous term.

#### GUIDE-017: Promotion alias without a metric

- **Question:** How did price promotions on CrunchCo perform at MarketNest this year?
- Intent: promotion_analysis
- Entities: brand = CrunchCo; retailer = MarketNest; promotion_type = tpr; date_range = this year
- Rationale: "price promotion" is an alias of `tpr`, which makes promotional effectiveness the subject. "perform" is not a metric.

#### GUIDE-018: Two clauses, one primary intent

- **Question:** Show DailyDairy yogurt sales at Basketly last month and recommend whether to extend the BOGO.
- Intent: recommendation
- Entities: brand = DailyDairy; sub_category = yogurt; retailer = Basketly; promotion_type = bogo; date_range = last month; metric = sales
- Rationale: The recommendation clause wins by precedence. Entities come from both clauses. "DailyDairy" does not yield the dairy category.

#### GUIDE-019: Metric only

- **Question:** What was the ROI?
- Intent: promotion_analysis
- Entities: metric = roi
- Rationale: Partial information as a single turn. Only the metric is stated, and no brand, promotion or period is invented.

#### GUIDE-020: Ambiguous payback wording

- **Question:** Did the BOGO on DailyDairy yogurt at Basketly pay back?
- Intent: promotion_analysis
- Entities: brand = DailyDairy; sub_category = yogurt; retailer = Basketly; promotion_type = bogo
- Rationale: The question is about promotion effectiveness, but "pay back" could mean ROI, incremental value or profit, so it is an ambiguous term and metric is `[]`.

#### GUIDE-021: No hierarchy inference

- **Question:** What is ColaMax market share in Lakeshire year to date?
- Intent: performance_analysis
- Entities: brand = ColaMax; geography = Lakeshire; date_range = year to date; metric = market_share
- Rationale: The city is not rolled up to Highlands Region or Northland. Neither the manufacturer nor the category is inferred from the brand, and "ColaMax" does not yield `cola`.

#### GUIDE-022: Excluded value

- **Question:** What were PureSpring sales in all retailers except Dollarden last month?
- Intent: performance_analysis
- Entities: brand = PureSpring; date_range = last month; metric = sales
- Rationale: "all retailers" is a quantifier and Dollarden is excluded, so retailer is `[]`.

#### GUIDE-023: Supported request plus an action

- **Question:** Show HomeClean sales at CartCloud this year and email it to my manager.
- Intent: performance_analysis
- Entities: brand = HomeClean; retailer = CartCloud; date_range = this year; metric = sales
- Rationale: The analytic request is labeled and the email action is ignored. The ecommerce channel is not inferred from CartCloud.

#### GUIDE-024: Out-of-domain forecast

- **Question:** What's the weather forecast for Port Amberly this weekend?
- Intent: unsupported
- Entities: none
- Rationale: Weather is outside CPG analytics. The word "forecast" does not make it in scope, and Port Amberly is not extracted because the intent is unsupported.

#### GUIDE-025: Execute an action

- **Question:** Raise the FreshFizz shelf price at MarketNest to $1.99.
- Intent: unsupported
- Entities: none
- Rationale: The user asks the system to perform an action, not to analyze or advise. All entities are empty.

#### GUIDE-026: Content generation

- **Question:** Write a poem about CrunchCo.
- Intent: unsupported
- Entities: none
- Rationale: Content generation with no analytic request. The brand is not extracted.

#### GUIDE-027: Promotion beats pricing

- **Question:** What were the average price and promo depth for CrunchCo at MarketNest last quarter?
- Intent: promotion_analysis
- Entities: brand = CrunchCo; retailer = MarketNest; date_range = last quarter; metric = average_price, promo_depth
- Rationale: Both pricing and promotion topics are present. promotion_analysis ranks higher, and both metrics are extracted in order of mention.

#### GUIDE-028: Period-over-period base without a named period

- **Question:** How have FreshFizz unit sales at QuickNook changed versus a year ago?
- Intent: trend_analysis
- Entities: brand = FreshFizz; retailer = QuickNook; metric = unit_sales
- Rationale: "changed versus a year ago" is a change signal. The base "a year ago" is not a named period, so date_range is `[]`.

#### GUIDE-029: Comparison against a named benchmark

- **Question:** Is DailyDairy's weighted distribution above the dairy category average?
- Intent: comparison
- Entities: brand = DailyDairy; category = dairy; metric = weighted_distribution
- Rationale: One comparand against an explicitly named benchmark. The category is extracted because "dairy category" is stated, not because of the brand.

#### GUIDE-030: Keyword-only request

- **Question:** FreshFizz MarketNest last month
- Intent: performance_analysis
- Entities: brand = FreshFizz; retailer = MarketNest; date_range = last month
- Rationale: An in-scope request with no intent signal or metric. The default intent applies, and no metric is invented.

### Multi-turn examples

#### GUIDE-101: Substitution, comparison and a scoped forecast

- **Turn 1 (user):** What were FreshFizz sales in Northland last quarter?
  - Intent: performance_analysis
  - Entities: brand = FreshFizz; geography = Northland; date_range = last quarter; metric = sales
  - Rationale: A complete single-period sales question.
- **Assistant:** Reports FreshFizz sales in Northland for last quarter.
- **Turn 2 (user):** What about CrunchCo?
  - Intent: performance_analysis
  - Entities: brand = CrunchCo; geography = Northland; date_range = last quarter; metric = sales
  - Rationale: MT-4 inherits the intent. MT-8 and MT-9 replace the product_scope group with CrunchCo. Geography, date and metric carry over.
- **Assistant:** Reports CrunchCo sales in Northland for last quarter.
- **Turn 3 (user):** Now compare the two at MarketNest.
  - Intent: comparison
  - Entities: brand = FreshFizz, CrunchCo; retailer = MarketNest; geography = Northland; date_range = last quarter; metric = sales
  - Rationale: "the two" refers back to both brands (MT-10). Contrast language plus two named comparands gives comparison. MarketNest is a new entity type and is added (MT-6).
- **Assistant:** Compares the two brands at MarketNest.
- **Turn 4 (user):** Forecast next quarter for FreshFizz only.
  - Intent: forecast
  - Entities: brand = FreshFizz; retailer = MarketNest; geography = Northland; date_range = next quarter; metric = sales
  - Rationale: "only" restricts the inherited scope, so this is a continuation, not a reset. The brand and date are replaced (MT-7). Retailer, geography and metric carry over.

#### GUIDE-102: Answer to a clarification question

- **Turn 1 (user):** Show me the promo lift at QuickNook last summer.
  - Intent: promotion_analysis
  - Entities: retailer = QuickNook; date_range = last summer; metric = promo_lift
  - Rationale: No brand is stated, and none is invented.
- **Assistant:** Which brand would you like to see?
- **Turn 2 (user):** ColaMax.
  - Intent: promotion_analysis
  - Entities: brand = ColaMax; retailer = QuickNook; date_range = last summer; metric = promo_lift
  - Rationale: The answer to a clarification question is merged into the pending request (MT-10). The intent is inherited (MT-4).

#### GUIDE-103: Group substitution and explicit reset

- **Turn 1 (user):** What is the weighted distribution of FreshFizz Zero at QuickNook?
  - Intent: distribution_analysis
  - Entities: brand = FreshFizz; product = FreshFizz Zero; retailer = QuickNook; metric = weighted_distribution
  - Rationale: The product name contains the brand.
- **Assistant:** Reports the weighted distribution of FreshFizz Zero at QuickNook.
- **Turn 2 (user):** Switch to energy drinks instead.
  - Intent: distribution_analysis
  - Entities: sub_category = energy_drinks; retailer = QuickNook; metric = weighted_distribution
  - Rationale: A substitution cue with a product_scope value replaces the whole product_scope group (MT-8), so brand and product are cleared. Retailer and metric carry over.
- **Assistant:** Reports weighted distribution for energy drinks at QuickNook.
- **Turn 3 (user):** New question: should Basketly delist any salty snacks SKUs?
  - Intent: recommendation
  - Entities: category = salty_snacks; retailer = Basketly
  - Rationale: "New question" resets the context (MT-11), so QuickNook and weighted_distribution are dropped. "SKUs" names an object type, and no metric is stated.

#### GUIDE-104: Assistant-named value and a skipped unsupported turn

- **Turn 1 (user):** Which region had the highest CrunchCo sales last month?
  - Intent: performance_analysis
  - Entities: brand = CrunchCo; date_range = last month; metric = sales
  - Rationale: "which region" is a breakdown dimension, so geography is `[]`.
- **Assistant:** The Highlands Region had the highest CrunchCo sales last month.
- **Turn 2 (user):** Thanks. Can you set up a meeting with the sales team?
  - Intent: unsupported
  - Entities: none
  - Rationale: Thanks plus an action request. All entities are empty.
- **Assistant:** I can't schedule meetings.
- **Turn 3 (user):** What was the promo depth there?
  - Intent: promotion_analysis
  - Entities: brand = CrunchCo; geography = Highlands Region; date_range = last month; metric = promo_depth
  - Rationale: The unsupported turn is skipped (MT-3), so context comes from turn 1. "there" refers to the region the assistant named (MT-10). promo_depth replaces sales (MT-7), and its promotional subject gives promotion_analysis.

#### GUIDE-105: Additive cue, metric substitution and a standalone reset

- **Turn 1 (user):** Show PureSpring unit sales at MarketNest in the Coastal Region this year.
  - Intent: performance_analysis
  - Entities: brand = PureSpring; retailer = MarketNest; geography = Coastal Region; date_range = this year; metric = unit_sales
  - Rationale: A complete single-period performance question.
- **Assistant:** Reports PureSpring unit sales at MarketNest in the Coastal Region this year.
- **Turn 2 (user):** Add Basketly as well.
  - Intent: performance_analysis
  - Entities: brand = PureSpring; retailer = MarketNest, Basketly; geography = Coastal Region; date_range = this year; metric = unit_sales
  - Rationale: The additive cues "add" and "as well" append the retailer instead of replacing it (MT-7).
- **Assistant:** Adds Basketly to the view.
- **Turn 3 (user):** What about value share?
  - Intent: performance_analysis
  - Entities: brand = PureSpring; retailer = MarketNest, Basketly; geography = Coastal Region; date_range = this year; metric = value_share
  - Rationale: A substitution cue with a new metric replaces the measure group (MT-8). Everything else carries over.
- **Assistant:** Reports PureSpring value share at both retailers.
- **Turn 4 (user):** What were CrunchCo unit sales at QuickNook last month?
  - Intent: performance_analysis
  - Entities: brand = CrunchCo; retailer = QuickNook; date_range = last month; metric = unit_sales
  - Rationale: A standalone turn with no continuation cue that states its own product scope and metric, so the context resets (MT-11). Coastal Region is not inherited.

## Annotation checklist

Before submitting a label, confirm each item:

1. The effective request was resolved first (multi-turn rules applied, unsupported turns skipped).
2. Exactly one intent was chosen by walking the precedence list, and the `confusable_with` rules were checked.
3. `unsupported` was used only when no CPG analytics request exists, and all of its entities are `[]`.
4. Every extracted value is stated in the turn or established by context. Nothing was inferred from another entity, from catalog attributes or from a typical default.
5. Excluded values, quantifiers, breakdown dimensions, thresholds, type words and ambiguous terms were not extracted.
6. Every value uses its canonical form (registered spelling, snake_case vocabulary, pack_size, discount or date format) with no unit conversion and no date resolution.
7. All 14 entity keys are present. Missing entities are `[]`, never `null`, `""` or a placeholder.
8. Lists are unique and in order of first mention, with aliases collapsed.
9. Any unmappable controlled term or unresolvable ambiguity was escalated, not guessed.
10. The record stores the guide version (0.1.0) and the annotator ID. No model output was consulted.

## Adjudication and change control

### Adjudication

- Two annotators label each case independently. The adjudicator resolves every disagreement by citing the rule in this guide that decides it.
- If no rule decides the case, the adjudicator records a provisional label, marks the case `needs_guide_update` and raises a change request. The case is not certified until the guide is updated.
- Inter-annotator agreement is tracked per intent and per entity during Phase 2 to find unclear rules. Agreement is a quality signal for the guide, not a substitute for adjudication.

### Change control

- `intents.yaml`, `entities.yaml`, this guide and the schema share one `spec_version` (currently 0.1.0) and change together.
- Patch version: wording clarifications that do not change any label. Minor version: new aliases, values, examples or rules that may change labels. Major version: changes to the intent set, entity set or output contract.
- After any minor or major change, re-label the affected ground-truth cases and record the new version on them.
- New aliases must not collide with existing values, aliases or ambiguous terms. `scripts/validate_spec.py` enforces this.
- Run `python scripts/validate_spec.py` after every change. A change is not accepted until validation passes.
