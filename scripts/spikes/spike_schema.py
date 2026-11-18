"""
Step 4a spikes: the "understand" schema.

ONE set of Pydantic models produces the strict JSON schema that is sent to Groq AND validates what comes back
(the same rule as the real build in 4b). The 53 category values are read from the real migration 0004, so the
spike always tests the real list.

Nothing here talks to the network.
"""
import ast
import copy
import json
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "0004_phase1_expenses.py"


# --------------------------------------------------------------------------------------------------
# The category list comes from the migration (read as text, never imported or run)
# --------------------------------------------------------------------------------------------------
def load_taxonomy(path: Path = MIGRATION):
    """[(slug, name_en, name_ur)] for the 13 categories followed by the 40 subcategories, in seed order."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("CATEGORIES", "SUBCATEGORIES")):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    if set(found) != {"CATEGORIES", "SUBCATEGORIES"}:
        raise RuntimeError("could not find CATEGORIES and SUBCATEGORIES in the migration")
    rows = [(r[0], r[1], r[2]) for r in found["CATEGORIES"]]
    rows += [(r[1], r[2], r[3]) for r in found["SUBCATEGORIES"]]
    return rows


TAXONOMY = load_taxonomy()
SLUGS = [row[0] for row in TAXONOMY]

# One enum class => the schema holds a single "$defs/Slug" that every field points to ($ref), like the real build.
Slug = Enum("Slug", {s.replace(".", "__"): s for s in SLUGS}, type=str)

Intent = Literal["expense_log", "expense_query", "expense_edit_delete", "islamic_finance_qa", "off_topic", "unclear"]
Language = Literal["en", "ur", "roman_ur", "mixed"]
Payment = Literal["cash", "bank_transfer", "card", "wallet", "other"]
GroupBy = Literal["none", "category", "subcategory", "day", "month"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpenseItem(_Strict):
    amount_text: str = Field(description="The amount exactly as the person wrote it, copied character for character. Never convert it.")
    category_slug: Slug
    note: Optional[str] = Field(description="Short note in the person's own words, or null.")
    date_text: Optional[str] = Field(description="The date words exactly as written (for example 'kal', 'yesterday'), or null.")
    payment_method: Optional[Payment]


class QuerySpec(_Strict):
    period_text: Optional[str] = Field(description="The period words exactly as written, or null.")
    category_slug: Optional[Slug]
    group_by: GroupBy


class EditSpec(_Strict):
    action: Literal["update", "delete"]
    target_text: str = Field(description="Words that point at the expense, exactly as written.")
    new_amount_text: Optional[str]
    new_category_slug: Optional[Slug]


class Understanding(_Strict):
    intent: Intent
    language: Language
    items: list[ExpenseItem]
    query: Optional[QuerySpec]
    edit: Optional[EditSpec]


# --------------------------------------------------------------------------------------------------
# Smaller schemas for S2: each one adds ONE feature, so a failure points at the feature that broke
# --------------------------------------------------------------------------------------------------
class _T1(_Strict):
    intent: Intent


class _ItemPlain(_Strict):
    amount_text: str
    category: str


class _T2(_Strict):
    items: list[_ItemPlain]


class _ItemNullable(_Strict):
    amount_text: str
    note: Optional[str]
    date_text: Optional[str]


class _T3(_Strict):
    items: list[_ItemNullable]


class _T4(_Strict):
    category_slug: Slug


STAGES = [
    ("trivial", _T1, "a single value from a short list", "Spent Rs 500 on chai today"),
    ("defs_ref", _T2, "a list of objects ($defs and $ref)", "Petrol 3000 aur chai 50"),
    ("nullable", _T3, "nested fields that may be null", "Petrol 3000 yesterday"),
    ("enum53", _T4, "one value out of the 53 categories ($ref to an enum)", "bijli ka bill 8500 diya"),
]


# --------------------------------------------------------------------------------------------------
# Step 4a-2: four ways to say "this field may be empty", because Groq's strict mode refused the first one
#   current    anyOf [category, null]               (refused: "anyOf branches must be disambiguated ...")
#   sentinel   no null anywhere: "" for no text, "none" for no category, "unknown" for no payment method,
#              and query/edit are always present with empty values (our code turns them back into null)
#   anyof_text sentinel for categories and objects, but text fields keep anyOf [string, null]
#   typearray  the same fields as "current", written as "type": ["string", "null"] (no anyOf)
# --------------------------------------------------------------------------------------------------
SlugN = Enum("SlugN", {**{s.replace(".", "__"): s for s in SLUGS}, "NONE": "none"}, type=str)
PaymentN = Literal["cash", "bank_transfer", "card", "wallet", "other", "unknown"]
ActionN = Literal["update", "delete", "none"]


class ItemS(_Strict):
    amount_text: str = Field(description="The amount exactly as the person wrote it, copied character for character. Never convert it.")
    category_slug: SlugN
    note: str = Field(description="Short note in the person's own words, or an empty string.")
    date_text: str = Field(description="The date words exactly as written (for example 'kal'), or an empty string.")
    payment_method: PaymentN


class QueryS(_Strict):
    period_text: str = Field(description="The period words exactly as written, or an empty string.")
    category_slug: SlugN
    group_by: GroupBy


class EditS(_Strict):
    action: ActionN
    target_text: str = Field(description="Words that point at the expense, exactly as written, or an empty string.")
    new_amount_text: str
    new_category_slug: SlugN


class UnderstandingS(_Strict):
    intent: Intent
    language: Language
    items: list[ItemS]
    query: QueryS
    edit: EditS


class ItemA(_Strict):
    amount_text: str = Field(description="The amount exactly as the person wrote it, copied character for character. Never convert it.")
    category_slug: SlugN
    note: Optional[str] = Field(description="Short note in the person's own words, or null.")
    date_text: Optional[str] = Field(description="The date words exactly as written, or null.")
    payment_method: PaymentN


class QueryA(_Strict):
    period_text: Optional[str]
    category_slug: SlugN
    group_by: GroupBy


class EditA(_Strict):
    action: ActionN
    target_text: str
    new_amount_text: Optional[str]
    new_category_slug: SlugN


class UnderstandingA(_Strict):
    intent: Intent
    language: Language
    items: list[ItemA]
    query: QueryA
    edit: EditA


def collapse_nullable(schema: dict) -> dict:
    """Rewrite every anyOf [X, null] as X with "type": [..., "null"] (and null added to an enum). No anyOf is left."""
    defs = schema.get("$defs", {})

    def resolve(node):
        return copy.deepcopy(defs[node["$ref"].split("/")[-1]])

    def walk(node):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        branches = node.get("anyOf")
        if isinstance(branches, list) and len(branches) == 2 and {"type": "null"} in branches:
            other = next(b for b in branches if b != {"type": "null"})
            out = walk(resolve(other) if "$ref" in other else other)
            out.update({k: v for k, v in node.items() if k != "anyOf"})
            kind = out.get("type", "string")
            out["type"] = kind + ["null"] if isinstance(kind, list) else [kind, "null"]
            if isinstance(out.get("enum"), list) and None not in out["enum"]:
                out["enum"] = out["enum"] + [None]
            return out
        return {k: walk(v) for k, v in node.items()}

    body = walk({k: v for k, v in schema.items() if k != "$defs"})
    walked_defs = {name: walk(d) for name, d in defs.items()}
    used, todo = {}, [body]
    while todo:                                      # keep only the definitions that are still pointed at
        node = todo.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref and ref.split("/")[-1] not in used:
                used[ref.split("/")[-1]] = walked_defs[ref.split("/")[-1]]
                todo.append(used[ref.split("/")[-1]])
            todo.extend(node.values())
        elif isinstance(node, list):
            todo.extend(node)
    if used:
        body["$defs"] = used
    return body


# name -> (model that validates the answer, how its schema is made, how the prompt describes "empty")
VARIANTS = {
    "sentinel": (UnderstandingS, "plain", "sentinel"),
    "anyof_text": (UnderstandingA, "plain", "sentinel"),
    "typearray": (Understanding, "collapse", "null"),
    "current": (Understanding, "plain", "null"),
}
VARIANT_ORDER = ["sentinel", "anyof_text", "typearray", "current"]
SAFE_ORDER = VARIANT_ORDER[:3]          # "current" is kept only to show the original refusal again


def variant_model(name: str):
    return VARIANTS[name][0]


def variant_style(name: str) -> str:
    return VARIANTS[name][2]


def variant_schema(name: str) -> dict:
    model, how, _ = VARIANTS[name]
    schema = strict_schema(model)
    return to_strict(collapse_nullable(schema)) if how == "collapse" else schema


# --------------------------------------------------------------------------------------------------
# Strict form
# --------------------------------------------------------------------------------------------------
def to_strict(node):
    """Strict mode wants every property listed in "required" and every object closed. Titles and defaults are noise."""
    if isinstance(node, list):
        return [to_strict(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in ("title", "default"):
            continue
        out[key] = to_strict(value)
    if isinstance(out.get("properties"), dict):
        out["required"] = list(out["properties"].keys())
        out["additionalProperties"] = False
    return out


def strict_schema(model) -> dict:
    return to_strict(model.model_json_schema())


def response_format(model, name: str = "understanding", strict: bool = True) -> dict:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": strict, "schema": strict_schema(model)}}


def variant_response_format(name: str, strict: bool = True) -> dict:
    return {"type": "json_schema", "json_schema": {"name": "understanding", "strict": strict, "schema": variant_schema(name)}}


def schema_features(schema: dict) -> dict:
    """What the schema uses, so a report can say what was actually tested."""
    text = json.dumps(schema, ensure_ascii=False)
    enum_sizes = []

    def walk(n):
        if isinstance(n, dict):
            if isinstance(n.get("enum"), list):
                enum_sizes.append(len(n["enum"]))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(schema)
    return {
        "chars": len(text),
        "has_defs": "$defs" in schema,
        "has_ref": '"$ref"' in text,
        "has_nullable_union": '"type": "null"' in text,
        "has_anyof": '"anyOf"' in text,
        "has_type_array": '"type": ["' in text,
        "largest_enum": max(enum_sizes) if enum_sizes else 0,
    }
