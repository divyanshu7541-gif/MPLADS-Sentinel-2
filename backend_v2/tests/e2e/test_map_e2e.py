"""
Browser tests for the Map page (2026-10-02 map improvements), against the
real-data API. Same stack as test_house_toggle_e2e.py (tests/e2e/conftest.py
starts it, or E2E_BASE_URL points at a running one).

Written, not yet run (DEPLOY_NOTES.md).

  * The works list opens only from the panel button (final spec item 5:
    a cluster bubble zooms or spiderfies, as before 49f4bfd, capped at 1,000
    markers), is built from the works already loaded (no new /api/
    request), is sorted by tier then amount, and shows 25 rows per page.
  * Areas without a value are never risk-coloured: "No works recorded"
    (dark slate, dashed) and "Fewer than 10 works" (lighter slate, dotted),
    both in the legend, with hover text; seats without a boundary are listed
    from /api/geographic-coverage.
  * Rajya Sabha: map-data is asked for state-level rows (level=state), every
    polygon takes its state's figures, the "state-level" note is shown, and
    the count and exposure metrics are disabled ("not comparable").
  * Jammu and Kashmir / Ladakh depiction (2026-10-03, display only): each
    re-delimited state (Assam, Jammu and Kashmir; final spec item 6) is one
    state-level shape coloured from map-data level=state on the rate metrics
    and neutral on Count and Exposure, with its works' cluster inside it;
    the Survey of India-derived national outline is a thin line
    above the seat fills that never blocks a click; a failed outline request
    leaves the map working with a note.
  * No tile layer (2026-10-03, final spec): no tile is requested from any
    provider; the national outline is an opaque land fill under the Admin2
    state borders and the seats, and no display pane takes the pointer.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

TIER_ORDER = ["CRITICAL", "HIGH", "MODERATE", "LOW"]
PAGE = 25
# Map.jsx: the old near-white "No data" grey, and the two neutral status fills.
OLD_NO_DATA = "#b0b8c8"
STATUS_FILL = {"none": "#3f4a63", "few": "#8d97ad"}
RISK_FILLS = {"#a6291f", "#c45a20", "#93630c", "#1c6e46"}


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


def _paths(page, since=0):
    return [urlparse(u).path.removeprefix("/api/") for u in page.api_calls[since:]]


def _open_map(page, base):
    page.goto(f"{base}/map")
    page.wait_for_load_state("networkidle")


def _biggest_constituency(page):
    """The constituency with the most works in the unfiltered map data (so the
    list has more than one page)."""
    rows = page.evaluate("fetch('/api/map-data').then(r => r.json())")
    assert rows, "map-data is empty"
    top = max(rows, key=lambda r: r["total"])
    return top["State"], top["Constituency"], top["total"]


def _select(page, state, constituency):
    page.evaluate("([s, c]) => window.__mapSelectConstituency(s, c)", [state, constituency])
    page.wait_for_load_state("networkidle")
    page.get_by_test_id("map-works-button").wait_for(state="visible")
    page.wait_for_function(
        "() => !document.querySelector('[data-testid=map-works-button]').disabled", timeout=15000
    )


def _rank(tier):
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else len(TIER_ORDER)


def _assert_sorted(rows):
    keys = []
    for r in rows:
        amount = r.get_attribute("data-amount")
        keys.append((_rank(r.get_attribute("data-tier")), -(float(amount) if amount else float("-inf"))))
    assert keys == sorted(keys), keys


def test_works_button_opens_list_without_a_request(page, base):
    _open_map(page, base)
    state, constituency, total = _biggest_constituency(page)
    _select(page, state, constituency)

    n = len(page.api_calls)
    page.get_by_test_id("map-works-button").click()
    lst = page.get_by_test_id("map-works-list")
    lst.wait_for(state="visible")
    page.wait_for_timeout(500)
    assert _paths(page, n) == [], _paths(page, n)

    rows = lst.get_by_test_id("map-works-row")
    assert 0 < rows.count() <= PAGE
    _assert_sorted(rows.all())
    for r in rows.all():
        href = r.locator("a").get_attribute("href")
        assert href and href.startswith("/record/"), href

    more = page.get_by_test_id("map-works-more")
    if more.count():
        before = rows.count()
        more.click()
        assert before < rows.count() <= min(before + PAGE, total)
        _assert_sorted(rows.all())
        assert _paths(page, n) == [], _paths(page, n)


def _constituency_with(page, lo, hi):
    """A constituency whose works count lies in [lo, hi], or None."""
    rows = page.evaluate("fetch('/api/map-data').then(r => r.json())")
    fit = [r for r in rows if lo <= r["total"] <= hi]
    return (fit[0]["State"], fit[0]["Constituency"], fit[0]["total"]) if fit else None


def test_cluster_bubble_spiderfies_and_never_opens_the_list(page, base):
    """A bubble click spreads the bubble out (spiderfies: every work of a seat
    shares one point), as before 49f4bfd. The spread-out works are small tier
    dots, not "1" bubbles (no singleMarkerMode), and a dot opens only its
    own popup: no works list, no request, the selected seat unchanged."""
    _open_map(page, base)
    pick = _constituency_with(page, 2, 300)
    assert pick, "no constituency with 2-300 works"
    state, constituency, total = pick
    _select(page, state, constituency)
    bubble = page.locator(".leaflet-marker-cluster").first
    bubble.wait_for(state="visible")
    assert bubble.inner_text().strip() == str(total)  # tier colour and count kept
    assert page.locator(".leaflet-marker-cluster").count() == 1  # one shared point

    bubble.click()
    page.wait_for_timeout(1200)
    assert page.get_by_test_id("map-works-list").count() == 0
    assert page.locator(".leaflet-cluster-spider-leg").count() == total
    assert page.get_by_test_id("map-spider-capped").count() == 0
    dots = page.locator(".leaflet-marker-icon:not(.leaflet-marker-cluster)")
    assert dots.count() == total  # every spread-out work is a dot
    # a spread-out dot opens only its single-work popup
    n = len(page.api_calls)
    dots.first.click(force=True)
    page.locator(".leaflet-popup").wait_for(state="visible")
    page.wait_for_timeout(500)
    assert page.get_by_test_id("map-works-list").count() == 0
    assert _paths(page, n) == [], _paths(page, n)
    assert constituency in page.get_by_test_id("map-selected-chip").inner_text()


def _zoom_band(page):
    return page.get_by_test_id("map-canvas").get_attribute("data-zoom-band")


def test_bubble_clicked_far_out_flies_in_then_spiderfies(page, base):
    """Below zoom 8 a bubble click flies in to zoom 8 first, then spreads out
    the same works; at every zoom (also the maximum, 10) the shared point
    never splits by zooming, so the spiral is the way to see its works."""
    _open_map(page, base)
    pick = _constituency_with(page, 9, 300)
    assert pick, "no constituency with 9-300 works"
    state, constituency, total = pick
    _select(page, state, constituency)
    for _ in range(6):
        if _zoom_band(page) == "low":
            break
        page.locator(".leaflet-control-zoom-out").click()
        page.wait_for_timeout(400)
    assert _zoom_band(page) == "low"
    page.locator(".leaflet-marker-cluster").first.click()
    page.wait_for_timeout(2000)
    assert _zoom_band(page) == "high"  # zoom 7 or more (SPIDER_ZOOM is 8)
    assert page.locator(".leaflet-cluster-spider-leg").count() == total
    assert page.get_by_test_id("map-works-list").count() == 0

    # At the maximum zoom the bubble still spreads out.
    page.mouse.click(700, 20)  # fold the spiral
    for _ in range(4):
        page.locator(".leaflet-control-zoom-in").click()
        page.wait_for_timeout(400)
    page.locator(".leaflet-marker-cluster").first.click()
    page.wait_for_timeout(1200)
    assert page.locator(".leaflet-cluster-spider-leg").count() == total


def test_spiral_is_capped_at_1000_markers(page, base):
    _open_map(page, base)
    pick = _constituency_with(page, 1001, 3000)
    if pick is None:
        pytest.skip("no constituency with more than 1,000 works in this data")
    state, constituency, total = pick
    _select(page, state, constituency)
    page.locator(".leaflet-marker-cluster").first.click()
    page.wait_for_timeout(1500)
    assert page.locator(".leaflet-cluster-spider-leg").count() == 1000
    note = page.get_by_test_id("map-spider-capped")
    assert "1,000 highest-risk of" in note.inner_text() and f"{total:,}" in note.inner_text()
    page.mouse.click(5, 5)  # click the map away from the spiral: it folds back
    page.wait_for_timeout(800)
    assert page.locator(".leaflet-cluster-spider-leg").count() == 0
    assert page.get_by_test_id("map-spider-capped").count() == 0


def test_hiding_markers_keeps_works_for_the_list(page, base):
    _open_map(page, base)
    state, constituency, _ = _biggest_constituency(page)
    _select(page, state, constituency)
    page.get_by_title("Hide work markers").click()
    assert page.locator(".leaflet-marker-cluster").count() == 0

    n = len(page.api_calls)
    page.get_by_test_id("map-works-button").click()
    page.get_by_test_id("map-works-list").wait_for(state="visible")
    page.wait_for_timeout(500)
    assert _paths(page, n) == [], _paths(page, n)
    assert page.locator(".leaflet-marker-cluster").count() > 0


# The seats the Map draws: /api/geojson plus the 2019 seats with no portal
# seat (frontend/public/geo/unserved-seats.json, today Khadoor Sahib), each
# only when the API does not already serve its area_key (Map.jsx loadGeoJSON).
DRAWN_SEATS_JS = """async () => {
    const geo = await fetch('/api/geojson').then(r => r.json());
    const extra = await fetch('/geo/unserved-seats.json').then(r => r.ok ? r.json() : null, () => null);
    const served = new Set(geo.features.map(f => f.properties.area_key));
    const add = ((extra && extra.features) || []).filter(f => !served.has(f.properties.area_key));
    return [...geo.features, ...add];
}"""


def _drawn_seats(page):
    return page.evaluate(DRAWN_SEATS_JS)


def _areas(page):
    """(status, fill, dasharray) for every drawn constituency polygon."""
    return page.evaluate(
        """() => [...document.querySelectorAll('path.map-area')].map(p => [
            [...p.classList].find(c => c.startsWith('map-area-')).slice(9),
            (p.getAttribute('fill') || '').toLowerCase(),
            p.getAttribute('stroke-dasharray')])"""
    )


def test_areas_without_a_value_are_never_risk_coloured(page, base):
    _open_map(page, base)
    areas = _areas(page)
    assert len(areas) == len(_drawn_seats(page))
    assert all(fill != OLD_NO_DATA for _, fill, _ in areas)
    for status, fill, dash in areas:
        if status in STATUS_FILL:
            assert fill == STATUS_FILL[status] and dash, (status, fill, dash)
            assert fill not in RISK_FILLS
        else:
            assert status == "value" and fill in RISK_FILLS, (status, fill)

    legend = page.get_by_test_id("map-legend")
    assert legend.locator('[data-status="none"]').inner_text().strip() == "No works recorded"
    assert "Fewer than 10 works" in legend.locator('[data-status="few"]').inner_text()
    assert "No data" not in legend.inner_text()

    none = page.locator("path.map-area-none")
    if none.count():
        none.first.hover(force=True)
        tip = page.locator(".leaflet-tooltip")
        tip.wait_for(state="visible")
        assert "No works recorded" in tip.inner_text()


def test_filtered_view_says_no_works_match(page, base):
    _open_map(page, base)
    page.locator("select.toolbar-select").nth(1).select_option("CRITICAL")
    page.wait_for_load_state("networkidle")
    legend = page.get_by_test_id("map-legend")
    assert legend.locator('[data-status="none"]').inner_text().strip() == "No works match the current filters"
    assert all(fill != OLD_NO_DATA for _, fill, _ in _areas(page))


def test_seats_without_a_boundary_are_listed(page, base):
    _open_map(page, base)
    cov = page.evaluate("fetch('/api/geographic-coverage').then(r => r.json())")
    note = page.get_by_test_id("map-no-boundary")
    if not cov["no_boundary"]:
        assert note.count() == 0
        return
    assert f"{len(cov['no_boundary'])} seats have no boundary" in note.inner_text()
    note.locator("summary").click()
    text = note.inner_text()
    for a in cov["no_boundary"]:
        assert a["name"] in text, a


def test_rajya_sabha_map_is_coloured_by_state(page, base):
    _open_map(page, base)
    n = len(page.api_calls)
    page.locator('.shell-topbar .house-toggle-btn[data-house="RS"]').click()
    page.wait_for_load_state("networkidle")

    queries = [
        parse_qs(urlparse(u).query) for u in page.api_calls[n:] if urlparse(u).path.endswith("/map-data")
    ]
    assert queries, "map-data was not requested"
    assert all(q.get("house") == ["RS"] and q.get("level") == ["state"] for q in queries), queries

    note = page.get_by_test_id("map-rs-note")
    assert note.is_visible()
    assert "Rajya Sabha figures are state-level" in note.inner_text()
    assert "where the works are located" in note.inner_text()
    assert page.get_by_test_id("house-not-applicable").is_visible()

    for key in ("flagged_count", "financial_exposure"):
        btn = page.locator(f'button[data-metric="{key}"]')
        assert btn.is_disabled() and "Not comparable at state level" in btn.inner_text()
    for key in ("priority_rate", "average_risk"):
        assert page.locator(f'button[data-metric="{key}"]').is_enabled()

    rows = page.evaluate("fetch('/api/map-data?house=RS&level=state').then(r => r.json())")
    areas = _areas(page)
    assert all(fill != OLD_NO_DATA for _, fill, _ in areas)
    if rows:
        assert page.get_by_test_id("map-rs-unavailable").count() == 0
        assert any(status == "value" for status, _, _ in areas)
        page.locator("path.map-area-value").first.hover(force=True)
        tip = page.locator(".leaflet-tooltip")
        tip.wait_for(state="visible")
        assert "state-level figures" in tip.inner_text()
    else:
        assert page.get_by_test_id("map-rs-unavailable").is_visible()
        assert all(status != "value" for status, _, _ in areas)


# ---- Jammu and Kashmir / Ladakh depiction (2026-10-03; not yet run) ---------------------------

REDELIMITED_FILL = "#dfe2ea"


def _outline_paths(page, kind):
    return page.evaluate(
        f"""() => [...document.querySelectorAll('path.map-outline-{kind}')].map(p => [
            (p.getAttribute('fill') || '').toLowerCase(), p.getAttribute('stroke-dasharray'),
            p.closest('.leaflet-pane').className])"""
    )


def _band(value):
    """Map.jsx getColorForValue for priority_rate."""
    return "#a6291f" if value >= 15 else "#c45a20" if value >= 10 else "#93630c" if value >= 5 else "#1c6e46"


def _state_areas(page):
    """(status, fill, dasharray) of each re-delimited state shape."""
    return page.evaluate(
        """() => [...document.querySelectorAll('path.map-state-area')].map(p => [
            [...p.classList].find(c => c.startsWith('map-state-area-')).slice(15),
            (p.getAttribute('fill') || '').toLowerCase(), p.getAttribute('stroke-dasharray')])"""
    )


def test_redelimited_states_are_coloured_by_their_state_level_rows(page, base):
    """Final spec item 6: Assam and Jammu and Kashmir take the state-level
    figure on the rate metrics, and stay neutral on Count and Exposure."""
    _open_map(page, base)
    rows = page.evaluate("fetch('/api/map-data?house=LS&level=state').then(r => r.json())")
    by_state = {r["State"]: r for r in rows}
    areas = _state_areas(page)
    assert len(areas) == 2, areas
    values = [by_state[s]["flag_rate"] for s in ("Assam", "Jammu And Kashmir")]
    assert all(dash for _, _, dash in areas), areas  # the state-level long dash, in every view
    fills = sorted(fill for status, fill, _ in areas if status == "value")
    assert fills == sorted(_band(v) for v in values if v is not None)
    for key in ("flagged_count", "financial_exposure"):
        page.locator(f'button[data-metric="{key}"]').click()
        page.wait_for_timeout(300)
        for status, fill, _ in _state_areas(page):
            assert status == "notComparable" and fill == REDELIMITED_FILL and fill not in RISK_FILLS
        page.locator("path.map-state-area").first.hover(force=True)
        tip = page.locator(".leaflet-tooltip")
        tip.wait_for(state="visible")
        assert "Not comparable at state level" in tip.inner_text()
        page.mouse.move(5, 5)


def test_state_level_cluster_sits_inside_its_state(page, base):
    _open_map(page, base)
    n = len(page.api_calls)
    page.evaluate("() => window.__mapSelectState('Assam')")
    page.wait_for_load_state("networkidle")
    works = [
        parse_qs(urlparse(u).query) for u in page.api_calls[n:] if urlparse(u).path.endswith("/map-works")
    ]
    assert works and works[-1].get("redelimited") == ["true"] and works[-1].get("state") == ["Assam"]
    card = page.get_by_test_id("map-state-level-card")
    card.wait_for(state="visible")
    assert "Constituency boundaries are not available for this state (re-delimited)." in card.inner_text()
    bubble = page.locator(".leaflet-marker-cluster").first
    bubble.wait_for(state="visible")
    inside = page.evaluate(
        """() => {
            const b = document.querySelector('.leaflet-marker-cluster').getBoundingClientRect();
            const c = new DOMPoint(b.x + b.width / 2, b.y + b.height / 2);
            return [...document.querySelectorAll('path.map-state-area')]
                .some(p => p.isPointInFill(c.matrixTransform(p.getScreenCTM().inverse())));
        }"""
    )
    assert inside, "the Assam cluster is not inside a state-level shape"
    # the works list still opens only from the panel button
    assert page.get_by_test_id("map-works-list").count() == 0
    card.get_by_test_id("map-works-button").click()
    page.get_by_test_id("map-works-list").wait_for(state="visible")


STATIC_OUTLINES = "**/geo/india-outlines.json*"


def test_failed_outlines_leave_the_map_working_with_a_note(page, base):
    # Map problem 4: the static file is tried first, then the API; both fail.
    page.route(STATIC_OUTLINES, lambda route: route.fulfill(status=404, body="not found"))
    page.route("**/api/boundary-outlines", lambda route: route.fulfill(status=503, body="{}"))
    _open_map(page, base)
    note = page.get_by_test_id("map-outlines-failed")
    note.wait_for(state="visible")
    failed = "Boundary outlines could not be loaded; the map is shown without them."
    assert note.inner_text().strip() == failed
    assert page.locator("path.map-area").count() > 0  # seats still drawn
    assert page.locator("path.map-area-value").count() > 0  # and coloured
    for cls in ("map-outline-redelimited", "map-outline-national", "map-outline-state", "map-land"):
        assert page.locator(f"path.{cls}").count() == 0, cls
    # final spec item 9: seats and clusters still work
    state, constituency, _ = _biggest_constituency(page)
    _select(page, state, constituency)
    page.locator(".leaflet-marker-cluster").first.wait_for(state="visible")
    page.get_by_test_id("map-works-button").click()
    page.get_by_test_id("map-works-list").wait_for(state="visible")


def test_slow_outlines_are_given_up_after_20_seconds(page, base):
    """Final spec item 9: a request that never answers is abandoned at 20 s;
    until then, and after, the seats are drawn and usable."""
    page.route(STATIC_OUTLINES, lambda route: route.fulfill(status=404, body="not found"))
    page.route("**/api/boundary-outlines", lambda route: None)  # never answered
    _open_map(page, base)
    assert page.locator("path.map-area").count() > 0
    note = page.get_by_test_id("map-outlines-failed")
    page.wait_for_timeout(15_000)
    assert note.count() == 0  # still waiting at 15 s (the old limit was 6 s)
    note.wait_for(state="visible", timeout=10_000)
    assert page.locator("path.map-area-value").count() > 0


def _pane_z(page, names):
    return page.evaluate(
        """(names) => names.map(n => {
            const el = document.querySelector('.leaflet-' + n + 'Pane-pane, .leaflet-' + n + '-pane');
            return [Number(getComputedStyle(el).zIndex), getComputedStyle(el).pointerEvents];
        })""",
        names,
    )


def test_india_outline_is_a_line_above_seats(page, base):
    _open_map(page, base)
    paths = _outline_paths(page, "national")
    assert len(paths) == 1, paths
    fill, _, pane = paths[0]
    assert fill == "none" and "leaflet-indiaOutlinePane-pane" in pane
    z = _pane_z(page, ["overlay", "indiaOutline", "marker"])
    (overlay, _), (india, india_pe), (marker, _) = z
    assert overlay < india < marker and india_pe == "none", z
    # the line never takes the pointer: points along it hit the seat (or tile) underneath
    hits = page.evaluate(
        """() => {
            const p = document.querySelector('path.map-outline-national');
            const m = p.getScreenCTM(), len = p.getTotalLength(), out = [];
            for (let i = 1; i < 60; i++) {
                const pt = p.getPointAtLength(len * i / 60).matrixTransform(m);
                const hit = document.elementFromPoint(pt.x, pt.y);
                if (hit) out.push(hit.getAttribute('class') || hit.tagName);
            }
            return out;
        }"""
    )
    assert hits and not any("map-outline-national" in h for h in hits), hits
    assert any("map-area" in h for h in hits), hits
    legend = page.get_by_test_id("map-legend")
    assert "India boundary" in legend.locator('[data-status="national"]').inner_text()


# ---- no tile layer (2026-10-03 final spec; not yet run) ---------------------------------------

LAND_FILL = "#e3e7ee"


def test_no_tile_layer_and_no_tile_request(page, base):
    tile_requests = []
    page.on("request", lambda r: tile_requests.append(r.url) if "tile" in r.url else None)
    _open_map(page, base)
    assert page.locator(".leaflet-tile").count() == 0
    assert tile_requests == [], tile_requests
    assert not any("openstreetmap" in u for u in page.api_calls + tile_requests)
    attribution = page.locator(".leaflet-control-attribution").inner_text()
    assert "OpenStreetMap" not in attribution


def test_land_fill_borders_seats_and_markers_are_layered_in_order(page, base):
    _open_map(page, base)
    land = page.evaluate(
        """() => [...document.querySelectorAll('path.map-land')].map(p => [
            (p.getAttribute('fill') || '').toLowerCase(), p.getAttribute('fill-opacity'),
            p.getAttribute('stroke'), p.closest('.leaflet-pane').className])"""
    )
    assert len(land) == 1, land
    fill, opacity, stroke, pane = land[0]
    assert fill == LAND_FILL and float(opacity) == 1 and stroke == "none" and "indiaLandPane" in pane
    assert page.locator("path.map-outline-state").count() >= 30  # one per Admin2 state/UT
    z = _pane_z(page, ["indiaLand", "stateBorder", "overlay", "indiaOutline", "marker"])
    zs = [v for v, _ in z]
    assert zs == sorted(zs) and len(set(zs)) == len(zs), z
    assert [pe for _, pe in z][:2] == ["none", "none"] and z[3][1] == "none", z


def test_display_panes_never_block_a_seat_click(page, base):
    _open_map(page, base)
    # the centre of a few seat polygons: the topmost element there is the seat, not a display pane
    hits = page.evaluate(
        """() => [...document.querySelectorAll('path.map-area')].slice(0, 40).map(p => {
            const b = p.getBoundingClientRect();
            const el = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
            return el ? (el.getAttribute('class') || el.tagName) : '';
        })"""
    )
    assert hits
    display = ("map-land", "map-outline map-outline-state", "map-outline map-outline-national")
    assert not any(c.startswith(display) for c in hits), hits


# ---- zoom and pan limits (2026-10-03 final spec, item 2; not yet run) --------------------------

MIN_ZOOM, MAX_ZOOM = 4, 10


def _zoom(page):
    """Leaflet 1.9 scales its .leaflet-proxy element by 2^zoom (zoom animation on)."""
    return page.evaluate(
        """() => {
            const t = getComputedStyle(document.querySelector('.leaflet-proxy')).transform;
            return Math.round(Math.log2(new DOMMatrix(t).a));
        }"""
    )


def _press(page, button, times):
    for _ in range(times):
        if "leaflet-disabled" in (button.get_attribute("class") or ""):
            break
        button.click()
        page.wait_for_timeout(350)


def test_zoom_in_and_out_work_within_the_limits(page, base):
    _open_map(page, base)
    zin, zout = page.locator(".leaflet-control-zoom-in"), page.locator(".leaflet-control-zoom-out")
    start = _zoom(page)
    _press(page, zin, 1)
    assert _zoom(page) == start + 1
    _press(page, zout, 1)
    assert _zoom(page) == start
    _press(page, zin, MAX_ZOOM + 2)
    assert _zoom(page) == MAX_ZOOM and "leaflet-disabled" in zin.get_attribute("class")
    _press(page, zout, MAX_ZOOM + 2)
    assert _zoom(page) == MIN_ZOOM and "leaflet-disabled" in zout.get_attribute("class")


def test_panning_cannot_leave_india(page, base):
    _open_map(page, base)
    canvas = page.get_by_test_id("map-canvas").bounding_box()
    x, y = canvas["x"] + canvas["width"] * 0.6, canvas["y"] + canvas["height"] * 0.5
    for dx, dy in ((-3000, 0), (3000, 0), (0, 3000), (0, -3000)):
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + dx, y + dy, steps=12)
        page.mouse.up()
        page.wait_for_timeout(600)
        land = page.locator("path.map-land").bounding_box()
        # India's land fill still overlaps the visible map: the view never drifts into empty space
        right, bottom = canvas["x"] + canvas["width"], canvas["y"] + canvas["height"]
        overlap_w = min(land["x"] + land["width"], right) - max(land["x"], canvas["x"])
        overlap_h = min(land["y"] + land["height"], bottom) - max(land["y"], canvas["y"])
        assert overlap_w > 0 and overlap_h > 0, (dx, dy, land, canvas)


# ---- Kashmir and Ladakh (2026-10-03 final spec, item 3; not yet run) ---------------------------


def test_ladakh_seat_and_jk_shape_are_both_drawn_and_clickable(page, base):
    _open_map(page, base)
    geo = page.evaluate("fetch('/api/geojson').then(r => r.json())")
    ladakh = [f for f in geo["features"] if f["properties"]["dataset_state"] == "Ladakh"]
    assert len(ladakh) == 1 and ladakh[0]["properties"].get("clipped_to_state") == "Ladakh"
    assert _outline_paths(page, "redelimited"), "the Jammu and Kashmir shape is not drawn"
    # the J&K shape takes the pointer (tooltip), and no display pane sits over it
    page.locator("path.map-outline-redelimited").first.hover(force=True)
    page.locator(".leaflet-tooltip").wait_for(state="visible")


# ---- every metric and filter colours every area with data (final spec, item 4; not yet run) ---

METRIC_FIELD = {
    "priority_rate": "flag_rate",
    "flagged_count": "flagged",
    "average_risk": "avg_risk_pct",
    "financial_exposure": "financial_exposure",
}


def _expected_statuses(page, metric, query):
    """Status per seat polygon as Map.jsx should draw it, from the API itself."""
    return page.evaluate(
        """async ([field, query]) => {
            const [rows, seats] = await Promise.all([
                fetch('/api/map-data' + query).then(r => r.json()),
                (""" + DRAWN_SEATS_JS + """)(),
            ]);
            const byKey = new Map(rows.map(r => [r.State + '|' + r.Constituency, r]));
            const out = { value: 0, few: 0, none: 0 };
            for (const f of seats) {
                const p = f.properties, r = byKey.get(p.dataset_state + '|' + p.dataset_constituency);
                if (!r) out.none += 1;
                else if (r[field] == null) out.few += 1;
                else out.value += 1;
            }
            return out;
        }""",
        [METRIC_FIELD[metric], query],
    )


@pytest.mark.parametrize("metric", list(METRIC_FIELD))
@pytest.mark.parametrize("flt", ["none", "risk", "state"])
def test_every_metric_and_filter_colours_every_area_with_data(page, base, metric, flt):
    _open_map(page, base)
    query = ""
    if flt == "risk":
        page.locator("select.toolbar-select").nth(1).select_option("CRITICAL")
        query = "?priority=CRITICAL"
    elif flt == "state":
        sel = page.locator("select.toolbar-select").nth(0)
        state = sel.locator("option").nth(1).get_attribute("value")
        sel.select_option(state)
        query = "?state=" + state.replace(" ", "%20")
    page.wait_for_load_state("networkidle")
    page.locator(f'button[data-metric="{metric}"]').click()
    page.wait_for_timeout(300)

    expected = _expected_statuses(page, metric, query)
    areas = _areas(page)
    got = {"value": 0, "few": 0, "none": 0}
    for status, fill, _ in areas:
        got[status] += 1
        if status == "value":
            assert fill in RISK_FILLS, (metric, flt, fill)
        else:  # never a risk colour on an area without a value or without works
            assert fill not in RISK_FILLS and fill == STATUS_FILL[status], (metric, flt, status, fill)
    assert got == expected, (metric, flt, got, expected)


def test_rajya_sabha_falls_back_to_a_rate_metric(page, base):
    _open_map(page, base)
    page.locator('button[data-metric="flagged_count"]').click()
    page.locator('.shell-topbar .house-toggle-btn[data-house="RS"]').click()
    page.wait_for_load_state("networkidle")
    selected = page.locator('button[data-metric][aria-pressed="true"]')
    assert selected.count() == 1 and selected.get_attribute("data-metric") == "priority_rate"


# ---- note for states without seat boundaries (final spec, item 7; not yet run) -----------------

NOTE_ONE = (
    "Constituency boundaries are not available for this state (re-delimited). Figures are shown state-wide."
)
NOTE_MANY = NOTE_ONE.replace("for this state", "for these states")


def _contrast(fg, bg):
    def lum(rgb):
        def ch(c):
            c = c / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = rgb
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def test_state_level_note_card_tooltip_and_legend(page, base):
    _open_map(page, base)
    cov = page.evaluate("fetch('/api/geographic-coverage').then(r => r.json())")
    reason = "redelimited_boundary_not_available"
    states = sorted({a["state"] for a in cov["no_boundary"] if a["reason"] == reason})
    note = page.get_by_test_id("map-state-level-note")
    if not states:
        assert note.count() == 0
        return
    assert note.get_attribute("role") == "note"
    text = note.inner_text()
    assert (NOTE_MANY if len(states) > 1 else NOTE_ONE) in text
    for st in states:  # from the coverage data, not a fixed list
        assert st.lower() in text.lower(), st
    assert note.locator("svg").count() == 1  # the info icon
    colours = note.evaluate(
        """el => {
            const rgb = s => s.match(/\\d+/g).slice(0, 3).map(Number);
            const text = el.querySelector('div > div:last-child') || el;
            return [rgb(getComputedStyle(el).backgroundColor), rgb(getComputedStyle(el).color),
                    rgb(getComputedStyle(text).color), getComputedStyle(el).borderRadius];
        }"""
    )
    bg, _, fg, radius = colours
    assert _contrast(fg, bg) >= 4.5, colours
    assert bg not in ([166, 41, 31], [196, 90, 32], [147, 99, 12], [28, 110, 70])  # never a risk colour
    assert float(radius.rstrip("px")) > 0
    legend = page.get_by_test_id("map-legend")
    assert legend.locator('[data-status="stateLevel"]').inner_text().strip() == "State-level view"
    page.locator("path.map-state-area").first.hover(force=True)
    tip = page.locator(".leaflet-tooltip")
    tip.wait_for(state="visible")
    assert NOTE_ONE in tip.inner_text() and "State-level view" in tip.inner_text()


# ---- state-name labels (final spec, item 8; not yet run) ---------------------------------------


def _visible_labels(page):
    return page.evaluate(
        """() => [...document.querySelectorAll('.map-state-label')]
            .filter(el => getComputedStyle(el).display !== 'none')
            .map(el => el.textContent.trim())"""
    )


def test_state_labels_show_at_national_zoom_and_hide_when_zoomed_in(page, base):
    _open_map(page, base)
    outlines = page.evaluate("fetch('/api/boundary-outlines').then(r => r.json())")
    borders = [f["properties"] for f in outlines["features"] if f["properties"]["kind"] == "state_border"]
    names = {p["name"] for p in borders}
    assert page.locator(".map-state-label").count() == len(borders)
    zin = page.locator(".leaflet-control-zoom-in")
    zout = page.locator(".leaflet-control-zoom-out")
    _press(page, zout, 3)  # national zoom (4-5): the larger states are labelled, in English
    shown = _visible_labels(page)
    assert {"Rajasthan", "Madhya Pradesh", "Maharashtra", "Uttar Pradesh"} <= set(shown), shown
    assert set(shown) <= names
    label = page.locator(".map-state-label span").first
    assert label.get_attribute("aria-hidden") == "true"
    assert label.evaluate("el => getComputedStyle(el).pointerEvents") == "none"
    while _zoom(page) < 7:
        _press(page, zin, 1)
    assert _visible_labels(page) == []  # zoomed in: the labels give way to the seats


# ---- Map problem 2 (2026-10-03): search zooms to the seat or state --------------------


def _search_box(page):
    return page.locator(".toolbar-search input")


def _pick(page, text, kind, state=None):
    """Types `text` and clicks its suggestion of `kind` ('MP' or 'Constituency')."""
    _search_box(page).fill(text)
    sel = f'[data-testid=map-search-suggestion][data-type="{kind}"]'
    if state:
        sel += f'[data-state="{state}"]'
    page.locator(sel).first.click()


def _selected_seat_in_view(page):
    """True when the selected seat's polygon (drawn with a 3 px outline) lies
    inside the visible map area."""
    return page.evaluate(
        """() => {
            const box = document.querySelector('[data-testid=map-canvas]').getBoundingClientRect();
            const sel = [...document.querySelectorAll('path.map-area')].filter(p => p.getAttribute('stroke-width') === '3');
            return sel.length === 1 && (() => {
                const r = sel[0].getBoundingClientRect();
                return r.width > 0 && r.left >= box.left - 2 && r.right <= box.right + 2
                    && r.top >= box.top - 2 && r.bottom <= box.bottom + 2;
            })();
        }"""
    )


def test_search_pick_of_a_constituency_flies_to_it_selects_it_and_loads_its_works(page, base):
    _open_map(page, base)
    pick = _constituency_with(page, 10, 300)
    assert pick, "no constituency with 10-300 works"
    state, constituency, _ = pick
    start = _zoom(page)
    n = len(page.api_calls)
    _pick(page, constituency, "Constituency", state)
    page.get_by_test_id("map-selected-chip").wait_for(state="visible", timeout=15000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1200)  # the 0.8 s fly
    assert constituency in page.get_by_test_id("map-selected-chip").inner_text()
    assert _zoom(page) > start
    calls = [parse_qs(urlparse(u).query) for u in page.api_calls[n:] if urlparse(u).path.endswith("/map-works")]
    assert any(q.get("constituency") == [constituency] and q.get("state") == [state] for q in calls), calls
    assert page.get_by_test_id("map-works-button").is_visible()
    assert _selected_seat_in_view(page)


def test_search_pick_of_a_lok_sabha_mp_goes_to_their_seat(page, base):
    _open_map(page, base)
    rows = page.evaluate("fetch('/api/map-data').then(r => r.json())")
    target = None
    for r in rows:
        for name in [m.strip() for m in (r.get("mps") or "").split(",") if m.strip()]:
            prof = page.evaluate("n => fetch('/api/mp-performance/' + encodeURIComponent(n)).then(r => r.json())", name)
            if prof.get("house") == "LS" and prof.get("constituency") == r["Constituency"]:
                target = (name, r["State"], r["Constituency"])
                break
        if target:
            break
    if target is None:
        pytest.skip("no Lok Sabha MP whose profile names their mapped seat")
    name, state, constituency = target
    n = len(page.api_calls)
    _pick(page, name, "MP")
    page.get_by_test_id("map-selected-chip").wait_for(state="visible", timeout=15000)
    assert any(p.startswith("mp-performance/") for p in _paths(page, n)), _paths(page, n)
    assert constituency in page.get_by_test_id("map-selected-chip").inner_text()


def test_search_pick_of_a_rajya_sabha_member_zooms_to_the_state_without_markers(page, base):
    _open_map(page, base)
    works = page.evaluate("fetch('/api/map-works?house=RS&limit=50').then(r => r.json())")
    names = sorted({w["mp"] for w in works if w.get("mp")})
    member = None
    for name in names:
        prof = page.evaluate("n => fetch('/api/mp-performance/' + encodeURIComponent(n)).then(r => r.json())", name)
        if prof.get("house") == "RS":
            member = (name, prof["state"])
            break
    if member is None:
        pytest.skip("no Rajya Sabha member in this data")
    name, state = member
    start = _zoom(page)
    _pick(page, name, "MP")
    note = page.get_by_test_id("map-search-note")
    note.wait_for(state="visible", timeout=15000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1200)
    assert "Rajya Sabha member" in note.inner_text()
    assert _zoom(page) >= start
    assert page.locator("path.map-search-highlight").count() > 0  # the state is outlined
    assert page.locator(".leaflet-marker-cluster").count() == 0  # no works markers
    assert page.get_by_test_id("map-selected-chip").count() == 0


def test_search_pick_of_a_redelimited_seat_opens_its_state_level_view(page, base):
    _open_map(page, base)
    cov = page.evaluate("fetch('/api/geographic-coverage').then(r => r.json())")
    seats = [a for a in cov["no_boundary"] if a["reason"] == "redelimited_boundary_not_available"]
    if not seats:
        pytest.skip("no re-delimited seat in this data")
    lists = page.evaluate("fetch('/api/constituencies').then(r => r.json())")["constituencies"]
    known = {(c["State"], c["Constituency"]) for c in lists}
    seat = next((a for a in seats if (a["state"], a["name"]) in known), None)
    if seat is None:
        pytest.skip("no re-delimited seat with works")
    _pick(page, seat["name"], "Constituency", seat["state"])
    page.get_by_test_id("map-state-level-card").wait_for(state="visible", timeout=15000)
    assert "re-delimited" in page.get_by_test_id("map-search-note").inner_text()


def test_search_with_no_match_says_so_and_keeps_the_view(page, base):
    _open_map(page, base)
    start = _zoom(page)
    box = _search_box(page)
    box.fill("zzqqxx")
    box.press("Enter")
    note = page.get_by_test_id("map-search-note")
    note.wait_for(state="visible")
    assert 'No MP or constituency matches "zzqqxx"' in note.inner_text()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)
    assert _zoom(page) == start
    assert page.get_by_test_id("map-selected-chip").count() == 0


def test_metric_change_keeps_the_view(page, base):
    """Before: every metric change refitted the map to all of India."""
    _open_map(page, base)
    _press(page, page.locator(".leaflet-control-zoom-in"), 2)
    before = _zoom(page)
    page.locator('[data-metric="average_risk"]').click()
    page.wait_for_timeout(800)
    assert _zoom(page) == before


# ---- Map problem 4 (2026-10-03): load speed ---------------------------------------------


def _static_outlines_deployed(page):
    return page.evaluate("fetch('/geo/india-outlines.json').then(r => r.ok, () => false)")


def test_static_outline_file_is_used_without_the_api(page, base):
    """The outlines come from the precomputed static file: no request to
    /api/boundary-outlines (its first build takes ~27 s)."""
    seen = []
    page.on("request", lambda r: seen.append(r.url))
    _open_map(page, base)
    if not _static_outlines_deployed(page):
        pytest.skip("frontend/public/geo/india-outlines.json is not generated yet")
    page.locator("path.map-land").first.wait_for(state="attached", timeout=10_000)
    assert any("/geo/india-outlines.json" in u for u in seen), seen
    assert "boundary-outlines" not in _paths(page)
    assert page.locator("path.map-outline-state").count() >= 30
    assert page.get_by_test_id("map-outlines-failed").count() == 0


def test_outlines_fall_back_to_the_api_when_the_static_file_is_missing(page, base):
    page.route(STATIC_OUTLINES, lambda route: route.fulfill(status=404, body="not found"))
    _open_map(page, base)
    page.locator("path.map-land").first.wait_for(state="attached", timeout=25_000)
    assert "boundary-outlines" in _paths(page)
    assert page.get_by_test_id("map-outlines-failed").count() == 0


def test_static_file_returning_the_app_page_falls_back_to_the_api(page, base):
    """A host that answers a missing file with the app's HTML page (200)."""
    page.route(STATIC_OUTLINES, lambda route: route.fulfill(status=200, body="<!doctype html><html></html>",
                                                            content_type="text/html"))
    _open_map(page, base)
    page.locator("path.map-land").first.wait_for(state="attached", timeout=25_000)
    assert "boundary-outlines" in _paths(page)


