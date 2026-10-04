"""Phase 14: every route of the REAL Vercel preview, by direct navigation and by refresh.

    python vercel_routes.py https://<preview>.vercel.app https://api.example.in /work/out14

Per route and per mode (direct, refresh):
  * the document response is 200 and is the app (index.html), never a server 404;
  * the page's API calls go to the real backend and all return 2xx (except the
    deliberate unknown-record visit);
  * no failure state (data-testid=load-failed) and no page errors;
  * the page shows REAL data: a figure from expected.json (computed from the
    database base tables, never from the API) appears on data pages.
Plus: query strings survive refresh; the House selector after refresh (reported
as observed, not changed); an unknown path; the copilot answers.
"""

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE, API, OUT = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
EXP = json.loads((Path(__file__).parent / "expected.json").read_text("utf-8"))
results = []

ROUTES = [
    ("landing", "/", None),
    ("login", "/login", None),
    ("dashboard", "/dashboard", "total"),
    ("map", "/map", None),
    ("queue", "/queue", "total"),
    ("record", f"/record/{EXP['record']}", "record"),
    ("analytics", "/analytics", None),
    ("mp-performance", "/mp-performance", None),
    ("data-health", "/data-health", None),
    ("methodology", "/methodology", None),
    ("queue+query", f"/queue?search={EXP['record']}", "record_key"),
    ("mp+query", "/mp-performance?mp=ASHISH%20DUBEY", "mp"),
]


def check(name, ok, detail=""):
    results.append({"check": name, "pass": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" | {str(detail)[:200]}"))


def visit(page, path, mode):
    calls, errs = [], []
    page.on("response", lambda r: calls.append((r.url, r.status)) if r.url.startswith(API) else None)
    page.on("pageerror", lambda e: errs.append(str(e)[:200]))
    if mode == "direct":
        resp = page.goto(BASE + path, wait_until="networkidle", timeout=120000)
    else:
        page.goto(BASE + path, wait_until="networkidle", timeout=120000)
        calls.clear()
        resp = page.reload(wait_until="networkidle", timeout=120000)
    page.wait_for_timeout(2500)
    return resp, calls, errs


def data_marker(kind, text):
    if kind == "total":
        return f"{EXP['total_scored']:,}" in text
    if kind == "record":
        return str(round(EXP["record_risk"])) + "/100" in text
    if kind == "record_key":
        return EXP["record"] in text
    if kind == "mp":
        return f"{EXP['mp_scored_works']:,} scored works" in text
    return True


with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, path, kind in ROUTES:
        for mode in ("direct", "refresh"):
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            resp, calls, errs = visit(page, path, mode)
            html_ok = resp is not None and resp.status == 200 and "<div id=\"root\"" in (resp.text() or "")
            text = page.locator("body").inner_text()
            bad_api = [(u, s) for u, s in calls if s >= 400]
            failed = page.get_by_test_id("load-failed").count()
            needs_api = name not in ("landing", "login", "methodology")
            check(f"{name} [{mode}] document 200 + app shell", html_ok, resp.status if resp else None)
            check(f"{name} [{mode}] real API calls all OK", (bool(calls) or not needs_api) and not bad_api,
                  bad_api or "no API calls")
            check(f"{name} [{mode}] no failure state, no page errors", failed == 0 and not errs, (failed, errs))
            check(f"{name} [{mode}] shows real data", data_marker(kind, text), text[:200])
            page.screenshot(path=str(OUT / f"{name.replace('+', '_')}_{mode}.png"))
            ctx.close()

    # query string survives refresh
    ctx = browser.new_context(); page = ctx.new_page()
    page.goto(BASE + f"/queue?search={EXP['record']}", wait_until="networkidle"); page.reload(wait_until="networkidle")
    check("query string survives refresh", f"search={EXP['record']}" in page.url, page.url)
    ctx.close()

    # House selector after refresh (observed behaviour, not changed)
    ctx = browser.new_context(); page = ctx.new_page()
    calls = []
    page.on("request", lambda r: calls.append(r.url) if r.url.startswith(API) else None)
    page.goto(BASE + "/dashboard", wait_until="networkidle")
    page.locator('.shell-topbar .house-toggle-btn[data-house="RS"]').click()
    page.wait_for_load_state("networkidle")
    calls.clear()
    page.reload(wait_until="networkidle"); page.wait_for_timeout(2000)
    pressed = page.locator('.shell-topbar .house-toggle-btn[data-house="RS"]').get_attribute("aria-pressed")
    rs_calls = [u for u in calls if "house=RS" in u]
    print(f"OBSERVED house selector after refresh: RS pressed={pressed}, house=RS calls after reload={len(rs_calls)}")
    results.append({"check": "house selector after refresh (observed)", "pass": True,
                    "detail": f"RS pressed={pressed}; house=RS calls={len(rs_calls)}"})
    text = page.locator("body").inner_text()
    check("house RS after refresh shows the RS total", f"{EXP['rs_scored']:,}" in text, text[:200])
    ctx.close()

    # unknown path: served by Vercel as the app (never a server 404); the app has no not-found page
    ctx = browser.new_context(); page = ctx.new_page()
    resp = page.goto(BASE + "/no-such-page", wait_until="networkidle")
    page.wait_for_timeout(1500)
    shell = page.locator(".shell-topbar").count()
    check("unknown path: document 200 (no server 404) and the app renders", resp.status == 200 and shell == 1,
          (resp.status, shell))
    print("OBSERVED unknown path content:", repr(page.locator("main").inner_text()[:120]))
    page.screenshot(path=str(OUT / "unknown_path.png"))
    ctx.close()

    # copilot answers on a real page
    ctx = browser.new_context(); page = ctx.new_page()
    page.goto(BASE + "/dashboard", wait_until="networkidle")
    page.locator(".chatbot-fab").click()
    replies = page.locator(".chatbot-bubble.assistant:not(.chatbot-typing)")
    before = replies.count()
    box = page.locator(".chatbot-input-row input, .chatbot-input-row textarea").first
    box.fill("How are works prioritised?")
    box.press("Enter")
    for _ in range(60):  # up to 30 s for the reply
        if replies.count() > before and len(replies.last.inner_text().strip()) > 40:
            break
        page.wait_for_timeout(500)
    bubbles = replies.all_inner_texts()
    check("copilot replies through the real API", len(bubbles) >= 1 and len(bubbles[-1]) > 40, bubbles[-1:] )
    page.screenshot(path=str(OUT / "copilot.png"))
    ctx.close()
    browser.close()

(OUT / "vercel_routes.json").write_text(json.dumps(results, indent=2))
failed = [r for r in results if not r["pass"]]
print(f"vercel route checks: {len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
