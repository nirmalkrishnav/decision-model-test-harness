#!/usr/bin/env python3
"""Offline validator for the Phase 1 specification artifacts.

Usage:
    python scripts/validate_spec.py [--root PATH]

Validates, without calling any AI model or network service:
  * syntax of intents.yaml, entities.yaml and canonical_output.schema.json
    (strict UTF-8, no BOM, duplicate keys rejected);
  * internal consistency of the intent and entity taxonomies;
  * alias collisions across every vocabulary and reference-value list;
  * the JSON Schema itself (meta-schema check when jsonschema is installed,
    plus a built-in keyword lint) and its consistency with the YAML files;
  * schema self-tests (instances that must pass and instances that must fail);
  * the annotation guide: structure, links, versions and every worked example
    (schema validity, registered values, grounding in the conversation text,
    agreement with the JSON examples and with intents.yaml examples);
  * absence of real-company names and model or network client imports in scripts/.

Exit codes:
    0  all checks passed
    1  one or more checks failed
    2  PyYAML is not installed
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # reported in main()
    yaml = None

try:
    import jsonschema
except ImportError:  # optional; the built-in validator is always used
    jsonschema = None

INTENTS_REL = "test_data/taxonomy/intents.yaml"
ENTITIES_REL = "test_data/taxonomy/entities.yaml"
GUIDE_REL = "test_data/taxonomy/annotation_guide.md"
SCHEMA_REL = "test_data/schemas/canonical_output.schema.json"

ARTIFACTS = [
    ("intents", INTENTS_REL, "yaml"),
    ("entities", ENTITIES_REL, "yaml"),
    ("schema", SCHEMA_REL, "json"),
    ("guide", GUIDE_REL, "text"),
]

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
SNAKE_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
SNAKE_TOKEN_RE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
CLEAN_TEXT_RE = re.compile(r"^\S+(?: \S+)*$")
MAX_TEXT_LENGTH = 200

# ---------------------------------------------------------------------------
# Expected structure
# ---------------------------------------------------------------------------

REQUIRED_INTENTS = [
    "performance_analysis", "promotion_analysis", "pricing_analysis",
    "distribution_analysis", "assortment_analysis", "comparison",
    "trend_analysis", "forecast", "recommendation", "unsupported",
]
REQUIRED_ENTITIES = [
    "brand", "manufacturer", "product", "category", "sub_category",
    "pack_size", "pack_type", "channel", "retailer", "geography",
    "promotion_type", "discount", "date_range", "metric",
]

INTENT_TOP_KEYS = ["spec_version", "domain", "labeling_policy", "precedence", "intents"]
POLICY_KEYS = [
    "cardinality", "default_in_scope_intent", "out_of_scope_intent",
    "operation_intents", "topic_intents",
]
INTENT_KEYS = [
    "name", "kind", "description", "include_when", "exclude_when",
    "typical_metrics", "examples", "confusable_with",
]
MIN_INTENT_EXAMPLES = 4
GENERIC_EXCLUDE_TARGETS = (
    "the matching topic intent",
    "the applicable topic intent",
    "the applicable intent",
    "label the supported analytic request",
)

ENTITY_TOP_KEYS = [
    "spec_version", "domain", "serialization", "global_rules",
    "normalization_strategies", "entity_groups", "entities",
]
SERIALIZATION_EXPECTED = {
    "value_container": "array",
    "item_type": "string",
    "all_keys_required": True,
    "empty_value": [],
    "allow_json_null": False,
    "allow_empty_string": False,
    "unique_items": True,
    "item_order": "first_mention",
}
GLOBAL_RULE_KEYS = [
    "grounding", "nullability", "normalization", "aliases", "casing",
    "units", "dates", "percentages", "multi_value", "exclusions",
]
STRATEGY_KIND = {
    "preserve": "preserve",
    "controlled_vocabulary": "controlled",
    "pack_size_format": "format",
    "discount_format": "format",
    "date_expression": "format",
}
COMMON_ENTITY_KEYS = [
    "type", "multi_value", "nullable", "normalization", "description",
    "extract_when", "do_not_extract_when", "examples",
]
KIND_EXTRA_KEYS = {
    "preserve": ["reference_values"],
    "controlled": ["ambiguous_terms", "allowed_values"],
    "format": ["format"],
}
FORMAT_KEYS = ["pattern", "canonical_rules", "examples", "counter_examples", "normalization_examples"]
# Catalog attributes of open-class reference values: (required, optional).
REFERENCE_ATTRS = {
    "brand": ({"manufacturer", "category"}, set()),
    "manufacturer": (set(), set()),
    "product": ({"brand", "sub_category"}, set()),
    "retailer": ({"channel"}, set()),
    "geography": ({"level"}, {"parent"}),
}
GEOGRAPHY_PARENT_LEVEL = {"country": None, "region": "country", "city": "region"}

SUPPORTED_SCHEMA_KEYWORDS = {
    "$schema", "$id", "$ref", "$defs", "$comment", "title", "description",
    "type", "enum", "const", "pattern", "required", "properties",
    "additionalProperties", "items", "uniqueItems", "minLength", "maxLength",
    "minItems", "maxItems", "minimum", "maximum", "allOf", "if", "then", "else",
}
JSON_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
MODEL_FIELD_RE = re.compile(
    r"prob|confidence|score|logit|reason|rationale|explanation|token|latency"
    r"|cost|model|provider|deployment|raw",
    re.IGNORECASE,
)

REQUIRED_GUIDE_SECTIONS = [
    "Intent rules", "Unsupported questions", "Ambiguity and partial information",
    "Entity rules", "Null versus empty", "Multi-turn conversations",
    "Worked examples", "Annotation checklist", "Adjudication and change control",
]
GUIDE_EXAMPLE_H3 = ["Single-turn examples", "Multi-turn examples"]
EXAMPLE_HEADING_RE = re.compile(r"^#### (GUIDE-\d{3}): \S.*$")
SINGLE_LINE_RES = [
    ("Question", re.compile(r"^- \*\*Question:\*\* (\S.*)$")),
    ("Intent", re.compile(r"^- Intent: (\S+)$")),
    ("Entities", re.compile(r"^- Entities: (\S.*)$")),
    ("Rationale", re.compile(r"^- Rationale: (\S.*)$")),
]
TURN_RE = re.compile(r"^- \*\*Turn (\d+) \(user\):\*\* (\S.*)$")
ASSISTANT_RE = re.compile(r"^- \*\*Assistant:\*\* (\S.*)$")
TURN_FIELD_RE = re.compile(r"^  - (Intent|Entities|Rationale): (\S.*)$")
PATH_RE = re.compile(r"[\w.\-]+(?:/[\w.\-]+)+")
GUIDE_SEMVER_RE = re.compile(r"(?<![\d.])\d+\.\d+\.\d+(?!\d)")
MT_DEF_RE = re.compile(r"^- \*\*MT-(\d+) \(", re.MULTILINE)
MT_REF_RE = re.compile(r"\bMT-(\d+)\b")

# Metrics that are asked for with a counting question rather than by name.
COUNT_CUES = {
    ("metric", "sku_count"): re.compile(r"\bhow\s+many\b[^?.!]*?\b(?:SKUs?|items?)\b", re.IGNORECASE),
    ("metric", "store_count"): re.compile(r"\bhow\s+many\b[^?.!]*?\bstores?\b", re.IGNORECASE),
}

# Real company, brand, retailer, data-provider and program names that must not
# appear in the benchmark. Split literals keep this file free of the terms.
DENYLIST_TERMS = [
    "coca" "-cola", "coca" " cola", "co" "ke", "pep" "si", "pep" "sico",
    "tc" "cc", "nes" "tle", "nestl\u00e9", "uni" "lever", "proc" "ter",
    "p" "&g", "kr" "aft", "he" "inz", "mon" "delez", "dan" "one",
    "kel" "logg", "kel" "loggs", "general" " mills", "ma" "rs",
    "red" " bull", "mon" "ster", "fr" "ito", "frito" "-lay", "dor" "itos",
    "la" "ys", "lay" "'s", "spr" "ite", "fan" "ta", "gator" "ade",
    "das" "ani", "aqua" "fina", "wal" "mart", "kro" "ger", "cost" "co",
    "tes" "co", "carre" "four", "al" "di", "li" "dl", "ama" "zon",
    "niel" "sen", "circ" "ana", "i" "ri", "tetra" " pak", "r" "gm", "rg" "mx",
]
SCAN_SUFFIXES = {".yaml", ".yml", ".json", ".jsonl", ".md", ".txt", ".py", ".csv"}
SKIP_DIRS = {".git", "venv", ".venv", "env", "__pycache__", "site-packages", "node_modules"}

BANNED_MODULES = [
    "openai", "anthropic", "azure", "google.generativeai", "google.genai",
    "vertexai", "llama_index", "semantic_kernel", "transformers",
    "huggingface_hub", "cohere", "mistralai", "ollama", "litellm", "boto3",
    "botocore", "requests", "httpx", "aiohttp", "urllib3", "urllib.request",
    "http.client", "socket",
]
BANNED_MODULE_STEMS = ["langchain", "llama_index"]
# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _is_text(value):
    return isinstance(value, str) and value.strip() != ""


def _is_text_list(value, min_items=1):
    return isinstance(value, list) and len(value) >= min_items and all(_is_text(v) for v in value)


def _is_semver(value):
    return isinstance(value, str) and bool(SEMVER_RE.match(value))


def _mentions(text, word):
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) is not None


def _check_exact_keys(obj, expected, where, errors):
    if not isinstance(obj, dict):
        errors.append(f"{where}: must be a mapping")
        return
    missing = [k for k in expected if k not in obj]
    extra = [k for k in obj if k not in expected]
    if missing:
        errors.append(f"{where}: missing keys {missing}")
    if extra:
        errors.append(f"{where}: unexpected keys {extra}")


def _iter_files(root, suffixes):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.suffix.lower() in suffixes:
                yield path


def _rel(root, path):
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Strict loaders
# ---------------------------------------------------------------------------

if yaml is not None:

    class UniqueKeyLoader(yaml.SafeLoader):
        """SafeLoader that rejects duplicate and unhashable mapping keys."""

    def _construct_unique_mapping(loader, node, deep=False):
        loader.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            try:
                hash(key)
            except TypeError:
                raise yaml.constructor.ConstructorError(
                    None, None, f"unhashable mapping key {key!r}", key_node.start_mark)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate mapping key {key!r}", key_node.start_mark)
            mapping[key] = loader.construct_object(value_node, deep=True)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _json_object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _json_reject_constant(name):
    raise ValueError(f"non-standard JSON constant {name}")


def _load_json(text):
    return json.loads(text, object_pairs_hook=_json_object_no_duplicates,
                      parse_constant=_json_reject_constant)


# ---------------------------------------------------------------------------
# Minimal JSON Schema validator (keywords used by the canonical schema only)
# ---------------------------------------------------------------------------

def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_type(value, type_name):
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    if type_name == "number":
        return _is_number(value)
    if type_name == "integer":
        return _is_number(value) and (isinstance(value, int) or float(value).is_integer())
    return False


def _json_type(value):
    for type_name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is_type(value, type_name):
            return type_name
    return type(value).__name__


def _json_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) and _is_number(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _ecma_end_anchor(pattern):
    """In ECMA-262 a final '$' matches only at the end; Python's also matches before a final newline."""
    if pattern.endswith("$"):
        body = pattern[:-1]
        backslashes = len(body) - len(body.rstrip("\\"))
        if backslashes % 2 == 0:
            return body + r"\Z"
    return pattern