def test_seat_layers_are_restyled_not_rebuilt(page, base):
    """A metric change or a selection restyles the existing seat paths (and
    keeps their status classes current); it never rebuilds them."""
    _open_map(page, base)
    page.evaluate("() => document.querySelectorAll('path.map-area').forEach((p, i) => { p.dataset.probe = i; })")
    n = page.locator("path.map-area").count()
    page.locator('[data-metric="average_risk"]').click()
    page.wait_for_timeout(500)
    state, constituency, _ = _biggest_constituency(page)
    _select(page, state, constituency)
    assert page.locator("path.map-area").count() == n
    assert page.locator("path.map-area[data-probe]").count() == n  # the same elements
    statuses = page.evaluate(
        "() => [...document.querySelectorAll('path.map-area')].map(p => [...p.classList].filter(c => /^map-area-/.test(c)))"
    )
    assert all(len(c) == 1 for c in statuses), statuses  # exactly one status class each
    assert page.locator("path.map-area[stroke-width='3']").count() == 1  # the selection


def test_seat_boundaries_are_fetched_once_per_page_load(page, base):
    """Before: /api/geojson was requested (and parsed) again on every filter change."""
    _open_map(page, base)
    page.locator(".toolbar-select").nth(1).select_option("HIGH")  # risk level filter
    page.wait_for_load_state("networkidle")
    page.locator(".toolbar-select").nth(1).select_option("")
    page.wait_for_load_state("networkidle")
    assert _paths(page).count("geojson") == 1, _paths(page)


