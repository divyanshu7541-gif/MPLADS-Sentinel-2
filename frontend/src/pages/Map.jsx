import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate, Link } from 'react-router-dom';
import { Search, Filter, ChevronDown, ChevronUp, Layers, ArrowLeft, RotateCcw, Info, Target, MapPin, BarChart3, X } from 'lucide-react';
import { getMapData, getMapWorks, getMapFilters, getMps, getConstituencies, getGeoJSON, getGeographicCoverage, getBoundaryOutlines, getStaticOutlines, getUnservedSeats, getConstituencyIntelligence, getMpPerformance, isNotFound } from '../services/api';
import LoadingState from '../components/LoadingState';
import ErrorState from '../components/ErrorState';
import RiskBadge from '../components/RiskBadge';
import StageBadge from '../components/StageBadge';
import { useHouse, HouseNotApplicable } from '../components/HouseToggle';
import { useTranslation } from '../i18n';
import { useLabels } from '../i18n/labels';

const MAP_CENTER = [20.5937, 78.9629];
const MAP_ZOOM = 5;
// Zoom and pan limits. With no tile layer there is nothing to see beyond
// India or below district scale: zoom 4 shows the whole country with room
// around it, zoom 10 is about 150 m per pixel (the boundaries are simplified
// to 0.5-1 km, so going further only magnifies their corners). The bounds are
// the Survey of India-derived outline's extent (6.76-37.08 N, 68.19-97.42 E)
// plus about 3 degrees, so no one can pan or zoom into empty space.
const MAP_MIN_ZOOM = 4;
const MAP_MAX_ZOOM = 10;
const MAP_MAX_BOUNDS = [[3.5, 65.0], [40.5, 100.5]];

const PRIORITY_COLORS = {
  CRITICAL: '#a6291f',
  HIGH: '#c45a20',
  MODERATE: '#93630c',
  LOW: '#1c6e46',
};
const NO_TIER_COLOR = '#b0b8c8';
// Worst first: a cluster takes the colour of the first tier present.
const TIER_ORDER = ['CRITICAL', 'HIGH', 'MODERATE', 'LOW'];
// Small-number rule of the map API (backend_v2/app/geo/service.py
// MIN_WORKS_FOR_RATE): below this many works an area's rates are null.
const MIN_WORKS_FOR_RATE = 10;
// The works list shows this many rows, and "Show more" adds this many.
const WORKS_LIST_PAGE = 25;
// A cluster bubble spreads out (spiderfies) at most this many markers: every
// work in a constituency shares one point, and a spiral of thousands is
// unreadable and slow. Above it, the highest-risk works are spread out (same
// order as the works list) and a note gives the full count.
const SPIDERFY_CAP = 1000;
// A bubble clicked below this zoom flies in to it first, then spreads out.
const SPIDER_ZOOM = 8;
// The constituency value of Rajya Sabha works (backend_v2/app/geo/service.py
// RS_CONSTITUENCY): a label, not a place, so never a search suggestion.
const RS_CONSTITUENCY = 'Sitting Rajya Sabha';
// The side panel's width plus its margin (index.css .map-floating-panel), so
// a zoomed-to area is never hidden under the panel.
const PANEL_PAD = 356;

// Upper-case letters only, "&" read as "and", a leading "The" dropped: our
// state names against the Admin2 file's names ("Jammu And Kashmir" and
// "Jammu & Kashmir" compare equal).
function normState(name) {
  return String(name || '').toUpperCase().replace(/&/g, ' AND ').replace(/^\s*THE\s+/, '').replace(/[^A-Z]/g, '');
}

function tierRank(work) {
  const i = TIER_ORDER.indexOf(work.risk_level || work.priority);
  return i < 0 ? TIER_ORDER.length : i;
}

// Worst tier first, then the largest amount (no amount last), then record ID
// so the order is stable.
function compareWorks(a, b) {
  const byTier = tierRank(a) - tierRank(b);
  if (byTier) return byTier;
  const aa = a.amount == null ? null : Number(a.amount);
  const ba = b.amount == null ? null : Number(b.amount);
  if (aa !== ba) {
    if (aa == null) return 1;
    if (ba == null) return -1;
    return ba - aa;
  }
  return String(a.record_id).localeCompare(String(b.record_id));
}
// Count label colour per fill, chosen by WCAG contrast (white fails 4.5:1 on
// HIGH: 4.35, black gives 4.83; the grey needs dark text: 8.47).
const CLUSTER_TEXT = {
  CRITICAL: '#ffffff',
  HIGH: '#000000',
  MODERATE: '#ffffff',
  LOW: '#ffffff',
  NONE: '#141c33',
};

// labelKey/descKey are i18n keys; `format` receives the locale-aware rupee
// formatter (labels.inr) as its second argument.
const METRIC_MODES = [
  { key: 'priority_rate', labelKey: 'map.priorityRate', descKey: 'map.metric.rate.desc', format: v => v != null ? v.toFixed(1) + '%' : '-' },
  { key: 'flagged_count', labelKey: 'map.metric.count', descKey: 'map.metric.count.desc', format: v => v != null ? String(v) : '-' },
  { key: 'average_risk', labelKey: 'map.metric.risk', descKey: 'map.metric.risk.desc', format: v => v != null ? v.toFixed(1) : '-' },
  { key: 'financial_exposure', labelKey: 'map.metric.exposure', descKey: 'map.metric.exposure.desc', format: (v, inr) => v != null ? inr(v) : '-' },
];

function getMetricValue(c, key) {
  if (!c) return null;
  switch (key) {
    case 'priority_rate': return c.flag_rate;
    case 'flagged_count': return c.flagged;
    case 'average_risk': return c.avg_risk_pct;
    case 'financial_exposure': return c.financial_exposure;
    default: return 0;
  }
}

function getColorForValue(value, metricKey) {
  if (value == null || isNaN(value)) return '#b0b8c8';
  switch (metricKey) {
    case 'priority_rate':
      if (value >= 15) return '#a6291f';
      if (value >= 10) return '#c45a20';
      if (value >= 5) return '#93630c';
      return '#1c6e46';
    case 'flagged_count':
      if (value >= 200) return '#a6291f';
      if (value >= 100) return '#c45a20';
      if (value >= 30) return '#93630c';
      return '#1c6e46';
    case 'average_risk':
      if (value >= 65) return '#a6291f';
      if (value >= 50) return '#c45a20';
      if (value >= 35) return '#93630c';
      return '#1c6e46';
    case 'financial_exposure':
      if (value >= 50000000) return '#a6291f';
      if (value >= 20000000) return '#c45a20';
      if (value >= 5000000) return '#93630c';
      return '#1c6e46';
    default: return '#b0b8c8';
  }
}

// Areas without a value never take a risk colour. Two neutral slate styles,
// told apart by shade and outline (not colour alone):
//   none -- no works recorded here (or none match the filters): dark, dashed
//   few  -- works exist, but too few for a rate or average: lighter, dotted
//   redelimited -- the fill of a re-delimited state's shape when it takes no
//                  risk colour (Count / Exposure, or figures unavailable)
const AREA_STATUS_STYLE = {
  none: { fillColor: '#3f4a63', fillOpacity: 0.5, color: '#3f4a63', dashArray: '5 4' },
  few: { fillColor: '#8d97ad', fillOpacity: 0.75, color: '#5c6680', dashArray: '1 3' },
  redelimited: { fillColor: '#dfe2ea', fillOpacity: 0.6, color: '#5c6680', dashArray: '8 4' },
};
const LEGEND_BORDER = { none: 'dashed', few: 'dotted', redelimited: 'dashed' };
// Re-delimited states (seats without boundaries) are drawn as one state-level
// shape, coloured from map-data level=state on the rate metrics. A heavy long
// dash marks them as state-level in every view, so it is not told by colour
// alone. On Flagged Count and Exposure (state totals, not comparable with the
// seat bands) or when the state figures are unavailable they take the neutral
// "redelimited" fill, never a risk colour.
const STATE_AREA_LINE = { color: '#2b3245', weight: 2, dashArray: '10 5' };
// No tile layer: a plain neutral background. From /api/boundary-outlines,
// bottom to top: the Survey of India-derived national outline as an opaque
// neutral land fill (no stroke), the Admin2 state borders, the seat polygons
// (overlay pane), the national outline as a thin line, then the markers.
const MAP_BACKGROUND = '#f4f6f9';
const LAND_FILL_STYLE = { fillColor: '#e3e7ee', fillOpacity: 1, stroke: false };
const STATE_BORDER_STYLE = { color: '#8a93a8', weight: 0.8, opacity: 1, fill: false };
const INDIA_OUTLINE_STYLE = { color: '#2b3245', weight: 1.2, opacity: 0.9 };
// State-name labels (English, from the state borders already loaded): all
// states at zoom 6, the larger ones only at zoom 4-5, none from zoom 7 (the
// seats and markers take over). "Small" = bounding box under 2 square degrees.
const SMALL_STATE_DEG2 = 2;
const zoomBand = z => (z <= 5 ? 'low' : z <= 6 ? 'mid' : 'high');

// Rate-type metrics are null under the small-number rule; counts and sums never are.
const RATE_METRICS = new Set(['priority_rate', 'average_risk']);

function getLegendItems(metricKey, t, filtered, minWorks, outlineKinds) {
  const cr = t('common.unit.cr');
  const status = [];
  if (RATE_METRICS.has(metricKey)) {
    status.push({ label: t('map.status.few', { n: minWorks }), status: 'few' });
  }
  status.push({ label: t(filtered ? 'map.status.noneFiltered' : 'map.status.none'), status: 'none' });
  // The re-delimited states' shapes (long-dashed outline): "State-level view".
  if (outlineKinds.has('redelimited_state')) status.push({ label: t('map.stateLevel.legend'), status: 'stateLevel' });
  if (outlineKinds.has('national_outline')) status.push({ label: t('map.legend.indiaOutline'), status: 'national' });
  switch (metricKey) {
    case 'priority_rate':
      return [
        { label: '\u226515%', color: '#a6291f' },
        { label: '10\u201315%', color: '#c45a20' },
        { label: '5\u201310%', color: '#93630c' },
        { label: '<5%', color: '#1c6e46' },
        ...status,
      ];
    case 'flagged_count':
      return [
        { label: '\u2265200', color: '#a6291f' },
        { label: '100\u2013200', color: '#c45a20' },
        { label: '30\u2013100', color: '#93630c' },
        { label: '<30', color: '#1c6e46' },
        ...status,
      ];
    case 'average_risk':
      return [
        { label: '\u226565', color: '#a6291f' },
        { label: '50\u201365', color: '#c45a20' },
        { label: '35\u201350', color: '#93630c' },
        { label: '<35', color: '#1c6e46' },
        ...status,
      ];
    case 'financial_exposure':
      return [
        { label: `\u226550 ${cr}`, color: '#a6291f' },
        { label: `20\u201350 ${cr}`, color: '#c45a20' },
        { label: `5\u201320 ${cr}`, color: '#93630c' },
        { label: `<5 ${cr}`, color: '#1c6e46' },
        ...status,
      ];
    default: return status;
  }
}