def resolve_pointer(root, ref):
    if not isinstance(ref, str) or not ref.startswith("#"):
        raise ValueError(f"only local references are supported: {ref!r}")
    fragment = ref[1:]
    if fragment == "":
        return root
    if not fragment.startswith("/"):
        raise ValueError(f"unsupported reference {ref!r}")
    node = root
    for part in fragment[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(f"unresolvable reference {ref!r}")
    return node


class MiniValidator:
    """Minimal Draft 2020-12 validator for the keywords allowed by the schema lint."""

    def __init__(self, schema):
        self.schema = schema
        self._patterns = {}

    def errors(self, instance):
        out = []
        self._validate(instance, self.schema, "$", out)
        return out

    def is_valid(self, instance):
        return not self.errors(instance)

    def _pattern(self, pattern):
        compiled = self._patterns.get(pattern)
        if compiled is None:
            compiled = re.compile(_ecma_end_anchor(pattern))
            self._patterns[pattern] = compiled
        return compiled

    def _validate(self, inst, schema, path, out):
        if schema is True:
            return
        if schema is False:
            out.append(f"{path}: not allowed by the schema")
            return
        if "$ref" in schema:
            self._validate(inst, resolve_pointer(self.schema, schema["$ref"]), path, out)
        if "type" in schema:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_is_type(inst, t) for t in types):
                out.append(f"{path}: expected {'/'.join(types)}, got {_json_type(inst)}")
                return
        if "enum" in schema and not any(_json_equal(inst, e) for e in schema["enum"]):
            out.append(f"{path}: {inst!r} is not an allowed value")
        if "const" in schema and not _json_equal(inst, schema["const"]):
            out.append(f"{path}: {inst!r} does not equal {schema['const']!r}")
        if isinstance(inst, str):
            if "minLength" in schema and len(inst) < schema["minLength"]:
                out.append(f"{path}: shorter than {schema['minLength']}")
            if "maxLength" in schema and len(inst) > schema["maxLength"]:
                out.append(f"{path}: longer than {schema['maxLength']}")
            if "pattern" in schema and not self._pattern(schema["pattern"]).search(inst):
                out.append(f"{path}: {inst!r} does not match the pattern")
        if _is_number(inst):
            if "minimum" in schema and inst < schema["minimum"]:
                out.append(f"{path}: below minimum {schema['minimum']}")
            if "maximum" in schema and inst > schema["maximum"]:
                out.append(f"{path}: above maximum {schema['maximum']}")
        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                out.append(f"{path}: fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                out.append(f"{path}: more than {schema['maxItems']} items")
            if schema.get("uniqueItems") is True:
                for i in range(1, len(inst)):
                    if any(_json_equal(inst[i], inst[j]) for j in range(i)):
                        out.append(f"{path}: duplicate item {inst[i]!r}")
            if "items" in schema:
                for i, item in enumerate(inst):
                    self._validate(item, schema["items"], f"{path}[{i}]", out)
        if isinstance(inst, dict):
            for key in schema.get("required", []):
                if key not in inst:
                    out.append(f"{path}: missing required property {key!r}")
            properties = schema.get("properties", {})
            for key, value in inst.items():
                if key in properties:
                    self._validate(value, properties[key], f"{path}.{key}", out)
                elif "additionalProperties" in schema:
                    self._validate(value, schema["additionalProperties"], f"{path}.{key}", out)
        for sub in schema.get("allOf", []):
            self._validate(inst, sub, path, out)
        if "if" in schema:
            probe = []
            self._validate(inst, schema["if"], path, probe)
            branch = "else" if probe else "then"
            if branch in schema:
                self._validate(inst, schema[branch], path, out)


def _instance_errors(ctx, instance):
    """Validate with the built-in validator and cross-check with jsonschema when installed."""
    errors = ctx.validator.errors(instance)
    if ctx.js_validator is not None:
        try:
            js_valid = ctx.js_validator.is_valid(instance)
        except Exception as exc:  # noqa: BLE001 - report library failures as findings
            return errors + [f"jsonschema raised {type(exc).__name__}: {exc}"]
        if js_valid != (not errors):
            errors = errors + [
                f"jsonschema disagrees with the built-in validator (jsonschema valid={js_valid})"]
    return errors


# ---------------------------------------------------------------------------
# Context and sections
# ---------------------------------------------------------------------------

class Context:
    def __init__(self, root):
        self.root = root
        self.intents = None
        self.entities = None
        self.schema = None
        self.guide = None
        self.policy = {}
        self.intent_names = []
        self.intent_by_name = {}
        self.entity_order = []
        self.catalog = {}
        self.validator = None
        self.js_validator = None
        self.details = {}
        self._form_cache = {}


def check_load(ctx):
    errors = []
    for attr, rel, kind in ARTIFACTS:
        path = ctx.root / rel
        if not path.is_file():
            errors.append(f"{rel}: file not found")
            continue
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            errors.append(f"{rel}: must be UTF-8 without a byte order mark")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"{rel}: not valid UTF-8 ({exc})")
            continue
        try:
            if kind == "yaml":
                data = yaml.load(text, Loader=UniqueKeyLoader)
            elif kind == "json":
                data = _load_json(text)
            else:
                # Normalize line endings for text checks that match whole lines.
                data = text
                if kind == "text":
                    data = data.replace("\r\n", "\n").replace("\r", "\n")
        except (yaml.YAMLError, ValueError) as exc:
            errors.append(f"{rel}: parse error: {exc}")
            continue
        if kind != "text" and not isinstance(data, dict):
            errors.append(f"{rel}: top level must be a mapping")
            continue
        setattr(ctx, attr, data)
    return errors