def test_filter_change_keeps_the_map_usable(page, base):
    """After the first load a filter change shows the small "Updating" pill,
    never the full-map overlay (LoadingState's spinner)."""
    _open_map(page, base)
    page.evaluate(
        """() => {
            window.__seen = { pill: false, overlay: false };
            const check = () => {
                if (document.querySelector('[data-testid=map-updating]')) window.__seen.pill = true;
                if (document.querySelector('.map-shell .chakra-spinner')) window.__seen.overlay = true;
            };
            new MutationObserver(check).observe(document.body, { childList: true, subtree: true });
        }"""
    )
    page.locator(".toolbar-select").nth(1).select_option("HIGH")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    seen = page.evaluate("() => window.__seen")
    assert seen == {"pill": True, "overlay": False}, seen


# ---- Map problem 3 (2026-10-03): Assam's shape, colour, tooltip and note ---------------------

REASON = "redelimited_boundary_not_available"


def _redelimited_states(page):
    cov = page.evaluate("fetch('/api/geographic-coverage').then(r => r.json())")
    return sorted({a["state"] for a in cov["no_boundary"] if a["reason"] == REASON})


def _tooltips_of_state_areas(page):
    texts = []
    paths = page.locator("path.map-state-area")
    for i in range(paths.count()):
        paths.nth(i).hover(force=True)
        tip = page.locator(".leaflet-tooltip")
        tip.wait_for(state="visible")
        texts.append(tip.inner_text())
        page.mouse.move(5, 5)
        page.wait_for_timeout(200)
    return texts


