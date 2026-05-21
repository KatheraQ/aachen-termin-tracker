"""
Production scraper for the StädteRegion Aachen Ausländerbehörde booking site.

Flow (verified empirically on 2026-05-21 via discover.py):
  step 1  /auslaenderamt/                → click button.select_mdt_btn
  step 2  /auslaenderamt/select2?md=1    → set input.cnc-number qty=1 via JS,
                                           click #WeiterButton,
                                           dismiss .btn-ok info modal if shown
  step 3  /auslaenderamt/location?...    → JS-click input[name=select_location]
                                           (it's type=submit, navigates on its own)
  step 4  /auslaenderamt/suggest         → read availability:
                                             "Kein freier Termin verfügbar"  → no slots
                                             otherwise parse date cells
"""
from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator, List

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import Anliegen, ScraperCfg

log = logging.getLogger(__name__)

STEP_TIMEOUT_MS = 15_000
NO_SLOTS_PHRASES = (
    "Kein freier Termin",
    "Keine Zeiten verfügbar",
    "keine freien Termine",
)


@asynccontextmanager
async def browser_session(cfg: ScraperCfg) -> AsyncIterator[Page]:
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=cfg.headless)
        context: BrowserContext = await browser.new_context(
            user_agent=cfg.user_agent,
            locale="de-DE",
            timezone_id="Europe/Berlin",
        )
        page = await context.new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        try:
            yield page
        finally:
            await context.close()
            await browser.close()


async def _dismiss_cookies(page: Page) -> None:
    for sel in ("#cookie_msg_btn_no", "#cookie_msg_btn_yes"):
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=2000)
                await page.wait_for_timeout(200)
                return
        except Exception:
            pass


async def _step1_pick_behoerde(page: Page) -> None:
    btn = page.locator("button.select_mdt_btn").first
    await btn.wait_for()
    await btn.click()
    await page.wait_for_load_state("domcontentloaded")


async def _step2_pick_anliegen(page: Page, anliegen: Anliegen) -> bool:
    """Find the cnc-number input matching anliegen.label_de in its aria-label,
    set value=1 via JS (the panel may be collapsed), submit, dismiss modal."""
    pattern = re.escape(anliegen.label_de)
    target = await page.evaluate(
        """(pat) => {
            const re = new RegExp('Anzahl für das Anliegen ' + pat + '\\\\.', '');
            const inputs = Array.from(document.querySelectorAll('input.cnc-number, input[type=number][name^="cnc-"]'));
            for (const el of inputs) {
                const aria = el.getAttribute('aria-label') || '';
                if (re.test(aria)) {
                    el.value = '1';
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return {id: el.id, aria};
                }
            }
            return null;
        }""",
        pattern,
    )
    if target is None:
        log.warning("Anliegen not found: %r (check label_de in config.yaml)", anliegen.label_de)
        return False
    log.debug("picked anliegen input id=%s", target["id"])
    weiter = page.locator("#WeiterButton").first
    await weiter.click()
    await page.wait_for_timeout(800)
    # If an info/confirmation modal shows up, click its OK button.
    ok = page.locator(".btn-ok").first
    if await ok.count() > 0 and await ok.is_visible():
        await ok.click()
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(800)
    return True


async def _step3_pick_standort(page: Page) -> bool:
    """Some Anliegen route to a single Standort; JS-click it (type=submit, submits form)."""
    clicked = await page.evaluate(
        """() => {
            const btn = document.querySelector('input[name="select_location"], .map_loc_btn');
            if (!btn) return false;
            btn.click();
            return true;
        }"""
    )
    if not clicked:
        log.warning("no Standort button on step 3")
        return False
    try:
        await page.wait_for_url(re.compile(r"suggest"), timeout=8000)
    except Exception:
        await page.wait_for_load_state("networkidle", timeout=8000)
    await page.wait_for_timeout(1500)
    return True


async def _step4_read_calendar(page: Page) -> List[str]:
    """Return ISO date strings (YYYY-MM-DD) for every available appointment day.
    Returns [] if the page reports no slots."""
    # Cheap check first: the "no slots" banner.
    body_text = await page.evaluate("() => document.body.innerText")
    for phrase in NO_SLOTS_PHRASES:
        if phrase.lower() in body_text.lower():
            return []

    # Extract any date-like attribute or content from clickable calendar elements.
    dates = await page.evaluate(
        """() => {
            const out = new Set();
            const isoRe = /(\\d{4}-\\d{2}-\\d{2})/;
            const deRe = /(\\d{2})\\.(\\d{2})\\.(\\d{4})/;
            const norm = (s) => {
                let m = s.match(isoRe);
                if (m) return m[1];
                m = s.match(deRe);
                if (m) return m[3] + '-' + m[2] + '-' + m[1];
                return null;
            };
            // Anything that looks like a clickable date: anchors, buttons, td/cells.
            const candidates = Array.from(document.querySelectorAll(
                '[data-date], [data-day], a[href*="day"], a[href*="date"], a.day-free, td.frei, .frei a, button.day-free'
            ));
            for (const el of candidates) {
                if (el.classList && el.classList.contains('disabled')) continue;
                if (el.disabled) continue;
                for (const v of [el.getAttribute('data-date'), el.getAttribute('data-day'),
                                  el.getAttribute('href'), el.getAttribute('title'),
                                  el.innerText]) {
                    if (!v) continue;
                    const d = norm(v);
                    if (d) { out.add(d); break; }
                }
            }
            return Array.from(out).sort();
        }"""
    )
    return dates


async def fetch_available_dates(cfg: ScraperCfg, anliegen: Anliegen) -> List[str]:
    """Walk steps 1-4 in a fresh browser session and return available ISO dates."""
    async with browser_session(cfg) as page:
        await page.goto(cfg.target_url, wait_until="domcontentloaded")
        await _dismiss_cookies(page)
        await _step1_pick_behoerde(page)
        if not await _step2_pick_anliegen(page, anliegen):
            return []
        if not await _step3_pick_standort(page):
            return []
        return await _step4_read_calendar(page)


async def fetch_all(cfg: ScraperCfg, anliegen_list: List[Anliegen]) -> dict[str, List[str]]:
    out: dict[str, List[str]] = {}
    for a in anliegen_list:
        try:
            dates = await fetch_available_dates(cfg, a)
            out[a.id] = dates
            log.info("anliegen=%s available_dates=%d", a.id, len(dates))
        except Exception:
            log.exception("scrape failed for anliegen=%s", a.id)
            out[a.id] = []
        await asyncio.sleep(2)
    return out