def _check_exclude_line(name, line, names):
    errors = []
    if "->" not in line:
        return [f"intent {name}: exclude_when entry has no '-> target': {line!r}"]
    for segment in line.split("->")[1:]:
        target = re.split(r"[.;]", segment, maxsplit=1)[0].strip()
        for token in SNAKE_TOKEN_RE.findall(target):
            if token not in names and token != "default_in_scope_intent":
                errors.append(f"intent {name}: exclude_when target {target!r} names unknown intent {token!r}")
        mentioned = [n for n in names if _mentions(target, n)]
        lowered = target.lower()
        if not mentioned and not any(g in lowered for g in GENERIC_EXCLUDE_TARGETS):
            errors.append(f"intent {name}: exclude_when target {target!r} does not name an intent")
        if name in mentioned:
            errors.append(f"intent {name}: exclude_when target {target!r} points to the intent itself")
    return errors


def check_intents(ctx):
    errors = []
    doc = ctx.intents
    _check_exact_keys(doc, INTENT_TOP_KEYS, INTENTS_REL, errors)
    if not _is_semver(doc.get("spec_version")):
        errors.append("spec_version must be a quoted semantic version string such as '0.1.0'")
    if not _is_text(doc.get("domain")):
        errors.append("domain must be a non-empty string")

    policy = doc.get("labeling_policy")
    if not isinstance(policy, dict):
        errors.append("labeling_policy must be a mapping")
        return errors
    ctx.policy = policy
    _check_exact_keys(policy, POLICY_KEYS, "labeling_policy", errors)
    if policy.get("cardinality") != "exactly_one_primary_intent":
        errors.append("labeling_policy.cardinality must be 'exactly_one_primary_intent'")

    intents = doc.get("intents")
    if not isinstance(intents, list) or not intents:
        errors.append("intents must be a non-empty list")
        return errors
    names = []
    by_name = {}
    for i, intent in enumerate(intents):
        if not isinstance(intent, dict):
            errors.append(f"intents[{i}]: must be a mapping")
            continue
        name = intent.get("name")
        if not isinstance(name, str) or not SNAKE_RE.match(name):
            errors.append(f"intents[{i}]: name must be lowercase snake_case, found {name!r}")
            continue
        if name in by_name:
            errors.append(f"intents[{i}]: duplicate intent name {name!r}")
            continue
        names.append(name)
        by_name[name] = intent
    ctx.intent_names = names
    ctx.intent_by_name = by_name
    for required in REQUIRED_INTENTS:
        if required not in by_name:
            errors.append(f"required MVP intent {required!r} is missing")

    operation = policy.get("operation_intents")
    topic = policy.get("topic_intents")
    out_of_scope = policy.get("out_of_scope_intent")
    default = policy.get("default_in_scope_intent")
    for key, value in (("operation_intents", operation), ("topic_intents", topic)):
        if not (isinstance(value, list) and value and all(isinstance(v, str) for v in value)):
            errors.append(f"labeling_policy.{key} must be a non-empty list of intent names")
    if not (isinstance(operation, list) and isinstance(topic, list)):
        return errors
    kinds = {}
    for n in operation:
        kinds.setdefault(n, []).append("operation")
    for n in topic:
        kinds.setdefault(n, []).append("topic")
    if isinstance(out_of_scope, str):
        kinds.setdefault(out_of_scope, []).append("out_of_scope")
    for n, assigned in kinds.items():
        if n not in by_name:
            errors.append(f"labeling_policy references unknown intent {n!r}")
        if len(assigned) > 1:
            errors.append(f"labeling_policy lists intent {n!r} in several groups {assigned}")
    for n in names:
        if n not in kinds:
            errors.append(f"intent {n!r} is not classified in labeling_policy")
    if default not in topic:
        errors.append("labeling_policy.default_in_scope_intent must be one of the topic_intents")
    if out_of_scope not in by_name:
        errors.append("labeling_policy.out_of_scope_intent must be a defined intent")

    precedence = doc.get("precedence")
    expected_precedence = [out_of_scope] + list(operation) + list(topic)
    if precedence != expected_precedence:
        errors.append(
            f"precedence must be [out_of_scope_intent] + operation_intents + topic_intents = "
            f"{expected_precedence}, found {precedence}")
    if isinstance(precedence, list):
        if sorted(map(str, precedence)) != sorted(names):
            errors.append("precedence must list every intent exactly once")
        if precedence and precedence[-1] != default:
            errors.append("the last precedence entry must be default_in_scope_intent")

    seen_examples = {}
    confusable_map = {}
    for name in names:
        intent = by_name[name]
        where = f"intent {name}"
        _check_exact_keys(intent, INTENT_KEYS, where, errors)
        expected_kind = (kinds.get(name) or [None])[0]
        if intent.get("kind") != expected_kind:
            errors.append(f"{where}: kind must be {expected_kind!r} to match labeling_policy")
        if not _is_text(intent.get("description")):
            errors.append(f"{where}: description must be a non-empty string")
        for key in ("include_when", "exclude_when"):
            if not _is_text_list(intent.get(key)):
                errors.append(f"{where}: {key} must be a non-empty list of strings")

        metrics = intent.get("typical_metrics")
        if not (isinstance(metrics, list) and all(isinstance(m, str) for m in metrics)):
            errors.append(f"{where}: typical_metrics must be a list of metric values")
        elif len(set(metrics)) != len(metrics):
            errors.append(f"{where}: typical_metrics contains duplicates")
        elif expected_kind == "topic" and not metrics:
            errors.append(f"{where}: topic intents must list typical_metrics")
        elif expected_kind != "topic" and metrics:
            errors.append(f"{where}: only topic intents may list typical_metrics")

        examples = intent.get("examples")
        if not _is_text_list(examples, MIN_INTENT_EXAMPLES):
            errors.append(f"{where}: examples must list at least {MIN_INTENT_EXAMPLES} questions")
        else:
            for example in examples:
                if not CLEAN_TEXT_RE.match(example):
                    errors.append(f"{where}: example has irregular whitespace: {example!r}")
                if example in seen_examples:
                    errors.append(f"{where}: example also listed under {seen_examples[example]}: {example!r}")
                else:
                    seen_examples[example] = name

        confusables = intent.get("confusable_with")
        targets = []
        if not isinstance(confusables, list) or not confusables:
            errors.append(f"{where}: confusable_with must be a non-empty list")
        else:
            for j, entry in enumerate(confusables):
                if not isinstance(entry, dict) or set(entry) != {"intent", "rule"}:
                    errors.append(f"{where}: confusable_with[{j}] must have exactly the keys intent and rule")
                    continue
                target, rule = entry["intent"], entry["rule"]
                if target not in by_name:
                    errors.append(f"{where}: confusable_with[{j}] names unknown intent {target!r}")
                    continue
                if target == name:
                    errors.append(f"{where}: confusable_with must not list the intent itself")
                if target in targets:
                    errors.append(f"{where}: confusable_with lists {target!r} twice")
                targets.append(target)
                if not _is_text(rule):
                    errors.append(f"{where}: confusable_with rule for {target!r} must be a non-empty string")
                elif not _mentions(rule, target):
                    errors.append(f"{where}: confusable_with rule for {target!r} does not mention {target}")
        confusable_map[name] = targets

        exclude_when = intent.get("exclude_when")
        if _is_text_list(exclude_when):
            for line in exclude_when:
                errors.extend(_check_exclude_line(name, line, names))

    for name, targets in confusable_map.items():
        for target in targets:
            if name not in confusable_map.get(target, []):
                errors.append(f"confusable_with is not symmetric: {name} lists {target}, but {target} does not list {name}")

    ctx.details["intents"] = f"{len(names)} intents"
    return errors