def test_assam_is_drawn_from_the_static_file_while_the_api_is_slow(page, base):
    """Before: the shapes came only from /api/boundary-outlines, whose first
    build (~27 s) outlasted the 20 s time-out, so Assam stayed blank."""
    page.route("**/api/boundary-outlines", lambda route: None)  # never answered
    _open_map(page, base)
    if not _static_outlines_deployed(page):
        pytest.skip("frontend/public/geo/india-outlines.json is not generated yet")
    states = _redelimited_states(page)
    assert "Assam" in states
    page.wait_for_function(
        f"() => document.querySelectorAll('path.map-state-area').length === {len(states)}", timeout=5_000
    )
    tips = _tooltips_of_state_areas(page)
    assert all(NOTE_ONE in t and "State-level view" in t for t in tips), tips
    assert any("Assam" in t for t in tips), tips
    assert "Assam" in page.get_by_test_id("map-state-level-note").inner_text()
    assert page.get_by_test_id("map-outlines-failed").count() == 0


def test_redelimited_shapes_follow_the_coverage_data(page, base):
    """The shapes drawn are the states the coverage data lists (here narrowed
    to Jammu and Kashmir), not the outline file's list."""

    def only_jk(route):
        resp = route.fetch()
        body = resp.json()
        body["no_boundary"] = [a for a in body["no_boundary"] if a["state"] == "Jammu And Kashmir"]
        route.fulfill(response=resp, json=body)

    page.route("**/api/geographic-coverage", only_jk)
    _open_map(page, base)
    page.locator("path.map-land").first.wait_for(state="attached", timeout=25_000)
    page.wait_for_timeout(500)
    assert page.locator("path.map-state-area").count() == 1
    assert all("Assam" not in t for t in _tooltips_of_state_areas(page))


