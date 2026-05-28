from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router    = APIRouter()
templates = Jinja2Templates(directory="frontend/templates")

@router.get("/")
async def home(request: Request):
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