def _load_value_records(entity, records, list_key, value_re, required, optional, entry, errors):
    if not isinstance(records, list) or not records:
        errors.append(f"entity {entity}: {list_key} must be a non-empty list")
        return
    base = {"value", "aliases"}
    for i, record in enumerate(records):
        where = f"entity {entity}: {list_key}[{i}]"
        if not isinstance(record, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        missing = sorted((base | required) - set(record))
        extra = sorted(set(record) - (base | required | optional))
        if missing:
            errors.append(f"{where}: missing keys {missing}")
        if extra:
            errors.append(f"{where}: unexpected keys {extra}")
        value = record.get("value")
        if not (isinstance(value, str) and value_re.match(value) and len(value) <= MAX_TEXT_LENGTH):
            errors.append(f"{where}: invalid value {value!r}")
            continue
        if value in entry["records"]:
            errors.append(f"{where}: duplicate value {value!r}")
            continue
        aliases = record.get("aliases")
        if not isinstance(aliases, list):
            errors.append(f"{where}: aliases must be a list (use [] for none)")
            aliases = []
        clean = []
        for alias in aliases:
            if isinstance(alias, str) and CLEAN_TEXT_RE.match(alias):
                clean.append(alias)
            else:
                errors.append(f"{where}: invalid alias {alias!r}")
        entry["values"].append(value)
        entry["aliases"][value] = clean
        entry["records"][value] = record


def _load_format(entity, spec, entry, errors):
    where = f"entity {entity}: format"
    fmt = spec.get("format")
    if not isinstance(fmt, dict):
        errors.append(f"{where} must be a mapping")
        return
    _check_exact_keys(fmt, FORMAT_KEYS, where, errors)
    pattern = fmt.get("pattern")
    if not (isinstance(pattern, str) and pattern.startswith("^") and pattern.endswith("$")):
        errors.append(f"{where}.pattern must be a string anchored with ^ and $")
        return
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        errors.append(f"{where}.pattern does not compile: {exc}")
        return
    entry["pattern"] = compiled
    entry["pattern_src"] = pattern
    if not _is_text_list(fmt.get("canonical_rules")):
        errors.append(f"{where}.canonical_rules must be a non-empty list of strings")
    examples = fmt.get("examples")
    if not _is_text_list(examples):
        errors.append(f"{where}.examples must be a non-empty list of strings")
    else:
        for example in examples:
            if not compiled.fullmatch(example):
                errors.append(f"{where}: example {example!r} does not match the pattern")
    counter = fmt.get("counter_examples")
    if not _is_text_list(counter):
        errors.append(f"{where}.counter_examples must be a non-empty list of strings")
    else:
        for example in counter:
            if compiled.fullmatch(example):
                errors.append(f"{where}: counter-example {example!r} matches the pattern")
        entry["counter_examples"] = list(counter)
    normalization = fmt.get("normalization_examples")
    if not isinstance(normalization, list) or not normalization:
        errors.append(f"{where}.normalization_examples must be a non-empty list")
    else:
        for i, item in enumerate(normalization):
            if not (isinstance(item, dict) and set(item) == {"input", "output"}
                    and _is_text(item.get("input")) and isinstance(item.get("output"), str)):
                errors.append(f"{where}.normalization_examples[{i}] must have string input and output")
            elif not compiled.fullmatch(item["output"]):
                errors.append(f"{where}: normalization output {item['output']!r} does not match the pattern")


def _check_catalog_references(catalog, errors):
    def values(entity):
        return catalog.get(entity, {}).get("records", {})

    for value, record in values("brand").items():
        if record.get("manufacturer") not in values("manufacturer"):
            errors.append(f"brand {value!r}: manufacturer {record.get('manufacturer')!r} is not a manufacturer value")
        if record.get("category") not in values("category"):
            errors.append(f"brand {value!r}: category {record.get('category')!r} is not a category value")
    for value, record in values("product").items():
        brand = record.get("brand")
        if brand not in values("brand"):
            errors.append(f"product {value!r}: brand {brand!r} is not a brand value")
            continue
        if not value.startswith(brand + " "):
            errors.append(f"product {value!r}: value must start with its brand followed by a space")
        sub_category = record.get("sub_category")
        if sub_category not in values("sub_category"):
            errors.append(f"product {value!r}: sub_category {sub_category!r} is not a sub_category value")
            continue
        parent = values("sub_category")[sub_category].get("parent")
        brand_category = values("brand")[brand].get("category")
        if parent != brand_category:
            errors.append(
                f"product {value!r}: sub_category {sub_category} belongs to {parent}, "
                f"but brand {brand} belongs to {brand_category}")
    for value, record in values("retailer").items():
        if record.get("channel") not in values("channel"):
            errors.append(f"retailer {value!r}: channel {record.get('channel')!r} is not a channel value")
    geographies = values("geography")
    for value, record in geographies.items():
        level = record.get("level")
        if level not in GEOGRAPHY_PARENT_LEVEL:
            errors.append(f"geography {value!r}: level must be one of {sorted(GEOGRAPHY_PARENT_LEVEL)}")
            continue
        parent_level = GEOGRAPHY_PARENT_LEVEL[level]
        parent = record.get("parent")
        if parent_level is None:
            if "parent" in record:
                errors.append(f"geography {value!r}: a {level} must not have a parent")
        elif parent not in geographies:
            errors.append(f"geography {value!r}: parent {parent!r} is not a geography value")
        elif geographies[parent].get("level") != parent_level:
            errors.append(f"geography {value!r}: parent of a {level} must be a {parent_level}")
    categories = values("category")
    children = set()
    for value, record in values("sub_category").items():
        parent = record.get("parent")
        if parent not in categories:
            errors.append(f"sub_category {value!r}: parent {parent!r} is not a category value")
        else:
            children.add(parent)
    if categories and values("sub_category"):
        for category in categories:
            if category not in children:
                errors.append(f"category {category!r} has no sub_category")


def check_entities(ctx):
    errors = []
    doc = ctx.entities
    _check_exact_keys(doc, ENTITY_TOP_KEYS, ENTITIES_REL, errors)
    if not _is_semver(doc.get("spec_version")):
        errors.append("spec_version must be a quoted semantic version string such as '0.1.0'")
    if not _is_text(doc.get("domain")):
        errors.append("domain must be a non-empty string")
    serialization = doc.get("serialization")
    if serialization != SERIALIZATION_EXPECTED:
        errors.append(f"serialization must be {SERIALIZATION_EXPECTED}, found {serialization}")

    rules = doc.get("global_rules")
    _check_exact_keys(rules, GLOBAL_RULE_KEYS, "global_rules", errors)
    if isinstance(rules, dict):
        for key in GLOBAL_RULE_KEYS:
            if key in rules and not _is_text_list(rules[key]):
                errors.append(f"global_rules.{key} must be a non-empty list of strings")

    strategies = doc.get("normalization_strategies")
    _check_exact_keys(strategies, list(STRATEGY_KIND), "normalization_strategies", errors)
    if not isinstance(strategies, dict):
        strategies = {}
    for key, value in strategies.items():
        if not _is_text(value):
            errors.append(f"normalization_strategies.{key} must be a non-empty description")

    entities = doc.get("entities")
    if not isinstance(entities, dict) or not entities:
        errors.append("entities must be a non-empty mapping")
        return errors
    order = list(entities)
    ctx.entity_order = order
    for required in REQUIRED_ENTITIES:
        if required not in entities:
            errors.append(f"required MVP entity {required!r} is missing")

    catalog = {}
    for name in order:
        where = f"entity {name}"
        spec = entities[name]
        if not isinstance(name, str) or not SNAKE_RE.match(name):
            errors.append(f"{where}: name must be lowercase snake_case")
            continue
        if not isinstance(spec, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        strategy = spec.get("normalization")
        kind = STRATEGY_KIND.get(strategy)
        if kind is None or strategy not in strategies:
            errors.append(f"{where}: unknown normalization strategy {strategy!r}")
            continue
        _check_exact_keys(spec, COMMON_ENTITY_KEYS + KIND_EXTRA_KEYS[kind], where, errors)
        if spec.get("type") != "string":
            errors.append(f"{where}: type must be 'string'")
        if spec.get("multi_value") is not True:
            errors.append(f"{where}: multi_value must be true")
        if spec.get("nullable") is not True:
            errors.append(f"{where}: nullable must be true")
        if not _is_text(spec.get("description")):
            errors.append(f"{where}: description must be a non-empty string")
        for key in ("extract_when", "do_not_extract_when"):
            if not _is_text_list(spec.get(key)):
                errors.append(f"{where}: {key} must be a non-empty list of strings")

        entry = {"strategy": strategy, "kind": kind, "values": [], "aliases": {}, "records": {},
                 "ambiguous": [], "pattern": None, "pattern_src": None, "examples": [],
                 "counter_examples": []}
        if kind == "preserve":
            required, optional = REFERENCE_ATTRS.get(name, (set(), set()))
            _load_value_records(name, spec.get("reference_values"), "reference_values",
                                CLEAN_TEXT_RE, required, optional, entry, errors)
        elif kind == "controlled":
            required = {"description"} | ({"parent"} if name == "sub_category" else set())
            _load_value_records(name, spec.get("allowed_values"), "allowed_values",
                                SNAKE_RE, required, set(), entry, errors)
            for value, record in entry["records"].items():
                if not _is_text(record.get("description")):
                    errors.append(f"{where}: allowed value {value!r} needs a description")
            ambiguous = spec.get("ambiguous_terms")
            if not (isinstance(ambiguous, list)
                    and all(isinstance(t, str) and CLEAN_TEXT_RE.match(t) for t in ambiguous)):
                errors.append(f"{where}: ambiguous_terms must be a list of strings (use [] for none)")
            elif len(set(t.lower() for t in ambiguous)) != len(ambiguous):
                errors.append(f"{where}: ambiguous_terms contains duplicates")
            else:
                entry["ambiguous"] = list(ambiguous)
        else:
            _load_format(name, spec, entry, errors)

        examples = spec.get("examples")
        if not _is_text_list(examples, 2):
            errors.append(f"{where}: examples must list at least 2 values")
        else:
            entry["examples"] = list(examples)
            if len(set(examples)) != len(examples):
                errors.append(f"{where}: examples contains duplicates")
            for example in examples:
                if kind == "format":
                    if entry["pattern"] is not None and not entry["pattern"].fullmatch(example):
                        errors.append(f"{where}: example {example!r} does not match the format")
                elif example not in entry["records"]:
                    errors.append(f"{where}: example {example!r} is not a canonical value")
        catalog[name] = entry
    ctx.catalog = catalog
    _check_catalog_references(catalog, errors)

    groups = doc.get("entity_groups")
    if not isinstance(groups, dict) or not groups:
        errors.append("entity_groups must be a non-empty mapping")
    else:
        membership = {}
        for group, members in groups.items():
            if not (isinstance(group, str) and SNAKE_RE.match(group)):
                errors.append(f"entity_groups: invalid group name {group!r}")
            if not (isinstance(members, list) and members and all(isinstance(m, str) for m in members)):
                errors.append(f"entity_groups.{group} must be a non-empty list of entity names")
                continue
            for member in members:
                if member not in entities:
                    errors.append(f"entity_groups.{group}: unknown entity {member!r}")
                elif member in membership:
                    errors.append(f"entity {member!r} is in groups {membership[member]} and {group}")
                else:
                    membership[member] = group
        for name in order:
            if name not in membership:
                errors.append(f"entity {name!r} is not in any entity_groups group")

    ctx.details["entities"] = f"{len(order)} entities"
    return errors


def alias_key(text):
    key = " ".join(text.lower().replace("_", " ").split())
    if len(key) > 3 and key.endswith("s"):
        key = key[:-1]
    return key


def check_aliases(ctx):
    errors = []
    owners = {}
    forms_checked = 0
    for entity, entry in ctx.catalog.items():
        for value in entry["values"]:
            seen = {}
            for form in [value] + entry["aliases"].get(value, []):
                key = alias_key(form)
                forms_checked += 1
                if key in seen:
                    errors.append(f"{entity} {value!r}: {form!r} duplicates {seen[key]!r}")
                    continue
                seen[key] = form
                owners.setdefault(key, []).append((entity, value, form))
    for key, owned in owners.items():
        if len({(e, v) for e, v, _ in owned}) > 1:
            listing = ", ".join(f"{e}:{v} ({f!r})" for e, v, f in owned)
            errors.append(f"alias collision on {key!r}: {listing}")
    for entity, entry in ctx.catalog.items():
        for term in entry["ambiguous"]:
            key = alias_key(term)
            if key in owners:
                listing = ", ".join(f"{e}:{v}" for e, v, _ in owners[key])
                errors.append(f"{entity} ambiguous term {term!r} collides with {listing}")
    ctx.details["aliases"] = f"{forms_checked} values and aliases"
    return errors


def _lint_schema(node, path, root, errors, names):
    if isinstance(node, bool):
        return
    if not isinstance(node, dict):
        errors.append(f"{path}: a subschema must be an object or a boolean")
        return
    for key in node:
        if key not in SUPPORTED_SCHEMA_KEYWORDS:
            errors.append(f"{path}: unsupported keyword {key!r}")
    if "type" in node:
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not types or any(t not in JSON_TYPES for t in types):
            errors.append(f"{path}: invalid type {node['type']!r}")
    if "enum" in node and not (isinstance(node["enum"], list) and node["enum"]):
        errors.append(f"{path}: enum must be a non-empty array")
    if "pattern" in node:
        pattern = node["pattern"]
        if not isinstance(pattern, str):
            errors.append(f"{path}: pattern must be a string")
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(f"{path}: pattern does not compile: {exc}")
    if "$ref" in node:
        try:
            resolve_pointer(root, node["$ref"])
        except (KeyError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
    if "required" in node:
        required = node["required"]
        if not (isinstance(required, list) and all(isinstance(r, str) for r in required)
                and len(set(required)) == len(required)):
            errors.append(f"{path}: required must be an array of unique strings")
        else:
            names.extend(required)
    for key in ("properties", "$defs"):
        if key in node:
            mapping = node[key]
            if not isinstance(mapping, dict):
                errors.append(f"{path}/{key}: must be an object")
                continue
            for child, sub in mapping.items():
                names.append(child)
                _lint_schema(sub, f"{path}/{key}/{child}", root, errors, names)
    for key in ("items", "additionalProperties", "if", "then", "else"):
        if key in node:
            _lint_schema(node[key], f"{path}/{key}", root, errors, names)
    if "allOf" in node:
        subs = node["allOf"]
        if not isinstance(subs, list) or not subs:
            errors.append(f"{path}/allOf: must be a non-empty array")
        else:
            for i, sub in enumerate(subs):
                _lint_schema(sub, f"{path}/allOf/{i}", root, errors, names)


def check_schema_meta(ctx):
    errors = []
    schema = ctx.schema
    if schema.get("$schema") != DRAFT_2020_12:
        errors.append(f"$schema must be {DRAFT_2020_12}")
    if not _is_text(schema.get("$id")):
        errors.append("$id must be a non-empty string")
    if jsonschema is not None:
        validator_cls = getattr(jsonschema, "Draft202012Validator", None)
        if validator_cls is None:
            errors.append("installed jsonschema has no Draft202012Validator; upgrade to >= 4.18")
        else:
            try:
                validator_cls.check_schema(schema)
                ctx.js_validator = validator_cls(schema)
            except jsonschema.exceptions.SchemaError as exc:
                errors.append(f"meta-schema validation failed: {exc.message}")
    names = []
    _lint_schema(schema, "#", schema, errors, names)
    flagged = sorted({n for n in names if MODEL_FIELD_RE.search(n)})
    if flagged:
        errors.append(f"model- or provider-specific property names are not allowed: {flagged}")
    ctx.validator = MiniValidator(schema)
    ctx.details["schema"] = ("meta-schema + lint" if ctx.js_validator is not None
                             else "lint only; jsonschema not installed")
    return errors


def _expected_items(entry):
    if entry["kind"] == "preserve":
        return {"$ref": "#/$defs/text_value"}
    if entry["kind"] == "controlled":
        return {"type": "string", "enum": list(entry["values"])}
    return {"type": "string", "pattern": entry["pattern_src"]}


def _has_out_of_scope_rule(schema, out_of_scope):
    for sub in schema.get("allOf") or []:
        try:
            condition, then = sub["if"], sub["then"]
            if (condition["properties"]["intent"]["const"] == out_of_scope
                    and "intent" in condition.get("required", [])
                    and then["properties"]["entities"]["additionalProperties"]["maxItems"] == 0):
                return True
        except (KeyError, TypeError):
            continue
    return False


def check_cross_file(ctx):
    errors = []
    version = ctx.intents.get("spec_version")
    if ctx.entities.get("spec_version") != version:
        errors.append(f"spec_version differs: intents.yaml {version!r}, entities.yaml {ctx.entities.get('spec_version')!r}")
    if ctx.entities.get("domain") != ctx.intents.get("domain"):
        errors.append("domain differs between intents.yaml and entities.yaml")
    schema = ctx.schema
    schema_id = schema.get("$id")
    if not (isinstance(schema_id, str) and schema_id.endswith(f":{version}")):
        errors.append(f"schema $id must end with ':{version}'")
    description = schema.get("description")
    if not (isinstance(description, str) and f"(spec_version {version})" in description):
        errors.append(f"schema description must mention '(spec_version {version})'")

    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        errors.append("schema root must be a closed object (additionalProperties: false)")
    if schema.get("required") != ["test_case_id", "intent", "entities"]:
        errors.append("schema root must require exactly test_case_id, intent and entities")
    props = schema.get("properties")
    if not isinstance(props, dict):
        errors.append("schema root properties must be an object")
        return errors
    if list(props) != ["test_case_id", "turn_index", "intent", "entities"]:
        errors.append(f"schema root properties must be test_case_id, turn_index, intent, entities; found {list(props)}")
    test_case_id = props.get("test_case_id") or {}
    if test_case_id.get("type") != "string" or not isinstance(test_case_id.get("pattern"), str):
        errors.append("test_case_id must be a string with a pattern")
    turn_index = props.get("turn_index") or {}
    if turn_index.get("type") != "integer" or turn_index.get("minimum") != 1:
        errors.append("turn_index must be an integer with minimum 1")
    intent = props.get("intent") or {}
    if intent.get("type") != "string" or intent.get("enum") != ctx.intent_names:
        errors.append(f"schema intent enum must equal the intents.yaml names in order: {ctx.intent_names}")
    if props.get("entities") != {"$ref": "#/$defs/entities"}:
        errors.append("schema entities property must be {'$ref': '#/$defs/entities'}")

    defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}
    text_value = defs.get("text_value")
    expected_text_value = {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH,
                           "pattern": CLEAN_TEXT_RE.pattern}
    if not isinstance(text_value, dict) or {k: text_value.get(k) for k in expected_text_value} != expected_text_value:
        errors.append(f"$defs.text_value must contain {expected_text_value}")
    entities_def = defs.get("entities")
    if not isinstance(entities_def, dict):
        errors.append("$defs.entities must be an object")
        return errors
    if entities_def.get("type") != "object" or entities_def.get("additionalProperties") is not False:
        errors.append("$defs.entities must be a closed object (additionalProperties: false)")
    if entities_def.get("required") != ctx.entity_order:
        errors.append("$defs.entities.required must list every entity in entities.yaml order")
    entity_props = entities_def.get("properties")
    if not isinstance(entity_props, dict):
        errors.append("$defs.entities.properties must be an object")
        return errors
    if list(entity_props) != ctx.entity_order:
        errors.append(f"$defs.entities.properties must list every entity in entities.yaml order: {ctx.entity_order}")
    yaml_entities = ctx.entities["entities"]
    for name in ctx.entity_order:
        prop = entity_props.get(name)
        entry = ctx.catalog.get(name)
        if not isinstance(prop, dict) or entry is None:
            continue
        expected = {
            "description": " ".join(yaml_entities[name]["description"].split())
            + f" Normalization: {entry['strategy']}.",
            "type": "array",
            "uniqueItems": True,
            "items": _expected_items(entry),
        }
        if prop != expected:
            differing = sorted(k for k in set(prop) | set(expected) if prop.get(k) != expected.get(k))
            errors.append(f"schema entity {name!r} is out of sync with entities.yaml (keys: {differing})")

    out_of_scope = ctx.policy.get("out_of_scope_intent")
    if not _has_out_of_scope_rule(schema, out_of_scope):
        errors.append(f"schema allOf must force every entity to [] when intent is {out_of_scope!r}")

    metric_entry = ctx.catalog.get("metric")
    if metric_entry is None:
        errors.append("entities.yaml defines no metric entity for typical_metrics")
    else:
        covered = set()
        for name in ctx.intent_names:
            for metric in ctx.intent_by_name[name].get("typical_metrics") or []:
                if metric not in metric_entry["records"]:
                    errors.append(f"intent {name}: typical metric {metric!r} is not a metric value")
                covered.add(metric)
        uncovered = [m for m in metric_entry["values"] if m not in covered]
        if uncovered:
            errors.append(f"metric values not listed as typical_metrics of any topic intent: {uncovered}")
    return errors


def _instance(ctx, intent, entities=None, test_case_id="SELFTEST-001", turn_index=None):
    instance = {"test_case_id": test_case_id, "intent": intent,
                "entities": {name: [] for name in ctx.entity_order}}
    if turn_index is not None:
        instance["turn_index"] = turn_index
    instance["entities"].update(entities or {})
    return instance


def check_self_tests(ctx):
    errors = []
    names = ctx.intent_names
    default = ctx.policy.get("default_in_scope_intent")
    out_of_scope = ctx.policy.get("out_of_scope_intent")
    guide_like = _instance(ctx, "promotion_analysis", {
        "brand": ["FreshFizz"], "pack_size": ["500ml"],
        "date_range": ["last quarter"], "metric": ["roi"]}, "GUIDE-001")
    all_examples = _instance(ctx, default, {n: list(e["examples"]) for n, e in ctx.catalog.items()})

    must_pass = [
        ("promotion ROI example (GUIDE-001)", guide_like),
        ("every entity holding its documented examples", all_examples),
        ("out-of-scope intent with every entity empty", _instance(ctx, out_of_scope)),
        ("multi-turn instance with turn_index", _instance(
            ctx, names[0], {"brand": ctx.catalog["brand"]["values"][:2]}, "CPG-MT-001", 3)),
    ]

    def mutate(base, change):
        instance = copy.deepcopy(base)
        change(instance)
        return instance

    must_fail = [
        ("entity value is null", mutate(guide_like, lambda i: i["entities"].update(retailer=None))),
        ("empty-string item", mutate(guide_like, lambda i: i["entities"].update(retailer=[""]))),
        ("null item", mutate(guide_like, lambda i: i["entities"].update(retailer=[None]))),
        ("non-string item", mutate(guide_like, lambda i: i["entities"].update(brand=[1]))),
        ("padded string", mutate(guide_like, lambda i: i["entities"].update(brand=[" FreshFizz"]))),
        ("repeated internal space", mutate(guide_like, lambda i: i["entities"].update(geography=["Coastal  Region"]))),
        ("scalar instead of list", mutate(guide_like, lambda i: i["entities"].update(metric="roi"))),
        ("missing entity key", mutate(guide_like, lambda i: i["entities"].pop("geography"))),
        ("extra entity key", mutate(guide_like, lambda i: i["entities"].update(store_format=[]))),
        ("extra top-level key", mutate(guide_like, lambda i: i.update(confidence=0.9))),
        ("unknown intent", mutate(guide_like, lambda i: i.update(intent="sales_analysis"))),
        ("intent as a list", mutate(guide_like, lambda i: i.update(intent=["promotion_analysis"]))),
        ("missing intent", mutate(guide_like, lambda i: i.pop("intent"))),
        ("missing entities", mutate(guide_like, lambda i: i.pop("entities"))),
        ("missing test_case_id", mutate(guide_like, lambda i: i.pop("test_case_id"))),
        ("duplicate items", mutate(guide_like, lambda i: i["entities"].update(brand=["FreshFizz", "FreshFizz"]))),
        ("out-of-scope intent with an entity", mutate(guide_like, lambda i: i.update(intent=out_of_scope))),
        ("turn_index 0", mutate(guide_like, lambda i: i.update(turn_index=0))),
        ("turn_index true", mutate(guide_like, lambda i: i.update(turn_index=True))),
        ("turn_index 1.5", mutate(guide_like, lambda i: i.update(turn_index=1.5))),
        ("turn_index as a string", mutate(guide_like, lambda i: i.update(turn_index="1"))),
        ("lowercase test_case_id", mutate(guide_like, lambda i: i.update(test_case_id="cpg-001"))),
        ("test_case_id without a hyphen", mutate(guide_like, lambda i: i.update(test_case_id="CPG001"))),
        ("test_case_id with an empty segment", mutate(guide_like, lambda i: i.update(test_case_id="CPG-"))),
        ("non-object instance", []),
    ]
    for name, entry in ctx.catalog.items():
        if entry["kind"] == "controlled":
            alias = next((a for v in entry["values"] for a in entry["aliases"][v]
                          if a not in entry["records"]), None)
            if alias is not None:
                must_fail.append((f"{name} alias {alias!r} instead of the canonical value",
                                  _instance(ctx, default, {name: [alias]})))
        elif entry["kind"] == "format":
            for counter in entry["counter_examples"]:
                must_fail.append((f"{name} counter-example {counter!r}",
                                  _instance(ctx, default, {name: [counter]})))

    for label, instance in must_pass:
        problems = _instance_errors(ctx, instance)
        if problems:
            errors.append(f"must-pass instance rejected ({label}): {problems}")
    for label, instance in must_fail:
        problems = ctx.validator.errors(instance)
        if not problems:
            errors.append(f"must-fail instance accepted ({label})")
        if ctx.js_validator is not None and ctx.js_validator.is_valid(instance):
            errors.append(f"must-fail instance accepted by jsonschema ({label})")
    ctx.details["selftest"] = f"{len(must_pass)} must-pass, {len(must_fail)} must-fail"
    return errors


def _split_fences(lines, errors):
    """Return (fences, prose) where prose lines exclude fenced code blocks."""
    fences = []
    prose = []
    current = None
    for number, line in enumerate(lines, start=1):
        if line.startswith("```"):
            if current is None:
                current = {"lang": line[3:].strip(), "line": number, "body": []}
            else:
                fences.append(current)
                current = None
            continue
        if current is not None:
            current["body"].append(line)
        else:
            prose.append((number, line))
    if current is not None:
        errors.append(f"guide line {current['line']}: unclosed code fence")
    return fences, prose


def _slug(heading):
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def _parse_entities_field(ctx, text, where, errors):
    entities = {name: [] for name in ctx.entity_order}
    if text == "none":
        return entities
    for part in text.split("; "):
        if " = " not in part:
            errors.append(f"{where}: entity assignment {part!r} must use 'name = value'")
            continue
        name, raw_values = part.split(" = ", 1)
        name = name.strip()
        if name not in entities:
            errors.append(f"{where}: unknown entity {name!r}")
            continue
        if entities[name]:
            errors.append(f"{where}: entity {name!r} assigned twice")
            continue
        entities[name] = raw_values.split(", ")
    return entities


def _form_regex(ctx, form):
    compiled = ctx._form_cache.get(form)
    if compiled is None:
        body = r"\s+".join(re.escape(word) for word in form.split())
        compiled = re.compile(r"(?<!\w)" + body + r"(?:e?s)?(?:['\u2019]s)?(?!\w)", re.IGNORECASE)
        ctx._form_cache[form] = compiled
    return compiled


def _first_mention(ctx, entity, value, context):
    entry = ctx.catalog[entity]
    positions = []
    for form in [value.replace("_", " ")] + entry["aliases"].get(value, []):
        match = _form_regex(ctx, form).search(context)
        if match:
            positions.append(match.start())
    cue = COUNT_CUES.get((entity, value))
    if cue is not None:
        match = cue.search(context)
        if match:
            positions.append(match.start())
    return min(positions) if positions else None


def _check_grounding(ctx, instance, context, where, errors):
    for entity in ctx.entity_order:
        entry = ctx.catalog[entity]
        if entry["kind"] == "format":
            continue
        values = instance["entities"].get(entity) or []
        positions = []
        for value in values:
            if value not in entry["records"]:
                errors.append(f"{where}: {entity} value {value!r} is not a registered canonical value")
                continue
            position = _first_mention(ctx, entity, value, context)
            if position is None:
                errors.append(f"{where}: {entity} value {value!r} cannot be traced to the conversation text")
                continue
            positions.append(position)
        if any(later <= earlier for earlier, later in zip(positions, positions[1:])):
            errors.append(f"{where}: {entity} values {values} are not in order of first mention")


def _parse_worked_examples(ctx, prose, errors):
    """Return a list of parsed example turns from the 'Worked examples' section."""
    start = next((i for i, (_, line) in enumerate(prose) if line == "## Worked examples"), None)
    if start is None:
        errors.append("guide: section '## Worked examples' not found")
        return []
    end = next((i for i in range(start + 1, len(prose)) if prose[i][1].startswith("## ")), len(prose))
    section = prose[start + 1:end]
    h3 = [i for i, (_, line) in enumerate(section) if line.startswith("### ")]
    titles = [section[i][1][4:].strip() for i in h3]
    if titles != GUIDE_EXAMPLE_H3:
        errors.append(f"guide: worked examples must have the subsections {GUIDE_EXAMPLE_H3}, found {titles}")
        return []
    parts = {"single": section[h3[0] + 1:h3[1]], "multi": section[h3[1] + 1:]}
    examples = []
    seen_ids = set()
    for part_name, items in parts.items():
        blocks = []
        for number, line in items:
            if line.startswith("#### "):
                blocks.append({"line": number, "heading": line, "body": []})
            elif line.strip():
                if not blocks:
                    errors.append(f"guide line {number}: unexpected text before the first example")
                else:
                    blocks[-1]["body"].append((number, line))
        for block in blocks:
            match = EXAMPLE_HEADING_RE.match(block["heading"])
            if not match:
                errors.append(f"guide line {block['line']}: example heading must be '#### GUIDE-NNN: title'")
                continue
            example_id = match.group(1)
            if example_id in seen_ids:
                errors.append(f"guide line {block['line']}: duplicate example id {example_id}")
                continue
            seen_ids.add(example_id)
            if part_name == "single":
                examples.extend(_parse_single(example_id, block, errors))
            else:
                examples.extend(_parse_multi(example_id, block, errors))
    return examples


def _parse_single(example_id, block, errors):
    body = block["body"]
    if len(body) != len(SINGLE_LINE_RES):
        errors.append(f"guide {example_id}: expected exactly the lines Question, Intent, Entities and Rationale")
        return []
    fields = {}
    for (number, line), (field, pattern) in zip(body, SINGLE_LINE_RES):
        match = pattern.match(line)
        if not match:
            errors.append(f"guide line {number}: expected '- {field}: ...' in {example_id}")
            return []
        fields[field] = match.group(1)
    return [{"id": example_id, "turn_index": None, "intent": fields["Intent"],
             "entities": fields["Entities"], "question": fields["Question"],
             "context": fields["Question"], "line": block["line"]}]


def _parse_multi(example_id, block, errors):
    body = block["body"]
    messages = []
    turns = []
    k = 0
    while k < len(body):
        number, line = body[k]
        turn_match = TURN_RE.match(line)
        assistant_match = ASSISTANT_RE.match(line)
        if turn_match:
            turn_number = int(turn_match.group(1))
            if turn_number != len(turns) + 1:
                errors.append(f"guide line {number}: {example_id} turn {turn_number} is out of sequence")
            messages.append(turn_match.group(2))
            fields = {}
            for expected in ("Intent", "Entities", "Rationale"):
                k += 1
                field_match = TURN_FIELD_RE.match(body[k][1]) if k < len(body) else None
                if not field_match or field_match.group(1) != expected:
                    errors.append(f"guide line {number}: {example_id} turn {turn_number} needs an indented '{expected}:' line")
                    break
                fields[expected] = field_match.group(2)
            else:
                turns.append({"id": example_id, "turn_index": turn_number, "intent": fields["Intent"],
                              "entities": fields["Entities"], "question": turn_match.group(2),
                              "context": "\n".join(messages), "line": number})
                k += 1
                continue
            k += 1
            continue
        if assistant_match:
            if not messages:
                errors.append(f"guide line {number}: {example_id} must start with a user turn")
            messages.append(assistant_match.group(1))
            k += 1
            continue
        errors.append(f"guide line {number}: unexpected line in multi-turn example {example_id}")
        k += 1
    if len(turns) < 2:
        errors.append(f"guide {example_id}: a multi-turn example needs at least two user turns")
    return turns


def check_guide(ctx):
    errors = []
    text = ctx.guide
    version = ctx.intents.get("spec_version")
    fences, prose = _split_fences(text.splitlines(), errors)
    prose_text = "\n".join(line for _, line in prose)

    headings = [(n, len(m.group(1)), m.group(2).strip())
                for n, line in prose for m in [re.match(r"^(#{1,6}) (.+)$", line)] if m]
    h1 = [title for _, level, title in headings if level == 1]
    h2 = [title for _, level, title in headings if level == 2]
    if len(h1) != 1:
        errors.append(f"guide must have exactly one H1 heading, found {len(h1)}")
    for section in REQUIRED_GUIDE_SECTIONS:
        if section not in h2:
            errors.append(f"guide is missing the section '## {section}'")
    slugs = {_slug(title) for _, _, title in headings}
    for anchor in re.findall(r"\]\(#([^)\s]+)\)", prose_text):
        if anchor not in slugs:
            errors.append(f"guide link #{anchor} does not match any heading")

    version_line = re.search(r"^\*\*Specification version:\*\* (\S+)$", text, re.MULTILINE)
    if not version_line or version_line.group(1) != version:
        errors.append(f"guide must state '**Specification version:** {version}'")
    for found in sorted(set(GUIDE_SEMVER_RE.findall(text))):
        if found != version:
            errors.append(f"guide mentions version {found}, expected only {version}")

    for name in ctx.intent_names + ctx.entity_order:
        if not _mentions(text, name):
            errors.append(f"guide never mentions {name!r}")

    definitions = [int(n) for n in MT_DEF_RE.findall(text)]
    if not definitions or definitions != list(range(1, len(definitions) + 1)):
        errors.append(f"guide multi-turn rules must be numbered MT-1 to MT-N in order, found {definitions}")
    for reference in sorted({int(n) for n in MT_REF_RE.findall(text)}):
        if reference not in definitions:
            errors.append(f"guide references undefined rule MT-{reference}")

    for number, line in prose:
        for match in re.finditer(r"`([^`]+)`", line):
            candidate = match.group(1)
            if PATH_RE.fullmatch(candidate) and not (ctx.root / candidate).is_file():
                errors.append(f"guide line {number}: referenced file `{candidate}` does not exist")

    examples = _parse_worked_examples(ctx, prose, errors)
    instances = {}
    for example in examples:
        where = f"guide {example['id']}" + (f" turn {example['turn_index']}" if example["turn_index"] else "")
        entities = _parse_entities_field(ctx, example["entities"], where, errors)
        instance = _instance(ctx, example["intent"], entities, example["id"], example["turn_index"])
        problems = _instance_errors(ctx, instance)
        if problems:
            errors.append(f"{where}: not a valid canonical instance: {problems}")
        if example["intent"] in ctx.intent_names:
            _check_grounding(ctx, instance, example["context"], where, errors)
        instances[(example["id"], example["turn_index"])] = instance

    labeled = {instance["intent"] for instance in instances.values()}
    for name in ctx.intent_names:
        if name not in labeled:
            errors.append(f"guide has no worked example labeled {name!r}")
    if not any(turn is not None for _, turn in instances):
        errors.append("guide has no multi-turn worked example")
    if not any(all(not v for v in i["entities"].values()) for i in instances.values()):
        errors.append("guide has no worked example with every entity empty")

    json_blocks = 0
    for fence in fences:
        if fence["lang"] != "json":
            continue
        json_blocks += 1
        where = f"guide JSON block at line {fence['line']}"
        try:
            data = _load_json("\n".join(fence["body"]))
        except ValueError as exc:
            errors.append(f"{where}: invalid JSON: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{where}: must be a JSON object")
            continue
        problems = _instance_errors(ctx, data)
        if problems:
            errors.append(f"{where}: not a valid canonical instance: {problems}")
        key = (data.get("test_case_id"), data.get("turn_index"))
        if key not in instances:
            errors.append(f"{where}: no worked example {key[0]}" + (f" turn {key[1]}" if key[1] else ""))
        elif not _json_equal(data, instances[key]):
            errors.append(f"{where}: differs from the compact worked example {key[0]}")

    guide_questions = {e["question"]: e["intent"] for e in examples if e["turn_index"] is None}
    matched = 0
    for name in ctx.intent_names:
        for question in ctx.intent_by_name[name].get("examples") or []:
            if question in guide_questions:
                matched += 1
                if guide_questions[question] != name:
                    errors.append(
                        f"intents.yaml lists {question!r} under {name}, but the guide labels it "
                        f"{guide_questions[question]}")

    ctx.details["guide"] = (f"{len(examples)} labeled turns, {json_blocks} JSON blocks, "
                            f"{matched} shared with intents.yaml")
    return errors


def check_denylist(ctx):
    errors = []
    patterns = [(term, re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in term.split()) + r"(?!\w)",
                                  re.IGNORECASE)) for term in DENYLIST_TERMS]
    self_path = Path(__file__).resolve()
    scanned = 0
    for path in _iter_files(ctx.root, SCAN_SUFFIXES):
        if path.resolve() == self_path:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            for term, pattern in patterns:
                if pattern.search(line):
                    errors.append(f"{_rel(ctx.root, path)}:{number}: real or program-specific name {term!r}")
    ctx.details["denylist"] = f"{scanned} files scanned"
    return errors