def test_failed_coverage_still_draws_the_outlines_redelimited_shapes(page, base):
    page.route("**/api/geographic-coverage", lambda route: route.fulfill(status=503, body="{}"))
    _open_map(page, base)
    page.locator("path.map-land").first.wait_for(state="attached", timeout=25_000)
    page.wait_for_timeout(500)
    src = "/geo/india-outlines.json" if _static_outlines_deployed(page) else "/api/boundary-outlines"
    outlines = page.evaluate(f"fetch('{src}').then(r => r.json())")
    expected = [f for f in outlines["features"] if f["properties"]["kind"] == "redelimited_state"]
    assert page.locator("path.map-state-area").count() == len(expected) > 0


def test_no_boundary_reason_says_state_level(page, base):
    """map.noBoundary.reason no longer says the works are not on the map."""
    _open_map(page, base)
    if not _redelimited_states(page):
        pytest.skip("no re-delimited seat in this data")
    text = page.get_by_test_id("map-no-boundary").inner_text()
    assert "one state-level shape" in text and "not on the map" not in text


# ---- the seat with no portal seat (2026-10-03): Khadoor Sahib, Punjab -------------------------

UNSERVED_SEATS = "**/geo/unserved-seats.json*"


def _hover_seat_named(page, name, statuses=("none",)):
    """Hovers the seat paths of the given statuses until one's tooltip names
    `name`; returns that path's locator (None if no tooltip does)."""
    for status in statuses:
        paths = page.locator(f"path.map-area-{status}")
        for i in range(paths.count()):
            paths.nth(i).hover(force=True)
            tip = page.locator(".leaflet-tooltip")
            tip.wait_for(state="visible")
            if name in tip.inner_text():
                return paths.nth(i)
            page.mouse.move(5, 5)
    return None


