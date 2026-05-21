"""
Auto-explore the booking site. No manual clicking needed.

Walks step 1 → 2 → 3 by best-guess heuristics and prints the DOM at each step,
so we can lock down real selectors for app/scraper.py.

Usage:
    python discover.py          # headless, prints JSON dumps to stdout
    python discover.py --show   # headed mode so you can watch

Output is also written to data/discover_dump.json for inspection.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import Page, async_playwright

from app.config import load_config

DUMP_PATH = Path(__file__).resolve().parent / "data" / "discover_dump.json"

JS_DOM_SUMMARY = """
() => {
  const summarize = (el) => {
    const attrs = {};
    for (const a of el.attributes) attrs[a.name] = a.value;
    return {
      tag: el.tagName.toLowerCase(),
      text: (el.innerText || '').trim().slice(0, 120),
      attrs,
    };
  };
  const out = { url: location.href, title: document.title };
  out.headings = Array.from(document.querySelectorAll('h1, h2, h3, legend'))
    .slice(0, 12).map(el => (el.innerText || '').trim()).filter(Boolean);
  out.selects = Array.from(document.querySelectorAll('select')).map(s => ({
    name: s.name, id: s.id,
    options: Array.from(s.options).map(o => ({value: o.value, text: o.text.trim()}))
  }));
  out.inputs = Array.from(document.querySelectorAll('input')).slice(0, 200).map(summarize);
  out.buttons = Array.from(document.querySelectorAll('button, input[type=submit]'))
    .slice(0, 20).map(summarize);
  out.links = Array.from(document.querySelectorAll('a'))
    .filter(a => (a.innerText || '').trim().length > 0)
    .slice(0, 40)
    .map(a => ({text: a.innerText.trim().slice(0, 80),
                href: a.getAttribute('href'),
                cls: a.className}));
  out.labels = Array.from(document.querySelectorAll('label'))
    .slice(0, 40)
    .map(l => (l.innerText || '').trim().slice(0, 120))
    .filter(Boolean);
  out.body_text_sample = document.body.innerText.slice(0, 2000);
  // candidate calendar cells: things with date-ish attrs/classes/href
  // CNC quantity inputs (the Anliegen counters), grouped by their section heading
  out.cnc_inputs = Array.from(document.querySelectorAll('input.cnc-number, input[type=number][name^="cnc-"]')).map(el => {
    let section = '';
    let walker = el;
    while (walker && walker !== document.body) {
      const h = walker.querySelector ? walker.querySelector(':scope > h2, :scope > h3, :scope > legend, :scope > .panel-heading') : null;
      if (h) { section = h.innerText.trim(); break; }
      walker = walker.parentElement;
    }
    // also try previous heading sibling
    if (!section) {
      let p = el.closest('.panel, fieldset, section, .anliegen-group, .row');
      if (p) {
        let prev = p.previousElementSibling;
        while (prev && !/^h[1-6]$|legend/i.test(prev.tagName)) prev = prev.previousElementSibling;
        if (prev) section = prev.innerText.trim();
      }
    }
    return {
      id: el.id, name: el.name,
      anliegen_aria: el.getAttribute('aria-label') || '',
      anliegen_title: el.getAttribute('title') || '',
      section,
      max: el.getAttribute('max'),
    };
  });
  out.candidate_calendar_cells = Array.from(
    document.querySelectorAll('[data-date], [data-day], a, td')
  ).filter(el => {
    const cls = el.className || '';
    const href = el.getAttribute('href') || '';
    const dd = el.getAttribute('data-date') || el.getAttribute('data-day') || '';
    return /frei|free|available|day|date|kalender/i.test(cls)
      || /\\d{4}-\\d{2}-\\d{2}/.test(href)
      || /\\d{4}-\\d{2}-\\d{2}/.test(dd)
      || /\\d{2}\\.\\d{2}\\.\\d{4}/.test(href);
  }).slice(0, 30).map(summarize);
  return out;
}
"""


async def dump_step(page: Page, label: str, all_dumps: dict) -> dict:
    data = await page.evaluate(JS_DOM_SUMMARY)
    all_dumps[label] = data
    print(f"\n{'='*70}\n  {label}  ({data['url']})\n{'='*70}")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    sys.stdout.flush()
    return data


async def dismiss_cookies(page: Page) -> None:
    """Click the cookie banner away if present."""
    for sel in ("#cookie_msg_btn_no", "#cookie_msg_btn_yes", "button:has-text('Ablehnen')"):
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=2000)
                print(f">>> dismissed cookies via {sel!r}")
                await page.wait_for_timeout(300)
                return
        except Exception:
            continue


async def try_pick_behoerde(page: Page, step1: dict) -> bool:
    """Click the Behörde button (this site uses buttons, not a select)."""
    # First try the exact button class we saw in step 1
    candidates = [
        "button.select_mdt_btn",
        "button[id^='buttonfunktionseinheit']",
        "button:has-text('Ausländer')",
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            if await loc.count() == 0:
                continue
            txt = (await loc.inner_text()).strip()
            print(f"\n>>> clicking Behörde button via {sel!r}: {txt!r}")
            await loc.click(timeout=5000)
            await page.wait_for_load_state("domcontentloaded")
            return True
        except Exception as e:
            print(f">>> {sel!r} failed: {e}")
    # Fallback: any <select>
    for sel in step1.get("selects", []):
        if not sel["options"]:
            continue
        for opt in sel["options"]:
            if opt["value"] and re.search(r"ausländ", opt["text"], re.IGNORECASE):
                print(f"\n>>> falling back to <select>: {opt['text']!r}")
                locator = page.locator(f"select[name={sel['name']!r}]").first
                await locator.select_option(value=opt["value"])
                submit = page.locator('button[type="submit"], input[type="submit"]').first
                await submit.click(timeout=5000)
                await page.wait_for_load_state("domcontentloaded")
                return True
    return False


async def try_pick_standort(page: Page) -> bool:
    """On step 3 there's a 'Standort auswählen' button per location, then a #WeiterButton.
    For RWTH Studenten there's only one location, so pick the first button."""
    # The location button is technically in the DOM but hidden (the site uses
    # collapsed location cards). JS-click bypasses the visibility check.
    clicked = await page.evaluate(
        """() => {
            const btn = document.querySelector('.map_loc_btn, input[name="select_location"]');
            if (!btn) return null;
            btn.click();
            return {name: btn.name, value: btn.value};
        }"""
    )
    if not clicked:
        print(">>> no Standort button in DOM")
        return False
    print(f">>> JS-clicked location (submits form): {clicked}")
    # `select_location` is type=submit, so clicking it submits and navigates.
    try:
        await page.wait_for_url(re.compile(r"select_location|kalender|calendar|appointment|date"), timeout=8000)
    except Exception:
        # fall back to a generic load wait
        await page.wait_for_load_state("networkidle", timeout=8000)
    await page.wait_for_timeout(2500)
    return True