def _banned_module(name):
    if not isinstance(name, str):
        return False
    if any(name == m or name.startswith(m + ".") for m in BANNED_MODULES):
        return True
    return any(name.split(".")[0].startswith(stem) for stem in BANNED_MODULE_STEMS)


def check_no_model_calls(ctx):
    errors = []
    python_files = 0
    scripts_dir = ctx.root / "scripts"
    for path in _iter_files(scripts_dir, {".py"}):
        python_files += 1
        rel = _rel(ctx.root, path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{rel}: cannot be parsed: {exc}")
            continue
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                func = node.func
                func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if func_name in ("__import__", "import_module"):
                    imported = [node.args[0].value]
            for module in imported:
                if _banned_module(module):
                    errors.append(f"{rel}:{node.lineno}: imports model or network client module {module!r}")
    ctx.details["no_model_calls"] = f"{python_files} offline spec-tool Python files scanned"
    return errors


SECTIONS = [
    ("load", "Load and parse artifacts", check_load, []),
    ("intents", "Intent taxonomy (intents.yaml)", check_intents, ["load"]),
    ("entities", "Entity taxonomy (entities.yaml)", check_entities, ["load"]),
    ("aliases", "Alias and ambiguous-term collisions", check_aliases, ["entities"]),
    ("schema", "JSON Schema meta-validation and lint", check_schema_meta, ["load"]),
    ("cross", "Cross-file consistency", check_cross_file, ["intents", "entities", "schema"]),
    ("selftest", "Schema self-tests", check_self_tests, ["cross"]),
    ("guide", "Annotation guide and worked examples", check_guide, ["cross", "aliases"]),
    ("denylist", "No real company or program names", check_denylist, []),
    ("no_model_calls", "No model or network client code", check_no_model_calls, []),
]


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Validate the Phase 1 specification artifacts offline.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                        help="project root (default: the parent of the scripts directory)")
    args = parser.parse_args(argv)
    if yaml is None:
        print("ERROR: PyYAML is required. Run: python -m pip install -r requirements.txt", file=sys.stderr)
        return 2

    ctx = Context(Path(args.root).resolve())
    print(f"Validating specification artifacts under {ctx.root}")
    if jsonschema is None:
        print("Note: jsonschema is not installed; using the built-in validator only.")
    status = {}
    for key, title, check, depends_on in SECTIONS:
        blocked = [d for d in depends_on if status.get(d) != "PASS"]
        if blocked:
            status[key] = "SKIP"
            print(f"[SKIP] {title} (requires: {', '.join(blocked)})")
            continue
        try:
            errors = check(ctx)
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures as findings
            errors = [f"internal error: {type(exc).__name__}: {exc}"]
        status[key] = "FAIL" if errors else "PASS"
        detail = ctx.details.get(key)
        print(f"[{status[key]}] {title}" + (f" ({detail})" if detail and not errors else ""))
        for message in errors:
            print(f"    - {message}")
    passed = all(value == "PASS" for value in status.values())
    print(f"Result: {'PASSED' if passed else 'FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