def test_khadoor_sahib_is_drawn_as_a_no_works_seat(page, base):
    """Before: /api/geojson has no feature for it (no portal seat), so the
    Map showed bare land fill there, with no hover and no click."""
    _open_map(page, base)
    geo = page.evaluate("fetch('/api/geojson').then(r => r.json())")
    assert "pc:3:3" not in {f["properties"]["area_key"] for f in geo["features"]}
    assert page.locator("path.map-area").count() == len(geo["features"]) + 1
    seat = _hover_seat_named(page, "KHADOOR SAHIB")
    assert seat is not None, "no seat tooltip names Khadoor Sahib"
    tip = page.locator(".leaflet-tooltip").inner_text()
    assert "Punjab" in tip and "No works recorded" in tip
    assert seat.get_attribute("fill").lower() == STATUS_FILL["none"] and seat.get_attribute("stroke-dasharray")
    seat.click(force=True)
    popup = page.locator(".leaflet-popup-content")
    popup.wait_for(state="visible")
    assert "KHADOOR SAHIB" in popup.inner_text() and "No works recorded" in popup.inner_text()
    assert popup.locator("button").count() == 0  # no Explore: there is nothing to drill into
    assert _paths(page).count("geojson") == 1


def test_khadoor_sahib_says_no_works_match_when_filtered(page, base):
    _open_map(page, base)
    page.locator(".toolbar-select").nth(1).select_option("CRITICAL")
    page.wait_for_load_state("networkidle")
    assert _hover_seat_named(page, "KHADOOR SAHIB") is not None
    assert "No works match the current filters" in page.locator(".leaflet-tooltip").inner_text()


