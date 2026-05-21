from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import db
from .config import load_config
from .notifier import send_verification

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

cfg = load_config()
db.init_db()

app = FastAPI(title="Aachen Termin Tracker")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _common_ctx(request: Request) -> dict:
    return {
        "request": request,
        "anliegen": cfg.anliegen,
        "impressum": cfg.legal.impressum,
        "datenschutz_contact": cfg.legal.datenschutz_contact,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", _common_ctx(request))


@app.post("/subscribe", response_class=HTMLResponse)
async def subscribe(
    request: Request,
    email: str = Form(...),
    anliegen: List[str] = Form(default=[]),
    accept_privacy: str = Form(default=""),
) -> HTMLResponse:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    valid_ids = {a.id for a in cfg.anliegen}
    anliegen = [a for a in anliegen if a in valid_ids]
    if not anliegen:
        raise HTTPException(status_code=400, detail="Please pick at least one category")
    if accept_privacy != "yes":
        raise HTTPException(status_code=400, detail="You must accept the privacy policy")

    user_id, verify_token = db.create_unverified_user(email, anliegen)
    try:
        await send_verification(cfg, email, verify_token)
    except Exception:
        log.exception("failed to send verification email")
        raise HTTPException(status_code=502, detail="Could not send verification email. Try again.")
    ctx = _common_ctx(request)
    ctx["email"] = email
    return templates.TemplateResponse("verify_sent.html", ctx)


@app.get("/verify", response_class=HTMLResponse)
async def verify(request: Request, token: str) -> HTMLResponse:
    uid = db.verify_user(token)
    ctx = _common_ctx(request)
    ctx["ok"] = uid is not None
    return templates.TemplateResponse("verified.html", ctx)


@app.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(request: Request, token: str) -> HTMLResponse:
    email = db.unsubscribe(token)
    ctx = _common_ctx(request)
    ctx["ok"] = email is not None
    ctx["email"] = email
    return templates.TemplateResponse("unsubscribed.html", ctx)


@app.get("/impressum", response_class=HTMLResponse)
async def impressum(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("impressum.html", _common_ctx(request))


@app.get("/datenschutz", response_class=HTMLResponse)
async def datenschutz(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("datenschutz.html", _common_ctx(request))


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
