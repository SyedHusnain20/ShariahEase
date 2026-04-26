"""
Zakat Certificate PDF Generator.
Fixed: header line spacing, removed broken Urdu subtitle (renders as boxes).
"""

import io
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

# ── COLOUR PALETTE ────────────────────────────────────────
GREEN_DARK   = colors.HexColor("#0F6E56")
GREEN_MID    = colors.HexColor("#1D9E75")
GREEN_LIGHT  = colors.HexColor("#E1F5EE")
GREEN_BORDER = colors.HexColor("#9FE1CB")
GRAY_TEXT    = colors.HexColor("#6B7280")
GRAY_LIGHT   = colors.HexColor("#F9FAFB")
BLACK        = colors.HexColor("#111827")
WHITE        = colors.white


def _pkr(amount: float) -> str:
    return f"PKR {round(amount):,}"


def generate_zakat_certificate(
    user_name:        str,
    zakat_due:        float,
    zakatable_wealth: float,
    nisab_threshold:  float,
    total_assets:     float = 0.0,
    total_deductions: float = 0.0,
    breakdown:        dict  = None,
    nisab_rate:       str   = "gold",
) -> bytes:

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize     = A4,
        leftMargin   = 2.2 * cm,
        rightMargin  = 2.2 * cm,
        topMargin    = 2.0 * cm,
        bottomMargin = 2.0 * cm,
    )

    content = []

    # ── HEADER: App name ──────────────────────────────────
    # Fixed: explicit spaceBefore/spaceAfter and leading so lines don't merge
    content.append(Paragraph(
        "ShariahEase",
        ParagraphStyle(
            "app_name",
            fontSize   = 24,
            textColor  = GREEN_DARK,
            alignment  = TA_CENTER,
            fontName   = "Helvetica-Bold",
            leading    = 30,          # line height — prevents merging
            spaceAfter = 6,
        )
    ))

    content.append(Paragraph(
        "Islamic Finance AI Assistant &middot; Pakistan",
        ParagraphStyle(
            "app_sub",
            fontSize    = 10,
            textColor   = GRAY_TEXT,
            alignment   = TA_CENTER,
            fontName    = "Helvetica",
            leading     = 14,
            spaceBefore = 0,
            spaceAfter  = 10,
        )
    ))

    content.append(HRFlowable(
        width      = "100%",
        thickness  = 1.5,
        color      = GREEN_MID,
        spaceAfter = 10,
    ))

    # ── CERTIFICATE TITLE ─────────────────────────────────
    content.append(Paragraph(
        "Zakat Calculation Certificate",
        ParagraphStyle(
            "cert_title",
            fontSize    = 16,
            textColor   = BLACK,
            alignment   = TA_CENTER,
            fontName    = "Helvetica-Bold",
            leading     = 22,
            spaceBefore = 6,
            spaceAfter  = 16,
        )
    ))
    # NOTE: Removed Urdu subtitle — ReportLab renders it as boxes (■■■)
    # without a bundled Urdu font file. Removed to keep certificate clean.

    # ── RECIPIENT BOX ─────────────────────────────────────
    recipient_data = [
        ["Issued to:", user_name],
        ["Date:",      datetime.now().strftime("%d %B %Y")],
        ["Nisab standard:", nisab_rate.capitalize() + " standard"],
    ]
    recipient_table = Table(recipient_data, colWidths=[4 * cm, 12 * cm])
    recipient_table.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [GREEN_LIGHT, WHITE]),
        ("TEXTCOLOR",   (0, 0), (0, -1),  GRAY_TEXT),
        ("TEXTCOLOR",   (1, 0), (1, -1),  BLACK),
        ("FONTNAME",    (0, 0), (0, -1),  "Helvetica"),
        ("FONTNAME",    (1, 0), (1, -1),  "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("GRID",        (0, 0), (-1, -1), 0.5, GREEN_BORDER),
        ("PADDING",     (0, 0), (-1, -1), 8),
    ]))
    content.append(recipient_table)
    content.append(Spacer(1, 0.5 * cm))

    # ── FINANCIAL BREAKDOWN ───────────────────────────────
    content.append(Paragraph(
        "Financial Summary",
        ParagraphStyle(
            "section",
            fontSize    = 11,
            textColor   = GREEN_DARK,
            fontName    = "Helvetica-Bold",
            leading     = 16,
            spaceBefore = 12,
            spaceAfter  = 6,
        )
    ))

    breakdown_data = [
        ["Description",             "Amount (PKR)"],
        ["Total zakatable assets",  _pkr(total_assets)],
        ["Total deductions",        f"– {_pkr(total_deductions)}"],
        ["Net zakatable wealth",    _pkr(zakatable_wealth)],
        ["Nisab threshold",         _pkr(nisab_threshold)],
        ["Zakat rate",              "2.5%"],
        ["ZAKAT DUE",               _pkr(zakat_due)],
    ]

    breakdown_table = Table(breakdown_data, colWidths=[11 * cm, 5 * cm])
    breakdown_table.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  GREEN_DARK),
        ("TEXTCOLOR",      (0, 0), (-1, 0),  WHITE),
        ("FONTNAME",       (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",       (0, 0), (-1, 0),  10),
        ("ALIGN",          (1, 0), (1, -1),  "RIGHT"),
        ("FONTNAME",       (0, 1), (-1, -2), "Helvetica"),
        ("FONTSIZE",       (0, 1), (-1, -1), 10),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, GRAY_LIGHT]),
        ("TEXTCOLOR",      (0, 1), (-1, -2), BLACK),
        ("BACKGROUND",     (0, -1), (-1, -1), GREEN_LIGHT),
        ("TEXTCOLOR",      (0, -1), (-1, -1), GREEN_DARK),
        ("FONTNAME",       (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",       (0, -1), (-1, -1), 11),
        ("GRID",           (0, 0), (-1, -1), 0.5, GREEN_BORDER),
        ("PADDING",        (0, 0), (-1, -1), 8),
    ]))
    content.append(breakdown_table)
    content.append(Spacer(1, 0.5 * cm))

    # ── ZAKAT AMOUNT HIGHLIGHT ────────────────────────────
    highlight_data = [[
        Paragraph(
            f'<font color="#0F6E56"><b>Total Zakat Due: {_pkr(zakat_due)}</b></font>',
            ParagraphStyle(
                "hl",
                fontSize  = 14,
                alignment = TA_CENTER,
                fontName  = "Helvetica-Bold",
                leading   = 20,
            )
        )
    ]]
    highlight_table = Table(highlight_data, colWidths=[16 * cm])
    highlight_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN_LIGHT),
        ("GRID",       (0, 0), (-1, -1), 1.5, GREEN_MID),
        ("PADDING",    (0, 0), (-1, -1), 14),
    ]))
    content.append(highlight_table)
    content.append(Spacer(1, 0.6 * cm))

    # ── SUGGESTED CHARITIES ───────────────────────────────
    content.append(Paragraph(
        "Suggested Zakat Recipients in Pakistan",
        ParagraphStyle(
            "section2",
            fontSize    = 11,
            textColor   = GREEN_DARK,
            fontName    = "Helvetica-Bold",
            leading     = 16,
            spaceBefore = 4,
            spaceAfter  = 6,
        )
    ))

    charities = [
        ["Edhi Foundation",         "Orphanages, ambulance, shelter homes nationwide"],
        ["Akhuwat Foundation",      "Interest-free microfinance for poor families"],
        ["Shaukat Khanum Hospital", "Free cancer treatment for deserving patients"],
        ["The Citizens Foundation", "Schools in underserved communities across Pakistan"],
        ["Sundas Foundation",       "Thalassemia and blood disorder treatment"],
    ]
    charity_data = [["Organisation", "Focus Area"]] + charities
    charity_table = Table(charity_data, colWidths=[6 * cm, 10 * cm])
    charity_table.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  GREEN_DARK),
        ("TEXTCOLOR",      (0, 0), (-1, 0),  WHITE),
        ("FONTNAME",       (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",       (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, GRAY_LIGHT]),
        ("FONTNAME",       (0, 1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR",      (0, 1), (-1, -1), BLACK),
        ("GRID",           (0, 0), (-1, -1), 0.5, GREEN_BORDER),
        ("PADDING",        (0, 0), (-1, -1), 7),
    ]))
    content.append(charity_table)
    content.append(Spacer(1, 0.6 * cm))

    # ── DISCLAIMER ────────────────────────────────────────
    content.append(HRFlowable(
        width      = "100%",
        thickness  = 0.5,
        color      = GREEN_BORDER,
        spaceAfter = 8,
    ))
    disclaimer_style = ParagraphStyle(
        "disclaimer",
        fontSize  = 8,
        textColor = GRAY_TEXT,
        alignment = TA_CENTER,
        fontName  = "Helvetica",
        leading   = 12,
    )
    content.append(Paragraph(
        "This certificate is generated for personal record-keeping purposes. "
        "Zakat calculations are based on the information provided by the user. "
        "For a binding ruling, please consult a qualified Islamic scholar or your local Zakat committee. "
        "May Allah accept your Zakat and bless your wealth. Ameen.",
        disclaimer_style,
    ))
    content.append(Spacer(1, 0.2 * cm))
    content.append(Paragraph(
        f"Generated by ShariahEase · {datetime.now().strftime('%d %B %Y at %I:%M %p')}",
        disclaimer_style,
    ))

    doc.build(content)
    return buffer.getvalue()
