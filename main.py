from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv
import os, time, logging, hmac, hashlib
from collections import defaultdict

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Silence Chrome DevTools probe noise
logging.getLogger("uvicorn.access").addFilter(
    type("_WellKnownFilter", (logging.Filter,), {
        "filter": lambda self, r: "/.well-known/" not in r.getMessage()
    })()
)

VERIFY_TOKEN  = os.getenv("VERIFY_TOKEN", "shariahease-CHANGE-ME")
IS_PRODUCTION = os.getenv("ENV", "development") == "production"

# ── FastAPI — hide docs in production ────────────────────────────────────────
app = FastAPI(
    title=os.getenv("APP_NAME", "ShariahEase"),
    description="Islamic Finance AI Assistant",
    version="1.0.0",
    docs_url=None   if IS_PRODUCTION else "/docs",
    redoc_url=None  if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

# ── Security Headers ──────────────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["X-XSS-Protection"]       = "1; mode=block"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        if IS_PRODUCTION:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# ── Rate Limiter ──────────────────────────────────────────────────────────────
# Tracks per-IP request counts in a 60-second rolling window.
# BUG FIXED: previously all local requests shared "127.0.0.1" bucket and
# static file requests counted — causing the app to rate-limit itself after
# ~8 page loads. Now:
#   • Static files are excluded entirely (they are not API abuse vectors)
#   • Localhost (127.0.0.1, ::1) is exempt in development mode
#   • X-Forwarded-For is used when behind a proxy in production
#   • Limits are realistic for a single active user + small team

_rate_store: dict[str, list[float]] = defaultdict(list)

WINDOW          = 60    # seconds
GLOBAL_LIMIT    = 300   # requests per IP per minute (all routes except static)
AUTH_LIMIT      = 15    # login / signup attempts per IP per minute
AI_LIMIT        = 60    # chat, screener, zakat calls per IP per minute

AUTH_PATHS = {"/auth/login", "/auth/signup"}
AI_PATHS   = {"/chat/message", "/screener/check", "/voice/ask", "/zakat/calculate"}

# Paths that are NEVER rate-limited (static assets, health check)
EXEMPT_PREFIXES = ("/static/", "/favicon")
EXEMPT_PATHS    = {"/health", "/"}

def _get_client_ip(request: Request) -> str:
    """
    Real IP resolution order:
    1. X-Forwarded-For (set by Nginx / Cloudflare in production)
    2. request.client.host (direct connection — always 127.0.0.1 locally)
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip static files and health — never rate-limit these
        if path in EXEMPT_PATHS or any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        ip  = _get_client_ip(request)
        now = time.time()

        # Skip rate limiting for localhost in development
        if not IS_PRODUCTION and ip in LOCALHOST_IPS:
            return await call_next(request)

        # Clean window + record this request
        _rate_store[ip] = [t for t in _rate_store[ip] if now - t < WINDOW]
        _rate_store[ip].append(now)
        count = len(_rate_store[ip])

        if path in AUTH_PATHS and count > AUTH_LIMIT:
            logger.warning("Auth rate limit: ip=%s count=%d", ip, count)
            return JSONResponse(
                {"detail": "Too many attempts. Please wait 60 seconds."},
                status_code=429, headers={"Retry-After": "60"},
            )

        if path in AI_PATHS and count > AI_LIMIT:
            logger.warning("AI rate limit: ip=%s path=%s count=%d", ip, path, count)
            return JSONResponse(
                {"detail": "Too many requests. Please wait a moment."},
                status_code=429, headers={"Retry-After": "60"},
            )

        if count > GLOBAL_LIMIT:
            logger.warning("Global rate limit: ip=%s count=%d", ip, count)
            return JSONResponse(
                {"detail": "Rate limit exceeded. Please slow down."},
                status_code=429, headers={"Retry-After": "60"},
            )

        return await call_next(request)

app.add_middleware(RateLimitMiddleware)

# ── Static & Templates ────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
templates = Jinja2Templates(directory="frontend/templates")

# ── DB init ───────────────────────────────────────────────────────────────────
from app.database.db import engine, Base
from app.database import models
Base.metadata.create_all(bind=engine)

from app.services.rag_service import rag_service

@app.on_event("startup")
async def startup_event():
    rag_service.load()
    from app.services.whisper_service import preload_model
    preload_model()

# ── Routers ───────────────────────────────────────────────────────────────────
from app.routers import pages, zakat, mudarabah, chatbot, screener, certificate, charities, auth
app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(zakat.router)
app.include_router(mudarabah.router)
app.include_router(chatbot.router)
app.include_router(screener.router)
app.include_router(certificate.router)
app.include_router(charities.router)

# ── WhatsApp Webhook ──────────────────────────────────────────────────────────
@app.get("/webhook")
async def verify(
    hub_mode: str          = Query(alias="hub.mode"),
    hub_challenge: str     = Query(alias="hub.challenge"),
    hub_verify_token: str  = Query(alias="hub.verify_token"),
):
    if hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    logger.warning("Invalid webhook verify token received")
    return PlainTextResponse("Forbidden", status_code=403)

@app.post("/webhook")
async def receive(request: Request):
    # Validate Meta signature in production
    if IS_PRODUCTION:
        secret = os.getenv("WHATSAPP_APP_SECRET", "")
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        if secret and sig_header:
            body = await request.body()
            expected = "sha256=" + hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, sig_header):
                logger.warning("Invalid WhatsApp webhook signature")
                raise HTTPException(status_code=403, detail="Invalid signature")
            # Re-parse body since we already consumed it
            import json
            data = json.loads(body)
        else:
            data = await request.json()
    else:
        data = await request.json()

    logger.info("WhatsApp webhook received")
    try:
        msg    = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender = msg["from"]
        text   = msg["text"]["body"]
        logger.info("Message from %s", sender)
        from app.services.whatsapp_service import handle_message
        await handle_message(sender, text)
    except (KeyError, IndexError):
        pass
    return JSONResponse({"status": "ok"})

# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    if IS_PRODUCTION:
        return {"status": "ok"}
    return {"status": "ok", "rag_ready": rag_service.is_ready}