async def try_set_anliegen_qty(page: Page, prefer_pattern: str = r"RWTH Studenten") -> bool:
    """Find the cnc-number input matching prefer_pattern; set value=1 via JS (the
    accordion panel may be collapsed); then click Weiter."""
    target = await page.evaluate(
        """(pattern) => {
            const re = new RegExp(pattern, 'i');
            const inputs = Array.from(document.querySelectorAll('input.cnc-number, input[type=number][name^="cnc-"]'));
            for (const el of inputs) {
                const aria = el.getAttribute('aria-label') || '';
                if (re.test(aria)) {
                    el.value = '1';
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return {id: el.id, name: el.name, aria};
                }
            }
            return null;
        }""",
        prefer_pattern,
    )
    if target is None:
        print(f">>> no cnc input matches pattern {prefer_pattern!r}")
        return False
    print(f">>> picked Anliegen via JS: id={target['id']!r} — {target['aria']!r}")

    # Click Weiter — the form-submit input on this site is #WeiterButton.
    weiter = page.locator("#WeiterButton").first
    if await weiter.count() == 0:
        print(">>> no #WeiterButton found")
        return False
    print(">>> clicking #WeiterButton")
    await weiter.click(timeout=5000)
    await page.wait_for_timeout(1200)  # let the info modal animate in
    # If an info/confirm modal is visible, click its OK button.
    ok = page.locator(".btn-ok").first
    if await ok.count() > 0 and await ok.is_visible():
        print(">>> info modal showed — clicking .btn-ok")
        await ok.click(timeout=5000)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2000)  # give the calendar JS time to render
    return True


async def main() -> None:
    cfg = load_config()
    show = "--show" in sys.argv
    all_dumps: dict = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not show)
        ctx = await browser.new_context(
            user_agent=cfg.scraper.user_agent,
            locale="de-DE",
            timezone_id="Europe/Berlin",
        )
        page = await ctx.new_page()
        page.set_default_timeout(15_000)

        print(f">>> opening {cfg.scraper.target_url}")
        await page.goto(cfg.scraper.target_url, wait_until="domcontentloaded")
        await dismiss_cookies(page)
        step1 = await dump_step(page, "STEP 1 — landing", all_dumps)

        ok1 = await try_pick_behoerde(page, step1)
        if not ok1:
            print("\n!!! Could not auto-pick a Behörde. Examine STEP 1 dump above.")
        else:
            await dump_step(page, "STEP 2 — Anliegen list", all_dumps)
            ok2 = await try_set_anliegen_qty(page)
            if not ok2:
                print("\n!!! Could not auto-pick an Anliegen. Examine STEP 2 dump above.")
            else:
                await dump_step(page, "STEP 3 — Standortauswahl", all_dumps)
                ok3 = await try_pick_standort(page)
                if not ok3:
                    print("\n!!! Could not auto-pick a Standort. Examine STEP 3 dump above.")
                else:
                    await dump_step(page, "STEP 4 — Terminauswahl (calendar)", all_dumps)

        DUMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        DUMP_PATH.write_text(json.dumps(all_dumps, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n>>> full dump saved to {DUMP_PATH}")
        await ctx.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
