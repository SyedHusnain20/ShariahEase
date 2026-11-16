"""
Turn the rows of the data export (expense_service.export_expenses) into CSV (for Excel) or JSON text.

The export is deliberately small, for people who just want to see their spending: ID, date, amount in rupees, category,
sub-category and source (manual or chatbot). Nothing else leaves the app in the file.

CSV safety: a spreadsheet treats a cell that starts with = + - @ (or a tab / carriage return) as a FORMULA. The file no
longer carries anything a user types, but every text cell is still protected (a leading apostrophe makes the
spreadsheet read it as plain text), so a future change cannot reintroduce the risk.

Money is written as exact rupee text built from integer paisa (no floats).
"""
import csv
import io
import json
from datetime import datetime

# What a person sees in Excel (plain words) and what a program reads in JSON (plain keys).
CSV_HEADERS = ["ID", "Date", "Amount (Rs)", "Category", "Sub-category", "Source"]
JSON_KEYS = ["id", "date", "amount_rupees", "category", "sub_category", "source"]

# The stored source -> what the user sees. Every value the database allows must be listed (a test checks it).
SOURCE_LABEL = {
    "manual": "manual",
    "pwa_sync": "manual",        # typed by the user while offline and synced later: still manual
    "ai_chat": "chatbot",
    "whatsapp": "chatbot",       # the WhatsApp assistant is a chatbot too
}

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n")
BOM = "\ufeff"          # lets Excel read non-English text (UTF-8) correctly


def amount_rupees(paisa: int) -> str:
    """12550 -> '125.50'. Integer arithmetic only."""
    if isinstance(paisa, bool) or not isinstance(paisa, int) or paisa < 0:
        raise ValueError("paisa must be a non-negative int")
    return f"{paisa // 100}.{paisa % 100:02d}"


def source_label(source: str) -> str:
    """'ai_chat' -> 'chatbot'. An unknown stored value is an error, never a silent guess."""
    try:
        return SOURCE_LABEL[source]
    except KeyError:
        raise ValueError(f"no export label for source {source!r}") from None


def csv_safe(value: str) -> str:
    """Prefix an apostrophe when the cell could be read as a formula (also after leading spaces)."""
    probe = value.lstrip(" \u00a0")
    if value.startswith(_FORMULA_START) or probe.startswith(_FORMULA_START):
        return "'" + value
    return value


def _record(row) -> dict:
    """The plain values used by both formats."""
    return {
        "id": row.id,
        "date": row.occurred_on.isoformat(),
        "amount_rupees": amount_rupees(row.amount_paisa),
        "category": row.category,
        "sub_category": row.sub_category,
        "source": source_label(row.source),
    }


def to_csv(rows) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for row in rows:
        rec = _record(row)
        cells = []
        for key in JSON_KEYS:
            value = rec[key]
            if value is None:
                cells.append("")
            elif isinstance(value, str):
                cells.append(csv_safe(value))
            else:
                cells.append(str(value))
        writer.writerow(cells)
    return BOM + out.getvalue()


def to_json(rows, exported_at: datetime) -> str:
    records = [_record(r) for r in rows]
    payload = {"exported_at": exported_at.isoformat(), "count": len(records), "expenses": records}
    return json.dumps(payload, ensure_ascii=False, indent=2)