// What a seat shows under the current data (ctx = seatCtxRef.current):
// status null is coloured by its value; 'none' has no works (or none match
// the filters); 'few' has works but the metric is withheld (small numbers).
function seatInfo(ctx, props) {
  const state = props.dataset_state;
  const constituency = props.dataset_constituency;
  const cData = ctx.dataMap[ctx.isRS ? state : state + '|' + constituency];
  const metricValue = cData ? getMetricValue(cData, ctx.mapMetric) : null;
  const hasValue = metricValue != null && !isNaN(metricValue);
  const status = !cData ? 'none' : (hasValue ? null : 'few');
  const isSelected = !!(ctx.selected && ctx.selected.state === state && ctx.selected.constituency === constituency);
  return { cData, metricValue, status, isSelected };
}

function seatStyle(ctx, props) {
  const { metricValue, status, isSelected } = seatInfo(ctx, props);
  const look = status
    ? AREA_STATUS_STYLE[status]
    : { fillColor: getColorForValue(metricValue, ctx.mapMetric), fillOpacity: 0.7, color: 'rgba(255,255,255,0.7)', dashArray: null };
  return {
    fillColor: look.fillColor,
    weight: isSelected ? 3 : 1.5,
    opacity: 1,
    color: isSelected ? '#141c33' : look.color,
    dashArray: isSelected ? null : look.dashArray,
    fillOpacity: isSelected ? 0.85 : look.fillOpacity,
    className: 'map-area map-area-' + (status || 'value'),
  };
}

