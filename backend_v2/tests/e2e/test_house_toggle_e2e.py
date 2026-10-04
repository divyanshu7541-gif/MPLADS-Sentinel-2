"""
Phase 3 browser test for the frontend House toggle, against the real-data API.

Runs inside the ordinary full pytest run: tests/e2e/conftest.py starts the
API and the Vite dev server (or uses E2E_BASE_URL, the way
scripts/e2e_house_toggle.sh runs it). With E2E_REQUIRED=1 a missing
prerequisite is a failure, never a skip. Screenshots go to
E2E_SCREENSHOT_DIR when set.

It checks the toggle's states, which requests carry `house=`, and the
explicit Rajya Sabha not-applicable notices.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

HOUSE_ENDPOINTS = {"summary", "queue", "analytics", "map-data", "map-works", "map-filters", "graph-data"}
SHOTS = Path(os.environ["E2E_SCREENSHOT_DIR"]) if os.environ.get("E2E_SCREENSHOT_DIR") else None


@pytest.fixture
def page(playwright_api):
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.api_calls = []
        pg.on("request", lambda r: pg.api_calls.append(r.url) if "/api/" in r.url else None)
        yield pg
        browser.close()


def _calls(page, since=0):
    out = []
    for url in page.api_calls[since:]:
        u = urlparse(url)
        out.append((u.path.removeprefix("/api/"), parse_qs(u.query).get("house", [None])[0]))
    return out


def _shot(page, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"))


def _btn(page, code):
    return page.locator(f'.shell-topbar .house-toggle-btn[data-house="{code}"]')


def test_toggle_states_and_house_param_on_exactly_the_seven_calls(page, base):
    page.goto(f"{base}/dashboard")
    page.wait_for_load_state("networkidle")
    # default: both unselected, and no request carries house
    assert page.locator(".house-toggle").count() == 1, "toggle must be mounted once, in the shared topbar"
    assert _btn(page, "LS").get_attribute("aria-pressed") == "false"
    assert _btn(page, "RS").get_attribute("aria-pressed") == "false"
    assert _btn(page, "LS").inner_text().strip() == "LOK SABHA"
    assert _btn(page, "RS").inner_text().strip() == "RAJYA SABHA"
    assert all(h is None for _, h in _calls(page)), _calls(page)
    assert ("summary", None) in _calls(page)
    _shot(page, "01_dashboard_none_selected")

    # select LS -> the page re-fetches with house=LS
    n = len(page.api_calls)
    _btn(page, "LS").click()
    page.wait_for_load_state("networkidle")
    assert _btn(page, "LS").get_attribute("aria-pressed") == "true"
    assert _btn(page, "RS").get_attribute("aria-pressed") == "false"
    new = _calls(page, n)
    assert ("summary", "LS") in new, new
    for path, h in new:
        assert (h == "LS") == (path in HOUSE_ENDPOINTS), (path, h)
    _shot(page, "02_dashboard_ls_selected")

    # click LS again -> cleared, back to unfiltered
    n = len(page.api_calls)
    _btn(page, "LS").click()
    page.wait_for_load_state("networkidle")
    assert _btn(page, "LS").get_attribute("aria-pressed") == "false"
    assert ("summary", None) in _calls(page, n)

    # switching straight from RS to LS replaces the value
    _btn(page, "RS").click()
    page.wait_for_load_state("networkidle")
    n = len(page.api_calls)
    _btn(page, "LS").click()
    page.wait_for_load_state("networkidle")
    assert _btn(page, "RS").get_attribute("aria-pressed") == "false"
    assert ("summary", "LS") in _calls(page, n)


def test_selection_persists_across_pages_and_other_pages_send_it(page, base):
    page.goto(f"{base}/dashboard")
    _btn(page, "RS").click()
    page.wait_for_load_state("networkidle")
    for path, endpoint in (("/queue", "queue"), ("/analytics", "analytics")):
        n = len(page.api_calls)
        page.goto(f"{base}{path}")
        page.wait_for_load_state("networkidle")
        assert _btn(page, "RS").get_attribute("aria-pressed") == "true"
        calls = _calls(page, n)
        assert (endpoint, "RS") in calls, calls
        for p, h in calls:
            assert (h == "RS") == (p in HOUSE_ENDPOINTS), (p, h)


def test_rajya_sabha_shows_not_applicable_on_map_drilldown(page, base):
    page.goto(f"{base}/map")
    page.wait_for_load_state("networkidle")
    notice = page.get_by_test_id("house-not-applicable")
    assert notice.count() == 0
    _btn(page, "RS").click()
    page.wait_for_load_state("networkidle")
    assert notice.count() == 1 and notice.is_visible()
    assert "not applicable for Rajya Sabha" in notice.inner_text()
    assert ("map-data", "RS") in _calls(page) and ("map-filters", "RS") in _calls(page)
    # the drill-down action (what a popup's "Explore" button calls) is blocked, not silently empty
    n = len(page.api_calls)
    page.evaluate("window.__mapSelectConstituency('Test State', 'Test Constituency')")
    page.wait_for_timeout(500)
    assert not any(p in ("constituency-intelligence", "map-works") for p, _ in _calls(page, n))
    assert notice.is_visible()
    _shot(page, "03_map_rs_not_applicable")

    _btn(page, "LS").click()
    page.wait_for_load_state("networkidle")
    assert page.get_by_test_id("house-not-applicable").count() == 0


def test_rajya_sabha_shows_not_applicable_on_constituency_performance(page, base):
    page.goto(f"{base}/mp-performance")
    page.wait_for_load_state("networkidle")
    assert page.get_by_test_id("house-not-applicable").count() == 0
    _btn(page, "RS").click()
    page.wait_for_load_state("networkidle")
    notices = page.get_by_test_id("house-not-applicable")
    # The page remounts on a House switch and reloads its MP/constituency
    # lists; with real data (Phase 12) that outlasts the networkidle check,
    # so wait for the notice itself rather than counting immediately.
    notices.first.wait_for(timeout=15000)
    assert notices.count() == 1
    assert "Constituency performance is not applicable for Rajya Sabha" in notices.first.inner_text()
    _shot(page, "04_mp_performance_rs_browse")

    # compare -> Constituencies shows the notice instead of the selector
    page.locator(".mpp-section .btn", has_text="Start").first.click()
    page.locator(".mpp-section-body .btn", has_text="Constituencies").click()
    assert page.get_by_text("Constituency comparison is not applicable for Rajya Sabha").is_visible()
    _shot(page, "05_mp_performance_rs_compare")

    # a ?constituency= deep link under RS doesn't fetch, and says why
    n = len(page.api_calls)
    page.goto(f"{base}/mp-performance?constituency=Test%20Constituency")
    page.wait_for_load_state("networkidle")
    assert not any(p.startswith("constituency-performance") for p, _ in _calls(page, n))
    # same list-reload race as above
    page.get_by_test_id("house-not-applicable").nth(1).wait_for(timeout=15000)
    assert page.get_by_test_id("house-not-applicable").count() == 2
