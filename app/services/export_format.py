"""
Turn the rows of the data export (expense_service.export_expenses) into CSV or JSON text.

CSV safety: a spreadsheet treats a cell that starts with = + - @ (or a tab / carriage return) as a FORMULA, so a note
such as `=HYPERLINK(...)` typed by a user could run when the export is opened in Excel. Every text cell that could
start a formula gets a leading apostrophe, which makes the spreadsheet read it as plain text.

Money is written as exact rupee text built from integer paisa (no floats), next to the integer paisa itself.
"""
import csv
import io
import json
from datetime import datetime

CSV_COLUMNS = [
    "id", "client_id", "occurred_on", "amount_rupees", "amount_paisa", "currency",
    "category", "category_name", "subcategory", "subcategory_name", "note", "payment_method", "source",
    "shariah_flag", "shariah_flag_dismissed", "version", "created_at", "updated_at", "deleted_at",
]

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n")
BOM = "\ufeff"          # lets Excel read Urdu (UTF-8) correctly


def amount_rupees(paisa: int) -> str:
    """12550 -> '125.50'. Integer arithmetic only."""
    if isinstance(paisa, bool) or not isinstance(paisa, int) or paisa < 0:
        raise ValueError("paisa must be a non-negative int")
    return f"{paisa // 100}.{paisa % 100:02d}"


def csv_safe(value: str) -> str:
    """Prefix an apostrophe when the cell could be read as a formula (also after leading spaces)."""
    probe = value.lstrip(" \u00a0")
    if value.startswith(_FORMULA_START) or probe.startswith(_FORMULA_START):
        return "'" + value
    return value


def _iso(value):
    return None if value is None else value.isoformat()


def _record(row) -> dict:
    """The plain dict used by both formats (all values are text, numbers or booleans)."""
    return {
        "id": row.id,
        "client_id": str(row.client_id),
        "occurred_on": row.occurred_on.isoformat(),
        "amount_rupees": amount_rupees(row.amount_paisa),
        "amount_paisa": row.amount_paisa,
        "currency": row.currency,
        "category": row.category_slug,
        "category_name": row.category_name_en,
        "subcategory": row.subcategory_slug,
        "subcategory_name": row.subcategory_name_en,
        "note": row.note,
        "payment_method": row.payment_method,
        "source": row.source,
        "shariah_flag": row.shariah_flag,
        "shariah_flag_dismissed": row.shariah_flag_dismissed,
        "version": row.version,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "deleted_at": _iso(row.deleted_at),
    }


def to_csv(rows) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        rec = _record(row)
        cells = []
        for column in CSV_COLUMNS:
            value = rec[column]
            if value is None:
                cells.append("")
            elif isinstance(value, bool):
                cells.append("true" if value else "false")
            elif isinstance(value, str):
                cells.append(csv_safe(value))
            else:
                cells.append(str(value))
        writer.writerow(cells)
    return BOM + out.getvalue()


def to_json(rows, exported_at: datetime) -> str:
    records = [_record(r) for r in rows]
    payload = {
        "exported_at": exported_at.isoformat(),
        "format_version": 1,
        "currency": "PKR",
        "note": "amount_rupees is exact text; amount_paisa is the same amount as a whole number of paisa. "
                "Rows with deleted_at set are deleted rows that have not been purged yet.",
        "count": len(records),
        "expenses": records,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
