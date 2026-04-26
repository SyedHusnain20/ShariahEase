import json
import os
from fastapi import APIRouter, Request, Query
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/charities", tags=["Charities"])
templates = Jinja2Templates(directory="frontend/templates")

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "charities.json"
)


def load_charities() -> list:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/list")
async def charities_list(category: str = Query(default="all")):
    """
    Returns charities filtered by category.
    Category options: all, welfare, health, education,
                      microfinance, food, relief
    """
    charities = load_charities()
    if category != "all":
        charities = [c for c in charities if c["category_tag"] == category]
    return charities


@router.get("/categories")
async def categories():
    """Returns all unique categories for the filter buttons."""
    charities = load_charities()
    seen = set()
    cats = [{"tag": "all", "label": "All"}]
    for c in charities:
        tag = c["category_tag"]
        if tag not in seen:
            seen.add(tag)
            cats.append({"tag": tag, "label": c["category"]})
    return cats