// Fetched once per page load and shared by every mount of the Map
// (StrictMode's second mount in development, and coming back to the Map):
// the 1.45 MB seat boundaries, and the display-only outlines -- the static
// precomputed file first, then /api/boundary-outlines (20 s time-out). A
// failure is not kept, so the next visit tries again.
// The seats come with the 2019 seats that have no portal seat
// (unserved-seats.json, today Khadoor Sahib), each added only when the API
// does not already serve its area_key. They have no map-data row, so they
// take the "No works recorded" style and tooltip like any seat without
// works. If that file fails, the API's seats are drawn as before.
function withUnservedSeats(geojson, extra) {
  const feats = (extra && extra.features) || [];
  if (!geojson || !feats.length) return geojson;
  const served = new Set((geojson.features || []).map(f => f.properties && f.properties.area_key));
  const add = feats.filter(f => f && f.geometry && f.properties && f.properties.area_key && !served.has(f.properties.area_key));
  return add.length ? { ...geojson, features: [...(geojson.features || []), ...add] } : geojson;
}
let geojsonPromise = null;
function loadGeoJSON() {
  if (!geojsonPromise) {
    geojsonPromise = Promise.all([getGeoJSON(), getUnservedSeats().catch(() => null)])
      .then(([geojson, extra]) => withUnservedSeats(geojson, extra));
    geojsonPromise.catch(() => { geojsonPromise = null; });
  }
  return geojsonPromise;
}
let outlinesPromise = null;
function loadOutlines() {
  if (!outlinesPromise) {
    outlinesPromise = getStaticOutlines().catch(() => getBoundaryOutlines());
    outlinesPromise.catch(() => { outlinesPromise = null; });
  }
  return outlinesPromise;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export default function MapPage() {
  const { t } = useTranslation();
  const labels = useLabels();
  const { house } = useHouse();
  const isRS = house === 'RS';
  // Leaflet popups are built as HTML strings when opened, outside React's
  // render cycle. They read the latest labels through this ref so a language
  // switch applies to the next popup without rebuilding any map layers.
  const labelsRef = useRef(labels);
  labelsRef.current = labels;
  const navigate = useNavigate();
  const mapRef = useRef(null);
  const mapInstanceRef = useRef(null);
  const geoJsonLayerRef = useRef(null);
  const outlineLayerRef = useRef(null);
  const stateAreaLayerRef = useRef(null);
  const stateAreaBoundsRef = useRef({});
  const workMarkersRef = useRef(null);
  // Display-only outline of the state a Rajya Sabha member search zoomed to.
  const searchHighlightRef = useRef(null);

  const [loading, setLoading] = useState(true);
  const [constituencyData, setConstituencyData] = useState([]);
  // Lok Sabha: map-data level=state rows, for the re-delimited states' shapes
  // (null when they could not be loaded). Rajya Sabha uses constituencyData.
  const [stateRows, setStateRows] = useState(null);
  const [works, setWorks] = useState([]);
  const [geojsonData, setGeojsonData] = useState(null);
  const [coverage, setCoverage] = useState(null);
  // Display-only outlines; a failure only adds a note (the map loads without them).
  const [outlines, setOutlines] = useState(null);
  const [outlinesFailed, setOutlinesFailed] = useState(false);
  const [filterOptions, setFilterOptions] = useState({});
  const [mapMetric, setMapMetricState] = useState('priority_rate');
  const setMapMetric = useCallback((key) => {
    if (isRS && !RATE_METRICS.has(key)) return;
    setMapMetricState(key);
  }, [isRS]);
  // Switching to Rajya Sabha with Flagged Count or Exposure selected would
  // colour state totals with the constituency bands: fall back to the default.
  useEffect(() => {
    if (isRS && !RATE_METRICS.has(mapMetric)) setMapMetricState('priority_rate');
  }, [isRS, mapMetric]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const sidebarOpenRef = useRef(sidebarOpen);
  sidebarOpenRef.current = sidebarOpen;
  const [searchInput, setSearchInput] = useState('');
  const [mpList, setMpList] = useState([]);
  // { state, name } per Lok Sabha seat (a name can exist in two states).
  const [constituencyList, setConstituencyList] = useState([]);
  // One line under the search box after a pick: what the map did, or that
  // nothing matched. { key, vars } of an i18n string, or null.
  const [searchNote, setSearchNote] = useState(null);
  // A picked suggestion waits here until the map data for its text has
  // loaded (the pick also sets the text filter, and every filter reload
  // clears the selection), then the map flies to it.
  const [pendingPick, setPendingPick] = useState(null);
  const pendingPickRef = useRef(null);
  pendingPickRef.current = pendingPick;
  // The search text the loaded map data belongs to (null before the first load).
  const [loadedSearch, setLoadedSearch] = useState(null);
  // Set when new map data arrives (a filter or House change): the seat layer
  // then fits the map once. A metric or selection change never refits.
  const fitPendingRef = useRef(true);
  const fittedOnceRef = useRef(false);
  // Read by the seat polygons' style, tooltip and popup functions.
  const seatCtxRef = useRef({ dataMap: {}, mapMetric: 'priority_rate', isRS: false, filtered: false, selected: null });
  // Counts seat-layer builds, so the restyle runs after every build.
  const [seatsBuilt, setSeatsBuilt] = useState(0);
  // After the first load, reloads show a small "Updating" pill and the map
  // stays usable (the full overlay is for the first load only).
  const [firstLoadDone, setFirstLoadDone] = useState(false);
  // The outlines are still loading after 1.5 s: a one-line status.
  const [outlinesSlow, setOutlinesSlow] = useState(false);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [suggestHighlight, setSuggestHighlight] = useState(-1);
  const searchBoxRef = useRef(null);

  const [activeFilters, setActiveFilters] = useState({ state: '', priority: '', stage: '', search: '' });

  const [selectedConstituency, setSelectedConstituency] = useState(null);
  const [constituencyIntel, setConstituencyIntel] = useState(null);
  const [intelLoading, setIntelLoading] = useState(false);
  const [showWorkMarkers, setShowWorkMarkers] = useState(false);
  const [worksLoading, setWorksLoading] = useState(false);
  // Works list in the side panel, built only from the works already loaded
  // for the selected constituency. Opened only by the panel button.
  const [worksListOpen, setWorksListOpen] = useState(false);
  // { shown, total } while a capped spiral is open (see SPIDERFY_CAP).
  const [spiderCap, setSpiderCap] = useState(null);
  const [worksListLimit, setWorksListLimit] = useState(WORKS_LIST_PAGE);
  const worksListRef = useRef(null);
  // Failed loads are kept per data source so each one is shown where its
  // data would have appeared -- never silently rendered as "no data".
  const [loadErrors, setLoadErrors] = useState({});
  const [reloadKey, setReloadKey] = useState(0);
  const setLoadError = useCallback((key, err) => {
    setLoadErrors(prev => {
      if (!err && !prev[key]) return prev;
      const next = { ...prev };
      if (err) next[key] = err; else delete next[key];
      return next;
    });
  }, []);

  const loadFilters = useCallback(() => {
    setLoadError('filters', null);
    getMapFilters().then(setFilterOptions).catch(e => setLoadError('filters', e));
  }, [setLoadError]);

  const loadLists = useCallback(() => {
    setLoadError('lists', null);
    Promise.all([getMps(), getConstituencies()]).then(([mps, cons]) => {
      setMpList(mps.mps || []);
      setConstituencyList((cons.constituencies || [])
        .filter(c => c.Constituency && c.Constituency !== RS_CONSTITUENCY)
        .map(c => ({ state: c.State, name: c.Constituency })));
    }).catch(e => setLoadError('lists', e));
  }, [setLoadError]);

  useEffect(() => { loadFilters(); }, [loadFilters]);
  useEffect(() => { loadLists(); }, [loadLists]);
  // Fetched once, apart from the map data, so it never delays or fails the
  // map: if it fails (or takes over 20 s), the seats, colours, clusters and
  // panel work as usual on the plain background, with a one-line note.
  useEffect(() => {
    let live = true;
    const slow = setTimeout(() => { if (live) setOutlinesSlow(true); }, 1500);
    loadOutlines()
      .then(d => { if (live) { setOutlines(d); setOutlinesFailed(false); } })
      .catch(() => { if (live) setOutlinesFailed(true); })
      .finally(() => { clearTimeout(slow); if (live) setOutlinesSlow(false); });
    return () => { live = false; clearTimeout(slow); };
  }, []);

  useEffect(() => {
    setLoading(true);
    if (searchHighlightRef.current) searchHighlightRef.current.clearLayers();
    setSelectedConstituency(null);
    setConstituencyIntel(null);
    setShowWorkMarkers(false);
    setWorks([]);
    setWorksListOpen(false);

    const params = {};
    if (activeFilters.state) params.state = activeFilters.state;
    if (activeFilters.priority) params.priority = activeFilters.priority;
    if (activeFilters.stage) params.stage = activeFilters.stage;
    if (activeFilters.search) params.search = activeFilters.search;
    // Rajya Sabha members represent a state: ask for one row per state.
    if (isRS) params.level = 'state';

    setLoadError('data', null);
    setLoadError('boundaries', null);
    setLoadError('coverage', null);
    let live = true;
    Promise.all([
      getMapData(params),
      loadGeoJSON().catch((e) => { setLoadError('boundaries', e); return null; }),
      getGeographicCoverage().catch((e) => { setLoadError('coverage', e); return null; }),
      // State-level rows for the re-delimited states (same filters). A failure
      // leaves those shapes neutral with a note; the seats are unaffected.
      isRS ? Promise.resolve(null) : getMapData({ ...params, level: 'state' }).catch(() => null),
    ]).then(([mapData, geojson, cov, stRows]) => {
      if (!live) return;
      fitPendingRef.current = true;
      setConstituencyData(mapData);
      setStateRows(stRows);
      if (geojson) setGeojsonData(geojson);
      if (cov) setCoverage(cov);
      setLoadedSearch(params.search || '');
      setLoading(false);
      setFirstLoadDone(true);
    }).catch((e) => {
      if (!live) return;
      // Don't leave the previous filters' colours on the map under a failure.
      setConstituencyData([]);
      setLoadError('data', e);
      setLoadedSearch(params.search || '');
      setLoading(false);
    });
    // A newer load (filter change) or an unmount (StrictMode's first mount
    // in development) supersedes this one: its response is ignored.
    return () => { live = false; };
  }, [activeFilters, reloadKey, setLoadError, isRS]);

  const retryMapData = useCallback(() => setReloadKey(k => k + 1), []);

  // constituency null: the state's re-delimited seats' works (redelimited=true).
  const loadConstituencyWorks = useCallback((state, constituency) => {
    setShowWorkMarkers(true);
    const params = constituency ? { state, constituency } : { state, redelimited: 'true' };
    if (activeFilters.priority) params.priority = activeFilters.priority;
    if (activeFilters.stage) params.stage = activeFilters.stage;
    if (activeFilters.search) params.search = activeFilters.search;
    setLoadError('works', null);
    setWorksLoading(true);
    getMapWorks(params).then(data => {
      setWorks(data);
      setWorksLoading(false);
    }).catch((e) => { setWorks([]); setLoadError('works', e); setWorksLoading(false); });
  }, [activeFilters, setLoadError]);

  // Opens the works list of every loaded work. No request.
  const openWorksList = useCallback(() => {
    setWorksListLimit(WORKS_LIST_PAGE);
    setWorksListOpen(true);
    setSidebarOpen(true);
  }, []);

  // Seat polygons (Leaflet layers) whose feature properties pass `test`.
  const seatLayers = useCallback((test) => {
    const out = [];
    if (!geoJsonLayerRef.current) return out;
    geoJsonLayerRef.current.eachLayer(layer => {
      if (!layer.eachLayer) return;
      layer.eachLayer(sub => {
        const props = sub.feature && sub.feature.properties;
        if (props && sub.getBounds && test(props)) out.push(sub);
      });
    });
    return out;
  }, []);

  // Smoothly fits an area, clear of the side panel when it is open beside a
  // wide map (on a narrow screen the panel covers the map anyway).
  const flyToArea = useCallback((bounds, maxZoom) => {
    const map = mapInstanceRef.current;
    if (!map || !bounds || !bounds.isValid()) return;
    const wide = mapRef.current && mapRef.current.clientWidth > 2 * PANEL_PAD;
    const left = sidebarOpenRef.current && wide ? PANEL_PAD : 40;
    map.flyToBounds(bounds, { paddingTopLeft: [left, 60], paddingBottomRight: [40, 60], maxZoom, duration: 0.8 });
  }, []);

  const selectConstituency = useCallback((state, constituency) => {
    // Constituency drill-down doesn't apply to Rajya Sabha; surface the
    // panel's explicit not-applicable notice instead of fetching nothing.
    if (isRS) {
      if (mapInstanceRef.current) mapInstanceRef.current.closePopup();
      setSidebarOpen(true);
      return;
    }
    if (searchHighlightRef.current) searchHighlightRef.current.clearLayers();
    setSelectedConstituency({ state, constituency });
    setIntelLoading(true);
    setShowWorkMarkers(true);
    setWorksListOpen(false);

    setLoadError('intel', null);
    getConstituencyIntelligence(state, constituency).then(intel => {
      setConstituencyIntel(intel);
      setIntelLoading(false);
    }).catch((e) => {
      setConstituencyIntel(null);
      setLoadError('intel', e);
      setIntelLoading(false);
    });

    loadConstituencyWorks(state, constituency);

    const seat = seatLayers(p => p.dataset_state === state && p.dataset_constituency === constituency)[0];
    if (seat) flyToArea(seat.getBounds(), MAP_MAX_ZOOM);
  }, [loadConstituencyWorks, isRS, seatLayers, flyToArea, setLoadError]);

  // A re-delimited state: its works at the state shape's marker point, and
  // the state-level card in the panel (no per-seat intelligence exists).
  const selectStateLevel = useCallback((state) => {
    if (mapInstanceRef.current) mapInstanceRef.current.closePopup();
    if (isRS) {
      setSidebarOpen(true);
      return;
    }
    if (searchHighlightRef.current) searchHighlightRef.current.clearLayers();
    setSelectedConstituency({ state, constituency: null, stateLevel: true });
    setConstituencyIntel(null);
    setIntelLoading(false);
    setLoadError('intel', null);
    setWorksListOpen(false);
    loadConstituencyWorks(state, null);
    flyToArea(stateAreaBoundsRef.current[state], 8);
  }, [loadConstituencyWorks, isRS, setLoadError, flyToArea]);

  // Clears the selected seat or state and its works; the view stays.
  const clearSelection = useCallback(() => {
    setSelectedConstituency(null);
    setConstituencyIntel(null);
    setShowWorkMarkers(false);
    setWorks([]);
    setWorksListOpen(false);
    if (workMarkersRef.current) workMarkersRef.current.clearLayers();
    if (searchHighlightRef.current) searchHighlightRef.current.clearLayers();
  }, []);

  const resetToNational = useCallback(() => {
    clearSelection();
    setSearchNote(null);
    if (mapInstanceRef.current) {
      mapInstanceRef.current.setView(MAP_CENTER, MAP_ZOOM);
    }
  }, [clearSelection]);

  // States whose seats are re-delimited (no boundaries), from the coverage data.
  const redelimitedStates = useMemo(() => [...new Set(((coverage && coverage.no_boundary) || [])
    .filter(a => a.reason === 'redelimited_boundary_not_available').map(a => a.state))].sort(), [coverage]);

  // A state's extent and outline: its Admin2 border from the outlines, else
  // its re-delimited shape's extent, else the union of its seat polygons.
  const stateOutline = useCallback((state) => {
    const L = window.L;
    if (!L) return null;
    const want = normState(state);
    const border = ((outlines && outlines.features) || []).find(f =>
      f.properties && f.properties.kind === 'state_border' && normState(f.properties.name) === want);
    if (border) return { bounds: L.geoJSON(border).getBounds(), features: [border] };
    if (stateAreaBoundsRef.current[state]) return { bounds: stateAreaBoundsRef.current[state], features: [] };
    const seats = seatLayers(p => p.dataset_state === state);
    if (!seats.length) return null;
    const first = seats[0].getBounds();
    const bounds = L.latLngBounds(first.getSouthWest(), first.getNorthEast());
    seats.forEach(l => bounds.extend(l.getBounds()));
    return { bounds, features: seats.map(l => l.feature) };
  }, [outlines, seatLayers]);

  // Search pick of a seat. Re-delimited seats (no boundary) open their
  // state's state-level view; on the Rajya Sabha map a Lok Sabha seat is only
  // shown (there is no constituency drill-down there).
  const goToSeat = useCallback((state, constituency) => {
    const shapes = seatLayers(p => p.dataset_state === state && p.dataset_constituency === constituency);
    if (isRS) {
      const area = shapes.length ? shapes[0].getBounds() : (stateOutline(state) || {}).bounds;
      flyToArea(area, shapes.length ? MAP_MAX_ZOOM : 8);
      setSearchNote({ key: 'map.search.lsOnRs', vars: { name: constituency } });
      return;
    }
    if (!shapes.length && redelimitedStates.includes(state)) {
      selectStateLevel(state);
      setSearchNote({ key: 'map.search.redelimited', vars: { name: constituency, state: labelsRef.current.state(state) } });
      return;
    }
    selectConstituency(state, constituency);
    if (!shapes.length) setSearchNote({ key: 'map.search.noBoundary', vars: { name: constituency } });
  }, [redelimitedStates, isRS, seatLayers, stateOutline, flyToArea, selectStateLevel, selectConstituency]);

  // Search pick of a Rajya Sabha member: members have no constituency, so
  // the map zooms to the state and outlines it, with a note. No works
  // markers are loaded (the Map has no Rajya Sabha drill-down).
  const goToRsMember = useCallback((mp, state) => {
    clearSelection();
    const area = stateOutline(state);
    const L = window.L;
    if (area && L && searchHighlightRef.current) {
      area.features.forEach(f => L.geoJSON(f, {
        pane: 'indiaOutlinePane',
        interactive: false,
        style: () => ({ color: '#141c33', weight: 3, opacity: 1, fill: false, className: 'map-search-highlight' }),
      }).addTo(searchHighlightRef.current));
    }
    if (area) flyToArea(area.bounds, 8);
    setSearchNote({ key: isRS ? 'map.search.rsMember' : 'map.search.rsMemberSwitch', vars: { mp, state: labelsRef.current.state(state) } });
  }, [clearSelection, stateOutline, flyToArea, isRS]);

  // A picked suggestion: { type: 'MP' | 'Constituency', name, state? }.
  const goToTarget = useCallback((target) => {
    setSearchNote(null);
    if (target.type === 'Constituency') {
      goToSeat(target.state, target.name);
      return;
    }
    // The member's seat, state and House (existing endpoint, exact name).
    getMpPerformance(target.name).then(p => {
      const seat = p.constituency && p.constituency !== RS_CONSTITUENCY ? p.constituency : null;
      if (p.house === 'RS' || !seat) {
        goToRsMember(target.name, p.state);
        return;
      }
      const cands = constituencyList.filter(c => c.name === seat);
      const match = cands.find(c => c.state === p.state) || cands[0];
      goToSeat(match ? match.state : p.state, seat);
    }).catch(e => setSearchNote({ key: isNotFound(e) ? 'map.search.noMatch' : 'map.search.failed', vars: { q: target.name } }));
  }, [goToSeat, goToRsMember, constituencyList]);

  // Applies a pick once the map data for its text has loaded.
  useEffect(() => {
    if (!pendingPick || loading || !geojsonData || loadedSearch !== pendingPick.search) return;
    setPendingPick(null);
    goToTarget(pendingPick.target);
  }, [pendingPick, loading, geojsonData, loadedSearch, goToTarget]);

  const handleFilterChange = useCallback((key, value) => {
    setActiveFilters(prev => ({ ...prev, [key]: value }));
  }, []);

  const clearFilters = useCallback(() => {
    setActiveFilters({ state: '', priority: '', stage: '', search: '' });
    setSearchInput('');
    setSuggestOpen(false);
    setSuggestHighlight(-1);
    setSearchNote(null);
    setPendingPick(null);
  }, []);

  const hasActiveFilters = Object.values(activeFilters).some(v => v !== '');

  const suggestions = useMemo(() => {
    const q = searchInput.trim().toLowerCase();
    if (q.length < 2) return [];
    const mpMatches = mpList.filter(n => n.toLowerCase().includes(q)).slice(0, 5).map(n => ({ type: 'MP', name: n }));
    const constMatches = constituencyList.filter(c => c.name.toLowerCase().includes(q)).slice(0, 5)
      .map(c => ({ type: 'Constituency', name: c.name, state: c.state }));
    return [...mpMatches, ...constMatches].slice(0, 8);
  }, [searchInput, mpList, constituencyList]);

  useEffect(() => {
    const handleOutside = (e) => {
      const insideInput = searchBoxRef.current && searchBoxRef.current.contains(e.target);
      const insideDropdown = e.target.closest && e.target.closest('[data-map-search-suggestions]');
      if (!insideInput && !insideDropdown) setSuggestOpen(false);
    };
    document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, []);

  // A pick sets the text filter to the name (as typing does) and flies to the
  // seat or state once the data for that text has loaded (pendingPick).
  const selectSuggestion = (s) => {
    setSearchInput(s.name);
    setSuggestOpen(false);
    setSuggestHighlight(-1);
    setSearchNote(null);
    setActiveFilters(prev => (prev.search === s.name ? prev : { ...prev, search: s.name }));
    setPendingPick({ target: s, search: s.name });
  };

  // Enter: the highlighted suggestion; else a typed name that is exactly one
  // MP or one seat; else, when nothing matches at all, a "no match" note
  // (the map does not move).
  const submitSearch = () => {
    const q = searchInput.trim();
    if (q.length < 2) return;
    const low = q.toLowerCase();
    const seats = constituencyList.filter(c => c.name.toLowerCase() === low);
    const mp = mpList.find(n => n.toLowerCase() === low);
    if (seats.length === 1) selectSuggestion({ type: 'Constituency', name: seats[0].name, state: seats[0].state });
    else if (seats.length === 0 && mp) selectSuggestion({ type: 'MP', name: mp });
    else if (suggestions.length === 0) {
      setSuggestOpen(false);
      setSearchNote({ key: 'map.search.noMatch', vars: { q } });
    }
  };

  const handleSearchKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (suggestOpen && suggestHighlight >= 0 && suggestions[suggestHighlight]) selectSuggestion(suggestions[suggestHighlight]);
      else submitSearch();
      return;
    }
    if (!suggestOpen || suggestions.length === 0) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setSuggestHighlight(h => Math.min(h + 1, suggestions.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSuggestHighlight(h => Math.max(h - 1, 0)); }
    else if (e.key === 'Escape') { setSuggestOpen(false); }
  };

  useEffect(() => {
    const t = setTimeout(() => {
      setActiveFilters(prev => (prev.search === searchInput ? prev : { ...prev, search: searchInput }));
    }, 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    window.__mapSelectConstituency = (state, constituency) => {
      selectConstituency(state, constituency);
    };
    return () => { delete window.__mapSelectConstituency; };
  }, [selectConstituency]);

  useEffect(() => {
    window.__mapSelectState = (state) => { selectStateLevel(state); };
    return () => { delete window.__mapSelectState; };
  }, [selectStateLevel]);

  useEffect(() => {
    window.__mapNavigate = (path) => { navigate(path); };
    return () => { delete window.__mapNavigate; };
  }, [navigate]);

  useEffect(() => {
    if (!mapRef.current || mapInstanceRef.current) return;
    const L = window.L;
    if (!L) return;

    const map = L.map(mapRef.current, {
      center: MAP_CENTER,
      zoom: MAP_ZOOM,
      maxZoom: MAP_MAX_ZOOM,
      minZoom: MAP_MIN_ZOOM,
      maxBounds: MAP_MAX_BOUNDS,
      maxBoundsViscosity: 1.0,
      zoomControl: false,
    });

    L.control.zoom({ position: 'topright' }).addTo(map);

    // No tile layer and no tile provider: the background is plain, and the
    // land is drawn from the boundary files below.
    map.attributionControl.addAttribution('Boundaries: <a href="https://github.com/datameet/maps" target="_blank" rel="noopener noreferrer">datameet/maps</a> (CC-BY-SA 2.5)');

    // Display-only panes. None takes the pointer, so every seat polygon
    // (overlayPane, 400) and marker (markerPane, 600) stays clickable.
    //   indiaLandPane    250  national land fill
    //   stateBorderPane  300  Admin2 state borders
    //   indiaOutlinePane 450  national outline line, above the seat fills
    //   stateLabelPane   460  state-name labels
    [['indiaLandPane', 250], ['stateBorderPane', 300], ['indiaOutlinePane', 450], ['stateLabelPane', 460]].forEach(([name, z]) => {
      const pane = map.createPane(name);
      pane.style.zIndex = z;
      pane.style.pointerEvents = 'none';
    });

    // Labels are shown or hidden by CSS from this attribute (index.css).
    const setBand = () => { if (mapRef.current) mapRef.current.dataset.zoomBand = zoomBand(map.getZoom()); };
    map.on('zoomend', setBand);
    setBand();

    mapInstanceRef.current = map;
    geoJsonLayerRef.current = L.layerGroup().addTo(map);
    outlineLayerRef.current = L.layerGroup().addTo(map);
    stateAreaLayerRef.current = L.layerGroup().addTo(map);
    searchHighlightRef.current = L.layerGroup().addTo(map);

    return () => {
      map.remove();
      mapInstanceRef.current = null;
      // These layers belonged to the removed map. Clear them so the next
      // mount (StrictMode, hot reload) builds fresh ones on the new map.
      geoJsonLayerRef.current = null;
      outlineLayerRef.current = null;
      stateAreaLayerRef.current = null;
      searchHighlightRef.current = null;
      workMarkersRef.current = null;
    };
  }, []);

  // Leaflet's zoom buttons carry English title/aria-label by default; keep
  // them in the selected language (also re-applies on language change).
  useEffect(() => {
    const root = mapRef.current;
    if (!root) return;
    const zin = root.querySelector('.leaflet-control-zoom-in');
    const zout = root.querySelector('.leaflet-control-zoom-out');
    if (zin) { zin.title = t('map.zoomIn'); zin.setAttribute('aria-label', t('map.zoomIn')); }
    if (zout) { zout.title = t('map.zoomOut'); zout.setAttribute('aria-label', t('map.zoomOut')); }
  }, [t]);

  // Seat polygons: built once per boundary file (one Leaflet layer each,
  // with its tooltip, popup and hover handlers). Their style, tooltip and
  // popup read seatCtxRef when they run, so a data, metric, filter or
  // selection change only restyles them (the effect below); nothing is rebuilt.
  useEffect(() => {
    const L = window.L;
    const layer = geoJsonLayerRef.current;
    if (!L || !layer || !mapInstanceRef.current || !geojsonData) return;
    layer.clearLayers();

    (geojsonData.features || []).forEach(feature => {
      const props = feature.properties || {};
      const state = props.dataset_state;
      const constituency = props.dataset_constituency;
      const geoLayer = L.geoJSON(feature, {
        style: feat => seatStyle(seatCtxRef.current, feat.properties || {}),
        onEachFeature: function(feat, l) {
          // Hover text for every area, including those without a value, so
          // no area is silently blank. Built when shown (current language,
          // current data and metric).
          const statusLine = () => {
            const ctx = seatCtxRef.current;
            const { cData, status, metricValue } = seatInfo(ctx, props);
            const metricInfo = METRIC_MODES.find(m => m.key === ctx.mapMetric);
            const L10 = labelsRef.current;
            const tr = L10.t;
            if (status === 'none') return tr(ctx.filtered ? 'map.status.noneFiltered' : 'map.status.none');
            if (status === 'few') {
              return cData.insufficient_data ? tr('map.status.few', { n: MIN_WORKS_FOR_RATE }) : tr('map.status.unrated');
            }
            return (metricInfo ? tr(metricInfo.labelKey) + ': ' : '') + (metricInfo ? metricInfo.format(metricValue, L10.inr) : String(metricValue));
          };
          // Rajya Sabha: the area is the state, so the tooltip names the state.
          const tooltipHtml = () => '<div style="font-family:IBM Plex Sans,sans-serif;font-size:11px;line-height:1.4;">' +
            (seatCtxRef.current.isRS
              ? '<strong>' + escapeHtml(labelsRef.current.state(state)) + '</strong><br>' + escapeHtml(labelsRef.current.t('map.rs.tooltip'))
              : '<strong>' + escapeHtml(constituency) + '</strong><br>' + escapeHtml(state)) +
            '<br>' + escapeHtml(statusLine()) + '</div>';
          l.bindTooltip(tooltipHtml, { sticky: true, direction: 'top' });
          l.on('mouseover', function() { this.setStyle({ weight: 3, color: '#141c33', fillOpacity: 0.85 }); });
          // The style function knows the selection, so a reset keeps it.
          l.on('mouseout', function() { geoLayer.resetStyle(this); });

          // Evaluated when the popup opens (Leaflet accepts a function), so
          // the text always reflects the language and data at that moment.
          const buildPopupHtml = () => {
            const ctx = seatCtxRef.current;
            const { cData, metricValue } = seatInfo(ctx, props);
            // Touch screens have no hover: a tap on an area without works
            // shows the hover text.
            if (!cData) return tooltipHtml();
            const metricInfo = METRIC_MODES.find(m => m.key === ctx.mapMetric);
            const L10 = labelsRef.current;
            const tr = L10.t;
            const formatted = metricInfo ? metricInfo.format(metricValue, L10.inr) : (metricValue != null ? String(metricValue) : '-');
            return '<div style="font-family:IBM Plex Sans,sans-serif;min-width:220px;">' +
              (ctx.isRS
                ? '<div style="font-size:13px;font-weight:700;color:#141c33;margin-bottom:2px;">' + escapeHtml(L10.state(state)) + '</div>' +
                  '<div style="font-size:11px;color:#454e64;margin-bottom:8px;">' + escapeHtml(tr('map.rs.tooltip')) + '</div>'
                : '<div style="font-size:13px;font-weight:700;color:#141c33;margin-bottom:2px;">' + constituency + '</div>' +
                  '<div style="font-size:11px;color:#454e64;margin-bottom:8px;">' + state + '</div>') +
              '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:11px;margin-bottom:8px;">' +
              '<div>' + tr('map.popup.works', { n: '<strong>' + cData.total + '</strong>' }) + '</div>' +
              '<div><strong>' + L10.inr(cData.total_amount) + '</strong></div>' +
              '<div style="color:#a6291f;">' + tr('map.popup.flagged', { n: '<strong>' + cData.flagged + '</strong>' }) + '</div>' +
              '<div>' + (cData.flag_rate != null
                ? tr('map.popup.rate', { pct: '<strong>' + cData.flag_rate + '</strong>' })
                : tr('map.priorityRate') + ': ' + tr('map.rateNA')) + '</div>' +
              '</div>' +
              '<div style="font-size:11px;color:#737d95;border-top:1px solid #dfe2ea;padding-top:6px;margin-bottom:6px;">' +
              '<strong>' + (metricInfo ? tr(metricInfo.labelKey) : '') + ':</strong> ' + formatted +
              '</div>' +
              // No constituency drill-down for Rajya Sabha (state-level figures).
              (ctx.isRS ? '</div>' : '<button onclick="window.__mapSelectConstituency(\'' + state + '\',\'' + constituency.replace(/'/g, "\\'") + '\')" style="' +
              'width:100%;padding:6px 12px;background:#384a8a;color:white;border:none;border-radius:4px;font-size:11px;font-weight:600;cursor:pointer;">' +
              tr('map.popup.explore') + '</button></div>');
          };
          l.bindPopup(buildPopupHtml, { maxWidth: 280 });
        },
      });
      layer.addLayer(geoLayer);
    });
    setSeatsBuilt(n => n + 1);
  }, [geojsonData]);

  // Restyles the seats for the current data, metric, filters and selection,
  // and fits the map once per data load.
  useEffect(() => {
    const L = window.L;
    const layer = geoJsonLayerRef.current;
    if (!L || !layer || !mapInstanceRef.current || !geojsonData) return;

    // Rajya Sabha rows are per state (level=state): every constituency
    // polygon takes its state's figures.
    const dataMap = {};
    constituencyData.forEach(c => {
      dataMap[isRS ? c.State : c.State + '|' + c.Constituency] = c;
    });
    seatCtxRef.current = { dataMap, mapMetric, isRS, filtered: hasActiveFilters, selected: selectedConstituency };

    // Extents of every seat, and of the seats with a row (the matches when filtered).
    const allBounds = [];
    const matchBounds = [];
    layer.eachLayer(geoLayer => {
      geoLayer.resetStyle();
      geoLayer.eachLayer(l => {
        const info = seatInfo(seatCtxRef.current, (l.feature && l.feature.properties) || {});
        // Leaflet sets a path's class only when it is created: keep the
        // status class (map-area-none / -few / -value) current by hand.
        const el = l.getElement && l.getElement();
        if (el) {
          el.classList.remove('map-area-none', 'map-area-few', 'map-area-value');
          el.classList.add('map-area-' + (info.status || 'value'));
        }
        if (l.getBounds) {
          const b = l.getBounds();
          if (b.isValid()) {
            allBounds.push(b);
            if (info.cData) matchBounds.push(b);
          }
        }
      });
    });

    // Fit once per data load (a filter or House change), never on a metric or
    // selection change, and not while a search pick is about to fly to its
    // seat. Filtered: the matching seats (typing one seat's name zooms to
    // it); none matching: the view stays. The first fit is instant.
    if (fitPendingRef.current) {
      fitPendingRef.current = false;
      const list = hasActiveFilters ? matchBounds : allBounds;
      if (list.length > 0 && !selectedConstituency && !pendingPickRef.current) {
        const combined = L.latLngBounds(list[0].getSouthWest(), list[0].getNorthEast());
        for (let i = 1; i < list.length; i++) combined.extend(list[i]);
        if (!fittedOnceRef.current) mapInstanceRef.current.fitBounds(combined, { padding: [30, 30] });
        else flyToArea(combined, 9);
        fittedOnceRef.current = true;
      }
    }
  }, [constituencyData, geojsonData, seatsBuilt, mapMetric, selectedConstituency, hasActiveFilters, isRS, flyToArea]);

  // Display-only outlines. The national outline is the land fill and a thin
  // line; every Admin2 state has a border line. Nothing here takes the pointer.
  useEffect(() => {
    const L = window.L;
    const layer = outlineLayerRef.current;
    if (!L || !layer || !mapInstanceRef.current) return;
    layer.clearLayers();
    if (!outlines) return;
    (outlines.features || []).forEach(feature => {
      const props = feature.properties || {};
      if (props.kind === 'national_outline') {
        L.geoJSON(feature, {
          pane: 'indiaLandPane',
          interactive: false,
          style: () => ({ ...LAND_FILL_STYLE, className: 'map-land' }),
        }).addTo(layer);
        L.geoJSON(feature, {
          pane: 'indiaOutlinePane',
          interactive: false,
          style: () => ({ ...INDIA_OUTLINE_STYLE, fill: false, className: 'map-outline map-outline-national' }),
        }).addTo(layer);
      } else if (props.kind === 'state_border') {
        const border = L.geoJSON(feature, {
          pane: 'stateBorderPane',
          interactive: false,
          style: () => ({ ...STATE_BORDER_STYLE, className: 'map-outline map-outline-state' }),
        }).addTo(layer);
        if (props.name && props.label_lat != null && props.label_lon != null) {
          const b = border.getBounds();
          const small = (b.getNorth() - b.getSouth()) * (b.getEast() - b.getWest()) < SMALL_STATE_DEG2;
          L.marker([props.label_lat, props.label_lon], {
            pane: 'stateLabelPane',
            interactive: false,
            keyboard: false,
            icon: L.divIcon({
              className: 'map-state-label' + (small ? ' map-state-label-small' : ''),
              // Names are in the seat and state tooltips; the label is visual only.
              html: '<span aria-hidden="true">' + escapeHtml(props.name) + '</span>',
              iconSize: null,
            }),
          }).addTo(layer);
        }
      }
    });
  }, [outlines]);

  // State-level figures for the re-delimited states' shapes, by state name.
  // null: not available (request failed, or an API without level=state).
  const stateLevelRows = useMemo(() => {
    const src = isRS ? constituencyData : stateRows;
    if (!Array.isArray(src)) return null;
    const rows = src.filter(r => r.level === 'state');
    if (src.length > 0 && rows.length === 0) return null;
    return Object.fromEntries(rows.map(r => [r.State, r]));
  }, [isRS, constituencyData, stateRows]);

  // Marker point of each re-delimited state: computed by the server inside
  // the state's shape (never a geocode); the state's works share it.
  // The re-delimited states' shapes to draw: every state the coverage data
  // lists as re-delimited, with its shape from the outlines (the static file,
  // or the API). A listed state the outlines have no such shape for is drawn
  // as its Admin2 border shape, its works at the border's label point (the
  // visual centre of its largest part, inside it). If the coverage data
  // failed to load, the outlines' own re-delimited shapes are drawn.
  const redelimitedAreas = useMemo(() => {
    const feats = (outlines && outlines.features) || [];
    const shapes = feats.filter(f => f.properties && f.properties.kind === 'redelimited_state');
    if (!coverage) return loadErrors.coverage ? shapes : [];
    return redelimitedStates.map(st => {
      const own = shapes.find(f => f.properties.state === st);
      if (own) return own;
      const want = normState(st);
      const border = feats.find(f => f.properties && f.properties.kind === 'state_border' && normState(f.properties.name) === want);
      if (!border) return null;
      const bp = border.properties;
      return {
        type: 'Feature',
        geometry: border.geometry,
        properties: { kind: 'redelimited_state', state: st, marker_lat: bp.label_lat, marker_lon: bp.label_lon },
      };
    }).filter(Boolean);
  }, [outlines, coverage, redelimitedStates, loadErrors.coverage]);

  const redelimitedPoints = useMemo(() => {
    const out = {};
    redelimitedAreas.forEach(f => {
      const p = f.properties || {};
      if (p.marker_lat != null && p.marker_lon != null) out[p.state] = [p.marker_lat, p.marker_lon];
    });
    return out;
  }, [redelimitedAreas]);

  // Re-delimited states (their seats have no boundaries): one Admin2 state
  // shape each, coloured by the state-level figure on the rate metrics.
  useEffect(() => {
    const L = window.L;
    const layer = stateAreaLayerRef.current;
    if (!L || !layer || !mapInstanceRef.current) return;
    layer.clearLayers();
    stateAreaBoundsRef.current = {};
    if (!redelimitedAreas.length) return;
    const filtered = hasActiveFilters;
    const metricInfo = METRIC_MODES.find(m => m.key === mapMetric);
    const comparable = RATE_METRICS.has(mapMetric);
    redelimitedAreas.forEach(feature => {
      const props = feature.properties || {};
      const st = props.state;
      const row = stateLevelRows ? stateLevelRows[st] : null;
      const value = row ? getMetricValue(row, mapMetric) : null;
      // value: coloured. none: no works (or none match the filters).
      // few: under the small-number rule. notComparable: a count or sum.
      // unavailable: the state-level figures could not be loaded.
      const status = !stateLevelRows ? 'unavailable'
        : !row ? 'none'
        : !comparable ? 'notComparable'
        : (value == null || isNaN(value)) ? 'few' : 'value';
      const fill = status === 'value'
        ? { fillColor: getColorForValue(value, mapMetric), fillOpacity: 0.7 }
        : status === 'none' || status === 'few'
        ? { fillColor: AREA_STATUS_STYLE[status].fillColor, fillOpacity: AREA_STATUS_STYLE[status].fillOpacity }
        : { fillColor: AREA_STATUS_STYLE.redelimited.fillColor, fillOpacity: 0.85 };
      const isSelected = !!(selectedConstituency && selectedConstituency.stateLevel && selectedConstituency.state === st);
      const lines = () => {
        const L10 = labelsRef.current;
        const tr = L10.t;
        const label = metricInfo ? tr(metricInfo.labelKey) + ': ' : '';
        const metricLine = status === 'value' ? label + metricInfo.format(value, L10.inr)
          : status === 'few' ? tr('map.status.few', { n: MIN_WORKS_FOR_RATE })
          : status === 'notComparable' ? label + tr('map.rs.notComparable')
          : status === 'none' ? tr(filtered ? 'map.status.noneFiltered' : 'map.status.none')
          : tr('map.stateLevel.unavailable');
        return '<strong>' + escapeHtml(L10.state(st)) + '</strong> &middot; ' + escapeHtml(tr('map.stateLevel.legend')) + '<br>' +
          escapeHtml(tr('map.stateLevel.note')) + '<br>' + escapeHtml(metricLine) +
          (row ? '<br>' + tr('map.popup.works', { n: '<strong>' + row.total + '</strong>' }) : '');
      };
      const tooltipHtml = () => '<div style="font-family:IBM Plex Sans,sans-serif;font-size:11px;line-height:1.4;max-width:260px;">' + lines() + '</div>';
      const popupHtml = () => '<div style="font-family:IBM Plex Sans,sans-serif;font-size:11px;line-height:1.4;max-width:260px;">' + lines() +
        // Lok Sabha only (Rajya Sabha has no drill-down), and only with works.
        (!isRS && row ? '<button onclick="window.__mapSelectState(\'' + st.replace(/'/g, "\\'") + '\')" style="' +
          'width:100%;margin-top:8px;padding:6px 12px;background:#384a8a;color:white;border:none;border-radius:4px;font-size:11px;font-weight:600;cursor:pointer;">' +
          escapeHtml(labelsRef.current.t('map.stateLevel.explore')) + '</button>' : '') + '</div>';
      const shapeLayer = L.geoJSON(feature, {
        style: () => ({
          ...fill,
          color: STATE_AREA_LINE.color,
          weight: isSelected ? 3 : STATE_AREA_LINE.weight,
          dashArray: STATE_AREA_LINE.dashArray,
          opacity: 1,
          // not .map-area: that class is the seat polygons
          className: 'map-outline map-outline-redelimited map-state-area map-state-area-' + status,
        }),
        onEachFeature: (feat, l) => {
          l.bindTooltip(tooltipHtml, { sticky: true, direction: 'top' });
          // Touch screens have no hover: a tap shows the same text.
          l.bindPopup(popupHtml, { maxWidth: 280 });
        },
      }).addTo(layer);
      stateAreaBoundsRef.current[st] = shapeLayer.getBounds();
      // Below every seat polygon (they share the overlay pane), so no seat is hidden by it.
      shapeLayer.bringToBack();
    });
  }, [redelimitedAreas, stateLevelRows, mapMetric, hasActiveFilters, isRS, selectedConstituency]);

  useEffect(() => {
    if (!mapInstanceRef.current) return;
    const L = window.L;
    if (!L) return;

    if (!workMarkersRef.current) {
      // Every work in a constituency shares one point, so its works are one
      // bubble at every zoom (zooming never splits it). The plugin's own click
      // handling is off and nothing here touches React state: a bubble click
      // (or Enter) flies in to SPIDER_ZOOM when the map is further out, then
      // spreads the bubble out (spiderfies); a single work is a small tier dot
      // that opens only its own popup. The works list is opened only by the
      // panel button, never from the map.
      const map = mapInstanceRef.current;
      const group = L.markerClusterGroup({
        // Thousands of markers are added in chunks, without blocking the page.
        chunkedLoading: true,
        zoomToBoundsOnClick: false,
        spiderfyOnMaxZoom: false,
        showCoverageOnHover: false,
        // Colour each bubble by the worst tier among its child markers, using
        // the tier already carried on every marker (no extra data fetched).
        iconCreateFunction: function(cluster) {
          const counts = { CRITICAL: 0, HIGH: 0, MODERATE: 0, LOW: 0, NONE: 0 };
          cluster.getAllChildMarkers().forEach(function(m) {
            const tier = m.options.tier;
            counts[PRIORITY_COLORS[tier] ? tier : 'NONE'] += 1;
          });
          const worst = TIER_ORDER.find(k => counts[k] > 0) || 'NONE';
          const fill = PRIORITY_COLORS[worst] || NO_TIER_COLOR;
          const L10 = labelsRef.current;
          const parts = TIER_ORDER.filter(k => counts[k] > 0).map(k => L10.riskShort(k) + ' ' + counts[k]);
          if (counts.NONE > 0) parts.push(L10.riskShort('NOT_EVALUATED') + ' ' + counts.NONE);
          const total = cluster.getChildCount();
          const title = String(total) + ': ' + parts.join(', ');
          return L.divIcon({
            html: '<div title="' + title.replace(/"/g, '&quot;') + '" style="width:34px;height:34px;border-radius:50%;background:' + fill + ';color:' + CLUSTER_TEXT[worst] + ';border:3px solid white;box-shadow:0 1px 4px rgba(0,0,0,0.35);display:flex;align-items:center;justify-content:center;font:700 12px IBM Plex Sans,sans-serif;">' + total + '</div>',
            className: 'leaflet-marker-cluster',
            iconSize: [34, 34],
          });
        },
      });
      // Cap the spiral: above SPIDERFY_CAP the bubble hands spiderfy() (which
      // reads its getAllChildMarkers) only its highest-risk markers; markers
      // left out stay inside the bubble, and the plugin's unspiderfy skips them.
      const spiderfyCapped = (cluster) => {
        if (cluster.getChildCount() <= SPIDERFY_CAP) { cluster.spiderfy(); return; }
        const top = cluster.getAllChildMarkers()
          .sort((a, b) => compareWorks(a.options.work, b.options.work))
          .slice(0, SPIDERFY_CAP);
        cluster.getAllChildMarkers = () => top.slice();
        try { cluster.spiderfy(); } finally { delete cluster.getAllChildMarkers; }
      };
      group.on('clusterclick clusterkeypress', (e) => {
        if (e.type === 'clusterkeypress' && !(e.originalEvent && e.originalEvent.keyCode === 13)) return;
        const cluster = e.layer;
        if (map.getZoom() >= SPIDER_ZOOM) { spiderfyCapped(cluster); return; }
        // The plugin rebuilds its bubbles after a zoom: spread out the one
        // that holds the same markers once its zoom animation has finished
        // ('animationend'; the timer covers a non-animated rebuild).
        const anchor = cluster.getAllChildMarkers()[0];
        let timer = null;
        const spread = () => {
          group.off('animationend', spread);
          clearTimeout(timer);
          const parent = group.getVisibleParent(anchor);
          if (parent && parent !== anchor && parent.spiderfy) spiderfyCapped(parent);
        };
        map.once('moveend', () => {
          group.on('animationend', spread);
          timer = setTimeout(spread, 600);
        });
        map.flyTo(cluster.getLatLng(), SPIDER_ZOOM, { duration: 0.6 });
      });
      group.on('spiderfied', (e) => {
        const total = e.cluster.getChildCount();
        setSpiderCap(e.markers.length < total ? { shown: e.markers.length, total } : null);
      });
      group.on('unspiderfied', () => setSpiderCap(null));
      workMarkersRef.current = group.addTo(map);
    }
    workMarkersRef.current.clearLayers();
    setSpiderCap(null);

    if (!showWorkMarkers || works.length === 0) return;

    const iconCache = {};
    function getDotIcon(color) {
      if (!iconCache[color]) {
        iconCache[color] = L.divIcon({
          html: '<div style="width:10px;height:10px;background:' + color + ';border-radius:50%;border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.3);"></div>',
          className: '',
          iconSize: [10, 10],
          iconAnchor: [5, 5],
        });
      }
      return iconCache[color];
    }

    const batch = [];
    works.forEach(function(work) {
      // A re-delimited seat's work has no stored point: it is shown at its
      // state's computed marker point (state-level representation).
      var stateLevel = work.latitude == null || work.longitude == null;
      var pos = stateLevel ? redelimitedPoints[work.state] : [work.latitude, work.longitude];
      if (!pos) return;
      var priority = work.risk_level || work.priority || 'NOT_EVALUATED';
      var color = PRIORITY_COLORS[priority] || '#b0b8c8';
      var marker = L.marker(pos, { icon: getDotIcon(color), tier: priority, work });
      var riskScore = work.risk_score != null ? (work.risk_score * 100).toFixed(0) : '-';
      var buildPopupContent = function() {
        var L10 = labelsRef.current;
        var tr = L10.t;
        return '<div style="font-family:IBM Plex Sans,sans-serif;min-width:220px;max-width:300px;">' +
          '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">' +
          '<span style="display:inline-block;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:600;background:' + color + '15;color:' + color + ';border:1px solid ' + color + '30;">' + String(L10.riskShort(priority)).toUpperCase() + '</span>' +
          '<span style="font-size:10px;color:#737d95;">' + tr('map.popup.risk', { score: riskScore }) + '</span></div>' +
          '<div style="font-size:13px;font-weight:600;color:#141c33;margin-bottom:4px;line-height:1.4;">' + (work.description || tr('common.noDescription')) + '</div>' +
          '<div style="font-size:11px;color:#454e64;margin-bottom:6px;">' +
          '<div><strong>' + tr('common.mp') + ':</strong> ' + work.mp + '</div>' +
          '<div><strong>' + tr('common.amount') + ':</strong> ' + L10.inr(work.amount) + ' | <strong>' + tr('common.stage') + ':</strong> ' + L10.stageUpper(work.stage) + '</div>' +
          '</div>' +
          '<div style="font-size:10px;color:#9aa2b6;margin-bottom:6px;">' + tr('map.popup.location') + ': ' +
          (stateLevel ? tr('map.popup.locStateLevel') : work.location_level === 'CONSTITUENCY' ? tr('map.popup.locConst') : work.location_level === 'STATE' ? tr('map.popup.locState') : (work.location_level || tr('map.popup.locUnknown'))) + '</div>' +
          '<button onclick="window.__mapNavigate(\'/record/' + work.record_id + '\')" style="' +
          'width:100%;padding:6px 12px;background:#384a8a;color:white;border:none;border-radius:4px;font-size:11px;font-weight:600;cursor:pointer;">' +
          tr('map.popup.view') + '</button></div>';
      };
      // A dot opens only this popup; it changes no React state.
      marker.bindPopup(buildPopupContent, { maxWidth: 320 });
      batch.push(marker);
    });
    // One call: the plugin clusters the whole batch once (adding markers one
    // by one re-clusters after each).
    workMarkersRef.current.addLayers(batch);
  }, [works, showWorkMarkers, redelimitedPoints]);

  const worksListRows = useMemo(() => works.slice().sort(compareWorks), [works]);

  useEffect(() => {
    if (worksListOpen && worksListRef.current && worksListRef.current.scrollIntoView) {
      worksListRef.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [worksListOpen]);

  const nationalStats = useMemo(() => {
    const total = constituencyData.reduce((s, c) => s + c.total, 0);
    const critical = constituencyData.reduce((s, c) => s + c.critical, 0);
    const high = constituencyData.reduce((s, c) => s + c.high, 0);
    const moderate = constituencyData.reduce((s, c) => s + c.moderate, 0);
    const low = constituencyData.reduce((s, c) => s + c.low, 0);
    const totalAmount = constituencyData.reduce((s, c) => s + (c.total_amount || 0), 0);
    const flagged = critical + high;
    const priorityRate = total > 0 ? (flagged / total * 100).toFixed(1) : 0;
    return { total, critical, high, moderate, low, flagged, totalAmount, priorityRate };
  }, [constituencyData]);

  const legendItems = useMemo(
    () => getLegendItems(mapMetric, t, hasActiveFilters, MIN_WORKS_FOR_RATE,
      new Set([
        ...((outlines && outlines.features) || []).filter(f => f.properties && f.properties.kind !== 'redelimited_state')
          .map(f => f.properties.kind),
        ...(redelimitedAreas.length ? ['redelimited_state'] : []),
      ])),
    [mapMetric, t, hasActiveFilters, outlines, redelimitedAreas]
  );

  // Seats with no boundary on this map (from /api/geographic-coverage,
  // already loaded), grouped by state for the coverage note.
  const noBoundaryByState = useMemo(() => {
    const out = {};
    ((coverage && coverage.no_boundary) || []).forEach(a => {
      (out[a.state] = out[a.state] || []).push(a.name);
    });
    return Object.entries(out).sort(([a], [b]) => a.localeCompare(b));
  }, [coverage]);
  const noBoundaryCount = noBoundaryByState.reduce((n, [, names]) => n + names.length, 0);
  const noBoundaryAllRedelimited = ((coverage && coverage.no_boundary) || [])
    .every(a => a.reason === 'redelimited_boundary_not_available');
  const currentMetricInfo = METRIC_MODES.find(m => m.key === mapMetric);
  const selectedStateRow = selectedConstituency && selectedConstituency.stateLevel && stateLevelRows
    ? stateLevelRows[selectedConstituency.state] : null;
  const worksDisabled = worksLoading || works.length === 0;
  const worksButtons = (
    <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
      {/* Opens the list of the works already loaded; no request. */}
      <button
        data-testid="map-works-button"
        aria-expanded={worksListOpen}
        disabled={worksDisabled}
        onClick={() => {
          setShowWorkMarkers(true);
          if (worksListOpen) setWorksListOpen(false); else openWorksList();
        }}
        style={{ flex: 1, padding: '6px 10px', background: 'var(--indigo-600)', color: 'white', border: 'none', borderRadius: 'var(--radius-md)', fontSize: 10.5, fontWeight: 600, cursor: worksDisabled ? 'default' : 'pointer', opacity: worksDisabled ? 0.6 : 1, whiteSpace: 'nowrap' }}>
        {worksLoading ? t('map.worksList.loading')
          : works.length === 0 ? t('map.worksList.none')
          : worksListOpen ? t('map.worksList.hide')
          : t('map.worksList.show', { n: works.length })}
      </button>
      <button onClick={resetToNational} style={{ padding: '6px 10px', background: 'var(--bg-subtle)', color: 'var(--text-secondary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', fontSize: 10.5, fontWeight: 600, cursor: 'pointer' }}>
        {t('common.reset')}
      </button>
    </div>
  );

  return (
    <div className="map-shell">
      {sidebarOpen ? (
        <div className="map-floating-panel">
          <div className="map-panel-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <h3>{t('map.title')}</h3>
              <p>{t('map.subtitle')}</p>
            </div>
            <button onClick={() => setSidebarOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-on-dark-muted)', padding: 4, flexShrink: 0 }} aria-label={t('map.collapse')}>
              <ChevronDown size={16} />
            </button>
          </div>

          <div className="map-panel-body">
            {isRS && <HouseNotApplicable feature={t('house.na.mapDrilldown')} />}
            {isRS && (
              <div data-testid="map-rs-note" role="note" style={{ fontSize: 11, color: 'var(--text-secondary)', background: 'var(--indigo-subtle)', border: '1px solid var(--indigo-border)', borderRadius: 'var(--radius-md)', padding: '8px 10px', marginBottom: 12, lineHeight: 1.45 }}>
                <strong>{t('map.rs.note')}</strong> {t('map.rs.noteBasis')}
              </div>
            )}
            {isRS && !loading && !loadErrors.data && !hasActiveFilters && constituencyData.length === 0 && (
              <div data-testid="map-rs-unavailable" role="status" style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 12 }}>
                {t('map.rs.unavailable')}
              </div>
            )}
            {loadErrors.data && <ErrorState compact what={t('map.what.data')} error={loadErrors.data} onRetry={retryMapData} />}
            {loadErrors.boundaries && <ErrorState compact what={t('map.what.boundaries')} error={loadErrors.boundaries} onRetry={retryMapData} />}
            {loadErrors.coverage && <ErrorState compact what={t('map.what.coverage')} error={loadErrors.coverage} onRetry={retryMapData} />}
            {loadErrors.filters && <ErrorState compact what={t('map.what.filters')} error={loadErrors.filters} onRetry={loadFilters} />}
            {loadErrors.lists && <ErrorState compact what={t('map.what.lists')} error={loadErrors.lists} onRetry={loadLists} />}
            {loadErrors.works && <ErrorState compact what={t('map.what.works')} error={loadErrors.works} />}
            {selectedConstituency && loadErrors.intel && (
              <ErrorState compact what={t('map.what.intel')} error={loadErrors.intel}
                onRetry={() => selectConstituency(selectedConstituency.state, selectedConstituency.constituency)} />
            )}
            {selectedConstituency && (
              <div style={{ marginBottom: 16 }}>
                <button onClick={resetToNational} style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'var(--indigo-subtle)', border: '1px solid var(--indigo-border)', borderRadius: 'var(--radius-md)', padding: '8px 12px', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: 'var(--indigo-600)', width: '100%', justifyContent: 'center', marginBottom: 12 }}>
                  <RotateCcw size={13} /> {t('map.resetNational')}
                </button>
              </div>
            )}

            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                <Filter size={13} />
                <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--shell-800)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{t('common.filters')}</span>
                {hasActiveFilters && (
                  <button onClick={clearFilters} style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--gov-blue)', fontSize: 11, cursor: 'pointer', fontWeight: 600 }}>{t('common.clearAll')}</button>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div className="toolbar-group">
                  <span className="toolbar-label">{t('common.state')}</span>
                  <select className="toolbar-select" style={{ width: '100%' }} value={activeFilters.state} onChange={(e) => handleFilterChange('state', e.target.value)}>
                    <option value="">{t('common.allStates')}</option>
                    {(filterOptions.states || []).map(s => <option key={s} value={s}>{labels.state(s)}</option>)}
                  </select>
                </div>
                <div className="toolbar-group">
                  <span className="toolbar-label">{t('common.riskLevel')}</span>
                  <select className="toolbar-select" style={{ width: '100%' }} value={activeFilters.priority} onChange={(e) => handleFilterChange('priority', e.target.value)}>
                    <option value="">{t('common.allRisk')}</option>
                    <option value="CRITICAL">{t('risk.critical')}</option>
                    <option value="HIGH">{t('risk.high')}</option>
                    <option value="MODERATE">{t('risk.moderate')}</option>
                    <option value="LOW">{t('risk.low')}</option>
                  </select>
                </div>
                <div className="toolbar-group">
                  <span className="toolbar-label">{t('common.stage')}</span>
                  <select className="toolbar-select" style={{ width: '100%' }} value={activeFilters.stage} onChange={(e) => handleFilterChange('stage', e.target.value)}>
                    <option value="">{t('common.allStages')}</option>
                    {(filterOptions.stages || []).map(s => <option key={s} value={s}>{labels.stageUpper(s)}</option>)}
                  </select>
                </div>
                <div className="toolbar-search" style={{ minWidth: 0 }} ref={searchBoxRef}>
                  <Search size={14} />
                  <input type="text" className="toolbar-input" style={{ width: '100%' }} placeholder={t('map.searchPlaceholder')} value={searchInput} onChange={(e) => { setSearchInput(e.target.value); setSuggestOpen(true); setSuggestHighlight(-1); setSearchNote(null); }} onFocus={() => searchInput.trim().length >= 2 && setSuggestOpen(true)} onKeyDown={handleSearchKeyDown} autoComplete="off" />
                  {suggestOpen && suggestions.length > 0 && searchBoxRef.current && createPortal(
                    <div data-map-search-suggestions style={{ position: 'fixed', top: searchBoxRef.current.getBoundingClientRect().bottom + 4, left: searchBoxRef.current.getBoundingClientRect().left, width: searchBoxRef.current.getBoundingClientRect().width, background: '#fff', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-lg)', zIndex: 5000, maxHeight: 260, overflowY: 'auto' }}>
                      {suggestions.map((s, i) => (
                        <div key={s.type + '-' + (s.state || '') + '-' + s.name} data-testid="map-search-suggestion" data-type={s.type} data-state={s.state || undefined} onMouseEnter={() => setSuggestHighlight(i)} onClick={() => selectSuggestion(s)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '8px 12px', cursor: 'pointer', fontSize: 12.5, background: i === suggestHighlight ? 'var(--bg-hover)' : 'transparent', borderBottom: i < suggestions.length - 1 ? '1px solid var(--border-light)' : 'none' }}>
                          <span style={{ color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                          <span style={{ flexShrink: 0, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.3, color: 'var(--text-faint)' }}>{s.type === 'MP' ? t('common.mp') : `${t('common.constituency')} · ${labels.state(s.state)}`}</span>
                        </div>
                      ))}
                    </div>,
                    document.body
                  )}
                </div>
                {searchNote && (
                  <div data-testid="map-search-note" role="status" style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    {t(searchNote.key, searchNote.vars)}
                  </div>
                )}
              </div>
            </div>

            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--shell-800)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.4 }}>{t('map.metricView')}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {METRIC_MODES.map(m => {
                  // Rajya Sabha: counts and sums of whole states are not
                  // comparable with the constituency bands, so only the
                  // rate-type metrics are offered.
                  const off = isRS && !RATE_METRICS.has(m.key);
                  return (
                    <button key={m.key} data-metric={m.key} aria-pressed={mapMetric === m.key} disabled={off} onClick={() => setMapMetric(m.key)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: mapMetric === m.key ? 'var(--indigo-subtle)' : 'transparent', border: mapMetric === m.key ? '1px solid var(--indigo-border)' : '1px solid transparent', borderRadius: 'var(--radius-md)', cursor: off ? 'not-allowed' : 'pointer', opacity: off ? 0.55 : 1, textAlign: 'left', transition: 'all 150ms ease' }}>
                      <BarChart3 size={13} style={{ color: mapMetric === m.key ? 'var(--indigo-600)' : 'var(--text-muted)', flexShrink: 0 }} />
                      <div>
                        <div style={{ fontSize: 11.5, fontWeight: 600, color: mapMetric === m.key ? 'var(--indigo-700)' : 'var(--text-secondary)' }}>{t(m.labelKey)}</div>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{off ? t('map.rs.notComparable') : t(m.descKey)}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Computed from the map data: when that failed to load, there
                are no figures to show (not zeros). */}
            {!loadErrors.data && (<>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--shell-800)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.4 }}>
              {t('map.nationalSummary', { count: nationalStats.total.toLocaleString('en-IN') })}
            </div>
            <div className="map-stat-strip">
              <div className="map-stat-cell">
                <div className="n" style={{ color: 'var(--risk-high)' }}>{nationalStats.critical}</div>
                <div className="l">{t('dash.risk.critical')}</div>
              </div>
              <div className="map-stat-cell">
                <div className="n" style={{ color: '#c45a20' }}>{nationalStats.high}</div>
                <div className="l">{t('dash.risk.high')}</div>
              </div>
              <div className="map-stat-cell">
                <div className="n" style={{ color: 'var(--risk-review)' }}>{nationalStats.moderate}</div>
                <div className="l">{t('dash.risk.moderate')}</div>
              </div>
              <div className="map-stat-cell">
                <div className="n" style={{ color: 'var(--risk-low)' }}>{nationalStats.low}</div>
                <div className="l">{t('dash.risk.low')}</div>
              </div>
            </div>
            <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
              {t('map.summaryLine', { rate: nationalStats.priorityRate, amount: labels.inr(nationalStats.totalAmount) })}
            </div>
            </>)}

            {/* States whose seats have no boundaries (re-delimited), from the
                coverage data. Neutral tint, never a risk colour; text in
                --text-secondary (#454e64 on #f6f7fa: about 7.7:1). */}
            {redelimitedStates.length > 0 && (
              <div data-testid="map-state-level-note" role="note"
                style={{ display: 'flex', gap: 8, alignItems: 'flex-start', background: 'var(--bg-subtle)', border: '1px solid var(--border-light)', borderRadius: 10, padding: '10px 12px', marginBottom: 12 }}>
                <Info size={14} aria-hidden="true" style={{ color: 'var(--text-secondary)', flexShrink: 0, marginTop: 1 }} />
                <div style={{ fontSize: 11, lineHeight: 1.45, color: 'var(--text-secondary)' }}>
                  <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2 }}>
                    {redelimitedStates.map(st => labels.state(st)).join(', ')}
                  </div>
                  {t(redelimitedStates.length > 1 ? 'map.stateLevel.notePlural' : 'map.stateLevel.note')}
                </div>
              </div>
            )}

            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--shell-800)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.4 }}>{t('map.legend')}</div>
              <div data-testid="map-legend" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {legendItems.map(({ label, color, status }) => {
                  const look = status && AREA_STATUS_STYLE[status];
                  const swatch = status === 'national'
                    ? { height: 0, borderRadius: 0, boxShadow: 'none', borderTop: `1.5px solid ${INDIA_OUTLINE_STYLE.color}` }
                    : status === 'stateLevel'
                    ? { background: 'transparent', boxShadow: 'none', border: `2px dashed ${STATE_AREA_LINE.color}` }
                    : look
                    ? { background: look.fillColor, opacity: Math.max(look.fillOpacity, 0.6), border: `1.5px ${LEGEND_BORDER[status] || 'dotted'} ${look.color}` }
                    : { background: color, border: '1px solid rgba(255,255,255,0.8)' };
                  return (
                    <div key={label} data-status={status || undefined} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ width: 14, height: 10, borderRadius: 2, boxShadow: '0 1px 2px rgba(0,0,0,0.15)', flexShrink: 0, ...swatch }} />
                      <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{label}</span>
                    </div>
                  );
                })}
              </div>
            </div>

            {outlinesSlow && !outlines && !outlinesFailed && (
              <div data-testid="map-outlines-loading" role="status" style={{ fontSize: 10.5, color: 'var(--text-secondary)', marginBottom: 8 }}>
                {t('map.outlines.loading')}
              </div>
            )}
            {outlinesFailed && (
              <div data-testid="map-outlines-failed" role="status" style={{ fontSize: 10.5, color: 'var(--text-secondary)', marginBottom: 8 }}>
                {t('map.outlines.failed')}
              </div>
            )}

            {coverage && (
              <div style={{ borderTop: '1px solid var(--border-light)', paddingTop: 10, marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--shell-800)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.4 }}>{t('map.coverage')}</div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                  <div>{t('map.coverageReal', { pct: coverage.real_boundary_pct })}</div>
                  <div>{t('map.coverageApprox', { pct: coverage.centroid_fallback_pct })}</div>
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.4, fontStyle: 'italic' }}>
                  {t('map.coverageNote')}
                </div>
                {noBoundaryCount > 0 && (
                  <div data-testid="map-no-boundary" style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.5 }}>
                    <div>
                      <strong>{t('map.noBoundary.count', { n: noBoundaryCount })}</strong>{' '}
                      {t('map.noBoundary.states', { states: noBoundaryByState.map(([st, names]) => `${labels.state(st)} (${names.length})`).join(', ') })}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                      {t(noBoundaryAllRedelimited ? 'map.noBoundary.reason' : 'map.noBoundary.reasonOther')}
                    </div>
                    <details style={{ marginTop: 4 }}>
                      <summary style={{ cursor: 'pointer', fontSize: 10.5, color: 'var(--indigo-600)', fontWeight: 600 }}>{t('map.noBoundary.list')}</summary>
                      {noBoundaryByState.map(([st, names]) => (
                        <div key={st} style={{ fontSize: 10.5, marginTop: 4 }}>
                          <span style={{ fontWeight: 600 }}>{labels.state(st)}:</span> {names.slice().sort().join(', ')}
                        </div>
                      ))}
                    </details>
                  </div>
                )}
              </div>
            )}

            {selectedConstituency && constituencyIntel && (
              <div style={{ borderTop: '1px solid var(--border-light)', paddingTop: 12, marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--shell-800)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.4 }}>{t('map.intel')}</div>
                <div style={{ background: 'var(--bg-subtle)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', padding: '12px 14px' }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--navy-800)', marginBottom: 2 }}>{constituencyIntel.constituency}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>{constituencyIntel.state}</div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 12px', fontSize: 11, marginBottom: 10 }}>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('dash.kpi.totalWorks')}:</span> <strong>{constituencyIntel.total_works}</strong></div>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('common.amount')}:</span> <strong>{labels.inr(constituencyIntel.total_amount)}</strong></div>
                    <div style={{ color: 'var(--risk-high)' }}><span style={{ color: 'var(--text-muted)' }}>{t('map.highCritical')}:</span> <strong>{constituencyIntel.flagged_count}</strong></div>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('map.priorityRate')}:</span> <strong>{constituencyIntel.priority_rate != null ? `${constituencyIntel.priority_rate}%` : t('map.rateNA')}</strong></div>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('map.avgRisk')}:</span> <strong>{constituencyIntel.average_risk != null ? constituencyIntel.average_risk : '—'}</strong></div>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('map.avgConfidence')}:</span> <strong>{constituencyIntel.average_confidence != null ? `${constituencyIntel.average_confidence}%` : '—'}</strong></div>
                    <div style={{ gridColumn: '1 / -1' }}><span style={{ color: 'var(--text-muted)' }}>{t('map.metric.exposure')}:</span> <strong>{labels.inr(constituencyIntel.financial_exposure)}</strong></div>
                  </div>

                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', borderTop: '1px solid var(--border-light)', paddingTop: 8, marginBottom: 8 }}>
                    <strong>{t('map.why')}</strong><br/>
                    {constituencyIntel.priority_rate == null
                      ? t('map.why.insufficient', { n: constituencyIntel.min_works_for_rate ?? 10 })
                      : constituencyIntel.priority_rate >= 10
                      ? t('map.why.high')
                      : constituencyIntel.priority_rate >= 5
                      ? t('map.why.mid')
                      : t('map.why.low')}
                    {' '}
                    {t('map.why.count', { flagged: constituencyIntel.flagged_count, total: constituencyIntel.total_works })}
                    {constituencyIntel.signal_summary && constituencyIntel.signal_summary.length > 1 && ` ${t('map.why.multi')}`}
                  </div>

                  {constituencyIntel.signal_summary && constituencyIntel.signal_summary.length > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.3 }}>{t('map.topSignals')}</div>
                      {constituencyIntel.signal_summary.slice(0, 4).map(s => (
                        <div key={s.signal} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-secondary)', padding: '2px 0' }}>
                          <span>{labels.signal(s.signal)}</span>
                          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{t('map.highN', { n: s.high_count })}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {worksButtons}
                </div>
              </div>
            )}

            {selectedConstituency && selectedConstituency.stateLevel && (
              <div data-testid="map-state-level-card" style={{ borderTop: '1px solid var(--border-light)', paddingTop: 12, marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--shell-800)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.4 }}>{t('map.stateLevel.legend')}</div>
                <div style={{ background: 'var(--bg-subtle)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', padding: '12px 14px' }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--navy-800)', marginBottom: 6 }}>{labels.state(selectedConstituency.state)}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45, marginBottom: 10 }}>{t('map.stateLevel.note')}</div>
                  {selectedStateRow && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 12px', fontSize: 11, marginBottom: 10 }}>
                      <div><span style={{ color: 'var(--text-secondary)' }}>{t('dash.kpi.totalWorks')}:</span> <strong>{selectedStateRow.total}</strong></div>
                      <div><span style={{ color: 'var(--text-secondary)' }}>{t('common.amount')}:</span> <strong>{labels.inr(selectedStateRow.total_amount)}</strong></div>
                      <div><span style={{ color: 'var(--text-secondary)' }}>{t('map.highCritical')}:</span> <strong>{selectedStateRow.flagged}</strong></div>
                      <div><span style={{ color: 'var(--text-secondary)' }}>{t('map.priorityRate')}:</span> <strong>{selectedStateRow.flag_rate != null ? `${selectedStateRow.flag_rate}%` : t('map.rateNA')}</strong></div>
                    </div>
                  )}
                  {worksButtons}
                </div>
              </div>
            )}

            {selectedConstituency && worksListOpen && (
              <div ref={worksListRef} data-testid="map-works-list" style={{ borderTop: '1px solid var(--border-light)', paddingTop: 12, marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--shell-800)', textTransform: 'uppercase', letterSpacing: 0.4 }}>
                    {t('map.worksList.title', { n: worksListRows.length })}
                  </div>
                  <button onClick={() => setWorksListOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 2 }} aria-label={t('map.worksList.close')} title={t('map.worksList.close')}>
                    <X size={12} />
                  </button>
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8, lineHeight: 1.4 }}>
                  {t('map.worksList.sorted')}{hasActiveFilters ? ' ' + t('map.worksList.filtered') : ''}
                </div>
                <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {worksListRows.slice(0, worksListLimit).map(w => (
                    <li key={w.record_id} data-testid="map-works-row" data-tier={w.risk_level || w.priority || 'NOT_EVALUATED'} data-amount={w.amount ?? ''}
                      style={{ background: 'var(--bg-subtle)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', padding: '8px 10px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
                        <RiskBadge priority={w.risk_level || w.priority} />
                        <StageBadge stage={w.stage} />
                        <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 600, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{labels.inr(w.amount)}</span>
                      </div>
                      <div style={{ fontSize: 11.5, color: 'var(--text-primary)', lineHeight: 1.4, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', marginBottom: 4 }}>
                        {w.description || t('common.noDescription')}
                      </div>
                      <Link to={`/record/${encodeURIComponent(w.record_id)}`} style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--indigo-600)' }}>
                        {t('map.worksList.view')} →
                      </Link>
                    </li>
                  ))}
                </ul>
                {worksListRows.length > worksListLimit && (
                  <button data-testid="map-works-more" onClick={() => setWorksListLimit(l => l + WORKS_LIST_PAGE)}
                    style={{ width: '100%', marginTop: 8, padding: '6px 10px', background: 'var(--bg-subtle)', color: 'var(--indigo-600)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', fontSize: 10.5, fontWeight: 600, cursor: 'pointer' }}>
                    {t('map.worksList.more', {
                      n: Math.min(WORKS_LIST_PAGE, worksListRows.length - worksListLimit),
                      left: worksListRows.length - worksListLimit,
                    })}
                  </button>
                )}
              </div>
            )}

            {!selectedConstituency && !isRS && (
              <div style={{ borderTop: '1px solid var(--border-light)', paddingTop: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--shell-800)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.4 }}>
                  {t('map.howTo')}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.5 }}>
                  {t('map.howToText')}
                </div>
              </div>
            )}
          </div>
        </div>
      ) : (
        <button className="map-panel-collapse" onClick={() => setSidebarOpen(true)}>
          <Layers size={14} /> {t('map.filtersAnalysis')}
        </button>
      )}

      <div ref={mapRef} data-testid="map-canvas" style={{ width: '100%', height: '100%', background: MAP_BACKGROUND }} />

      {selectedConstituency && (
        <div data-testid="map-selected-chip" style={{ position: 'absolute', top: 14, left: sidebarOpen ? 350 : 14, zIndex: 1001, display: 'flex', alignItems: 'center', gap: 6, background: 'var(--shell-900)', color: 'var(--text-on-dark)', padding: '8px 14px', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-lg)', fontSize: 12, fontWeight: 600 }}>
          <Target size={14} style={{ color: 'var(--seal-gold)' }} />
          {selectedConstituency.stateLevel
            ? `${labels.state(selectedConstituency.state)} · ${t('map.stateLevel.legend')}`
            : `${selectedConstituency.constituency}, ${selectedConstituency.state}`}
          <button onClick={resetToNational} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-on-dark-muted)', padding: 2, marginLeft: 4 }} title={t('map.backNational')}>
            <X size={14} />
          </button>
        </div>
      )}

      {showWorkMarkers && works.length > 0 && (
        <div style={{ position: 'absolute', bottom: 48, right: 90, zIndex: 1001, display: 'flex', alignItems: 'center', gap: 6, background: 'rgba(255,255,255,0.95)', border: '1px solid var(--border-light)', padding: '6px 12px', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-md)', fontSize: 11 }}>
          <MapPin size={12} style={{ color: 'var(--indigo-600)' }} />
          <span>{t('map.markersShown', { n: works.length })}</span>
          {spiderCap && (
            <span data-testid="map-spider-capped" role="status" style={{ color: 'var(--text-secondary)' }}>
              {' · '}{t('map.spiderCapped', { shown: spiderCap.shown.toLocaleString('en-IN'), n: spiderCap.total.toLocaleString('en-IN') })}
            </span>
          )}
          {/* Hides the markers but keeps the loaded works, so the works
              button can bring them back without a new request. */}
          <button onClick={() => { setShowWorkMarkers(false); if (workMarkersRef.current) workMarkersRef.current.clearLayers(); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 2 }} title={t('map.hideMarkers')}>
            <X size={12} />
          </button>
        </div>
      )}

      {showWorkMarkers && works.length > 0 && (
        <div className="map-tier-legend" style={{ left: sidebarOpen ? 350 : 14 }} role="group" aria-label={t('map.legend.tiers')}>
          <span className="map-tier-legend-title">{t('map.legend.tiers')}</span>
          {TIER_ORDER.map(k => (
            <span key={k} className="map-tier-legend-item">
              <span className="map-tier-legend-dot" style={{ background: PRIORITY_COLORS[k] }} />
              {labels.riskShort(k)}
            </span>
          ))}
        </div>
      )}

      {loading && !firstLoadDone && (
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(255,255,255,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 999 }}>
          <LoadingState message={t('map.loading')} />
        </div>
      )}
      {loading && firstLoadDone && (
        <div data-testid="map-updating" role="status" style={{ position: 'absolute', top: 14, left: '50%', transform: 'translateX(-50%)', zIndex: 1001, background: 'rgba(255,255,255,0.95)', border: '1px solid var(--border-light)', padding: '6px 12px', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-md)', fontSize: 11, color: 'var(--text-secondary)' }}>
          {t('map.updating')}
        </div>
      )}

      <div className="map-notice-strip" style={{ left: sidebarOpen ? 350 : 14 }}>
        {t('map.gpsNote')}
      </div>
    </div>
  );
}
