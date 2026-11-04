from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

router    = APIRouter()
templates = Jinja2Templates(directory="frontend/templates")

@router.get("/")
async def home(request: Request):
    return templates.TemplateResponse("pages/chatbot.html", {"request": request, "active_page": "chatbot"})

@router.get("/zakat")
async def zakat_page(request: Request):
    return templates.TemplateResponse("pages/home.html", {"request": request, "active_page": "zakat"})

@router.get("/screener")
async def screener(request: Request):
    return templates.TemplateResponse("pages/screener.html", {"request": request, "active_page": "screener"})

@router.get("/mudarabah")
async def mudarabah(request: Request):
    return templates.TemplateResponse("pages/mudarabah.html", {"request": request, "active_page": "mudarabah"})

@router.get("/chatbot")
async def chatbot(request: Request):
    return templates.TemplateResponse("pages/chatbot.html", {"request": request, "active_page": "chatbot"})

@router.get("/charities")
async def charities(request: Request):
    return templates.TemplateResponse("pages/charities.html", {"request": request, "active_page": "charities"})

@router.get("/about")
async def about(request: Request):
    return templates.TemplateResponse("pages/about.html", {"request": request, "active_page": "about"})

@router.get("/expenses")
async def expenses_page(request: Request):
    return templates.TemplateResponse("pages/expenses.html", {"request": request, "active_page": "expenses"})

# The service worker must be served from the site root to control every page; it is never cached by the browser
# for long, so a new version is picked up on the next visit.
_STATIC = Path("frontend/static")

@router.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(_STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

@router.get("/manifest.webmanifest", include_in_schema=False)
async def web_manifest():
    return FileResponse(_STATIC / "manifest.webmanifest", media_type="application/manifest+json",
                        headers={"Cache-Control": "public, max-age=3600"})