def test_khadoor_sahib_takes_punjabs_colour_in_rajya_sabha(page, base):
    _open_map(page, base)
    page.locator('.shell-topbar .house-toggle-btn[data-house="RS"]').click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    # every Punjab seat polygon has the same fill and status in the state view
    looks = set()
    for status in ("value", "few", "none"):
        paths = page.locator(f"path.map-area-{status}")
        for i in range(paths.count()):
            paths.nth(i).hover(force=True)
            tip = page.locator(".leaflet-tooltip")
            tip.wait_for(state="visible")
            if "Punjab" in tip.inner_text():
                looks.add((status, paths.nth(i).get_attribute("fill")))
            page.mouse.move(5, 5)
    assert len(looks) == 1, looks


def test_missing_unserved_seats_file_leaves_the_api_seats(page, base):
    page.route(UNSERVED_SEATS, lambda route: route.fulfill(status=404, body="not found"))
    _open_map(page, base)
    geo = page.evaluate("fetch('/api/geojson').then(r => r.json())")
    page.locator("path.map-area").first.wait_for(state="attached", timeout=25_000)
    assert page.locator("path.map-area").count() == len(geo["features"])


def test_unserved_seat_is_skipped_when_the_api_serves_it(page, base):
    """If a later snapshot gives the seat a portal row (and geo_area a
    polygon), the API's feature is drawn and the static copy is not."""
    extra = page.context.request.get(f"{base}/geo/unserved-seats.json").json()

    def with_extra(route):
        resp = route.fetch()
        body = resp.json()
        body["features"] = body["features"] + extra["features"]
        route.fulfill(response=resp, json=body)

    page.route("**/api/geojson", with_extra)
    _open_map(page, base)
    page.locator("path.map-area").first.wait_for(state="attached", timeout=25_000)
    served = page.evaluate("fetch('/api/geojson').then(r => r.json())")
    assert page.locator("path.map-area").count() == len(served["features"])  # no second copy
