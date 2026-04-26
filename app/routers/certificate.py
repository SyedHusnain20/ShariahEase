from fastapi import APIRouter
from fastapi.responses import Response
from app.services.pdf_generator import generate_zakat_certificate

router = APIRouter(prefix="/zakat", tags=["Certificate"])


@router.get("/certificate")
async def download_certificate(
    user_name:        str,
    zakat_due:        float,
    zakatable_wealth: float,
    nisab_threshold:  float,
    total_assets:     float = 0.0,
    total_deductions: float = 0.0,
    nisab_rate:       str   = "gold",
):
    """
    Generates and streams the Zakat certificate PDF.
    Called from the calculator page download button via URL params.
    """
    pdf_bytes = generate_zakat_certificate(
        user_name        = user_name,
        zakat_due        = zakat_due,
        zakatable_wealth = zakatable_wealth,
        nisab_threshold  = nisab_threshold,
        total_assets     = total_assets,
        total_deductions = total_deductions,
        nisab_rate       = nisab_rate,
    )

    filename = f"Zakat_Certificate_{user_name.replace(' ', '_')}.pdf"

    return Response(
        content      = pdf_bytes,
        media_type   = "application/pdf",
        headers      = {
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )
