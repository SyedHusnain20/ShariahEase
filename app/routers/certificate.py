import re
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.database import models
from app.services.pdf_generator import generate_zakat_certificate
from app.dependencies import get_current_user

router = APIRouter(prefix="/zakat", tags=["Certificate"])


def _safe_filename(name: str) -> str:
    """Strip anything that isn't alphanumeric, space, hyphen, or underscore."""
    clean = re.sub(r"[^\w\s\-]", "", name).strip()
    return clean[:80] if clean else "User"


@router.get("/certificate")
async def download_certificate(
    request:          Request,
    user_name:        str,
    zakat_due:        float,
    zakatable_wealth: float,
    nisab_threshold:  float,
    total_assets:     float = 0.0,
    total_deductions: float = 0.0,
    nisab_rate:       str   = "gold",
    db:               Session = Depends(get_db),
    current_user:     models.User = Depends(get_current_user),
):
    """
    Generates and streams the Zakat certificate PDF.
    Requires authentication — cookie-based JWT checked by get_current_user.
    Called from the calculator page via window.location.href (browser redirect).
    """
    # Sanitize user_name before embedding in PDF and filename
    safe_name = _safe_filename(user_name)

    # Validate nisab_rate to prevent garbage values reaching the PDF
    if nisab_rate not in ("gold", "silver"):
        nisab_rate = "gold"

    pdf_bytes = generate_zakat_certificate(
        user_name        = safe_name,
        zakat_due        = zakat_due,
        zakatable_wealth = zakatable_wealth,
        nisab_threshold  = nisab_threshold,
        total_assets     = total_assets,
        total_deductions = total_deductions,
        nisab_rate       = nisab_rate,
    )

    filename = f"Zakat_Certificate_{safe_name.replace(' ', '_')}.pdf"

    return Response(
        content    = pdf_bytes,
        media_type = "application/pdf",
        headers    = {
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )
