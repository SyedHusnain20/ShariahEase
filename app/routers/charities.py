import json
import os
import re
from fastapi import APIRouter, Request, Query
from fastapi.templating import Jinja2Templates

from app.services.web_search import web_search

router = APIRouter(prefix="/charities", tags=["Charities"])
templates = Jinja2Templates(directory="frontend/templates")

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "charities.json"
)

# Cache charities in memory — file never changes at runtime
_CHARITIES_CACHE: list = []

def load_charities() -> list:
    global _CHARITIES_CACHE
    if not _CHARITIES_CACHE:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            _CHARITIES_CACHE = json.load(f)
    return _CHARITIES_CACHE


@router.get("/list")
async def charities_list(category: str = Query(default="all")):
    charities = load_charities()
    if category != "all":
        charities = [c for c in charities if c["category_tag"] == category]
    return charities


@router.get("/categories")
async def categories():
    charities = load_charities()
    seen = set()
    cats = [{"tag": "all", "label": "All"}]
    for c in charities:
        tag = c["category_tag"]
        if tag not in seen:
            seen.add(tag)
            cats.append({"tag": tag, "label": c["category"]})
    return cats


@router.get("/search")
async def search_charity_web(q: str = Query(..., min_length=2, max_length=100)):
    """
    When a charity is not found in the local database, search the web
    for information about it — contact, website, cause, SECP status.
    Returns a structured result the frontend renders as a live web card.
    """
    # Sanitize query
    clean_q = re.sub(r"[^\w\s\-\.]", "", q).strip()[:100]

    query = f"{clean_q} Pakistan charity NGO donation official website"
    results = await web_search(query, max_results=5)

    if not results:
        return {"found": False, "results": []}

    # Filter to only useful results — skip unrelated news/directories
    useful = []
    for r in results:
        title = r.get("title", "")
        url   = r.get("url", "")
        snippet = r.get("snippet", "")
        # Skip generic directory/listing sites
        skip_domains = ["yellowpages", "justdial", "facebook.com/search", "twitter.com/search"]
        if any(d in url for d in skip_domains):
            continue
        if title and snippet:
            useful.append({
                "title":   title,
                "url":     url,
                "snippet": snippet,
            })

    return {
        "found":   len(useful) > 0,
        "query":   clean_q,
        "results": useful[:4],
    }
