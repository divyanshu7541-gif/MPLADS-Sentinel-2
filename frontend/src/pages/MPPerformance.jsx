import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Search, Users, MapPin, ArrowRight, TrendingUp, AlertTriangle, BarChart3, X } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, LineChart, Line, CartesianGrid, Legend } from 'recharts';
import { getMps, getConstituencies, getMpPerformance, getConstituencyPerformance, getMpComparison, getConstituencyComparison, isNotFound } from '../services/api';
import MetricCard from '../components/MetricCard';
import RiskBadge from '../components/RiskBadge';
import LoadingState from '../components/LoadingState';
import EmptyState from '../components/EmptyState';
import ErrorState from '../components/ErrorState';
import MPAvatar from '../components/MPAvatar';
import Donut3D from '../components/charts/Donut3D';
import Bar3D from '../components/charts/Bar3D';
import { paletteColor } from '../components/charts/chartTheme';
import { useHouse, HouseNotApplicable } from '../components/HouseToggle';
import { useTranslation } from '../i18n';
import { useLabels } from '../i18n/labels';

const COLORS = {
  HIGH_PRIORITY_REVIEW: '#a6291f',
  REVIEW_RECOMMENDED: '#93630c',
  LOW: '#1c6e46',
};

const STAGE_COLORS = {
  COMPLETED: '#0d766c',
  SANCTIONED: '#93630c',
  RECOMMENDED: '#1c2745',
};

const AXIS_STYLE = { fill: '#737d95', fontSize: 11, fontFamily: 'IBM Plex Sans, sans-serif' };

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    return (
      <div style={{ background: '#fff', border: '1px solid #dfe2ea', borderRadius: 6, padding: '8px 12px', fontSize: 12, boxShadow: '0 4px 14px rgba(9,13,26,0.10)' }}>
        <div style={{ fontWeight: 600, marginBottom: 2, color: '#141c33' }}>{label || payload[0].name}</div>
        {payload.map((p, i) => (
          <div key={i} style={{ color: '#454e64' }}>
            {p.name}: {typeof p.value === 'number' ? p.value.toLocaleString('en-IN') : p.value}
          </div>
        ))}
      </div>
    );
  }
  return null;
};

const AmountTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    const val = payload[0].value;
    const display = val >= 10000000 ? `₹${(val / 10000000).toFixed(2)} Cr` : val >= 100000 ? `₹${(val / 100000).toFixed(1)} L` : `₹${val.toLocaleString('en-IN')}`;
    return (
      <div style={{ background: '#fff', border: '1px solid #dfe2ea', borderRadius: 6, padding: '8px 12px', fontSize: 12, boxShadow: '0 4px 14px rgba(9,13,26,0.10)' }}>
        <div style={{ fontWeight: 600, color: '#141c33' }}>{label}</div>
        <div style={{ color: '#454e64' }}>{display}</div>
      </div>
    );
  }
  return null;
};

// `display` maps a raw name to the text shown; selection still returns the raw name.
function SearchSelector({ label, placeholder, items, onSelect, icon: Icon, display = (x) => x }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const ref = useRef(null);
  const listRef = useRef(null);

  const filtered = query.length >= 1
    ? items.filter(item => item.toLowerCase().includes(query.toLowerCase()) || display(item).toLowerCase().includes(query.toLowerCase())).slice(0, 15)
    : [];

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setHighlighted(h => Math.min(h + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHighlighted(h => Math.max(h - 1, 0)); }
    else if (e.key === 'Enter' && highlighted >= 0 && filtered[highlighted]) {
      e.preventDefault();
      setQuery(display(filtered[highlighted]));
      setOpen(false);
      onSelect(filtered[highlighted]);
    }
    else if (e.key === 'Escape') { setOpen(false); }
  };

  return (
    <div ref={ref} style={{ position: 'relative', flex: 1, minWidth: 260 }}>
      <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 500, marginBottom: 6 }}>
        <Icon size={13} style={{ marginRight: 4, verticalAlign: -2 }} />
        {label}
      </label>
      <input
        className="filter-input"
        style={{ width: '100%', boxSizing: 'border-box' }}
        placeholder={placeholder}
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setHighlighted(-1); }}
        onFocus={() => query.length >= 1 && setOpen(true)}
        onKeyDown={handleKeyDown}
      />
      {open && filtered.length > 0 && (
        <div ref={listRef} style={{ position: 'absolute', top: '100%', left: 0, right: 0, background: '#fff', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-lg)', zIndex: 50, maxHeight: 280, overflowY: 'auto' }}>
          {filtered.map((item, i) => (
            <div
              key={item}
              style={{ padding: '8px 12px', cursor: 'pointer', fontSize: 13, background: i === highlighted ? 'var(--bg-hover)' : 'transparent', borderBottom: i < filtered.length - 1 ? '1px solid var(--border-light)' : 'none' }}
              onMouseEnter={() => setHighlighted(i)}
              onClick={() => { setQuery(display(item)); setOpen(false); onSelect(item); }}
            >
              {display(item)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StageBar({ label, count, total, color }) {
  const pct = total > 0 ? (count / total * 100) : 0;
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
        <span style={{ fontWeight: 600 }}>{count.toLocaleString('en-IN')} ({pct.toFixed(1)}%)</span>
      </div>
      <div style={{ height: 8, background: 'var(--bg-muted)', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 4, transition: 'width 0.3s' }} />
      </div>
    </div>
  );
}

function ProfileHeader({ data, type }) {
  const { t } = useTranslation();
  const labels = useLabels();
  const name = type === 'mp' ? labels.mpName(data.mp_name) : labels.consName(data.constituency_name);
  const subtitle = type === 'mp'
    ? [labels.consName(data.constituency), data.state].filter(Boolean).join(' \u2022 ')
    : [data.state, data.mps?.length ? t('mp.mpCount', { n: data.mps.length }) : ''].filter(Boolean).join(' \u2022 ');
  const initials = (name || '?').split(' ').filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <MPAvatar
            name={type === 'mp' ? data.mp_name : null}
            initials={initials}
            size={50}
            enablePhoto={type === 'mp'}
          />
          <div>
            <h2 style={{ fontSize: 19, fontWeight: 600, color: 'var(--shell-800)', margin: 0, fontFamily: 'var(--font-display)' }}>{name}</h2>
            <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 3 }}>{subtitle}</div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-outline" onClick={() => window.location.href = `/queue?mp=${encodeURIComponent(data.mp_name || '')}`}>
            <AlertTriangle size={14} /> {t('mp.priorityWorks')}
          </button>
          <button className="btn btn-outline" onClick={() => window.location.href = '/map'}>
            <MapPin size={14} /> {t('nav.map')}
          </button>
        </div>
      </div>
    </div>
  );
}

function ComparisonTable({ data, entityLabel }) {
  const { t } = useTranslation();
  const labels = useLabels();
  const showName = (n) => (entityLabel === 'mp' ? labels.mpName(n) : labels.consName(n));
  if (!data || data.length === 0) return null;
  const metrics = [
    { label: t('dash.kpi.totalWorks'), key: 'total_works' },
    { label: t('stage.completed'), key: 'completed_works' },
    { label: t('mp.completionRateLabel'), key: 'completion_rate', suffix: '%' },
    { label: t('mp.recordedAmount'), key: 'total_recorded_amount', currency: true },
    { label: t('mp.avgWorkAmount'), key: 'average_work_amount', currency: true },
    { label: t('mp.highPriorityShort'), key: 'high_priority_count' },
    { label: t('mp.reviewRec'), key: 'review_recommended_count' },
    { label: t('risk.low'), key: 'low_risk_count' },
    { label: t('mp.riskRateLabel'), key: 'risk_rate', suffix: '%' },
  ];
  const unitCr = t('common.unit.cr');
  const unitL = t('common.unit.lakh');
  return (
    <div className="card" style={{ marginTop: 16, overflowX: 'auto' }}>
      <div className="card-header"><h3>{t(entityLabel === 'mp' ? 'mp.comparingMps' : 'mp.comparingCons', { n: data.length })}</h3></div>
      <table className="data-table">
        <thead>
          <tr>
            <th style={{ minWidth: 140 }}>{t('mp.metric')}</th>
            {data.map(d => {
              const rawName = d.name || d.mp_name || d.constituency_name;
              const initials = (showName(rawName) || '?').split(' ').filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
              return (
                <th key={d.name} style={{ minWidth: 140, textAlign: 'right' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                    <MPAvatar
                      name={entityLabel === 'mp' ? rawName : null}
                      initials={initials}
                      size={36}
                      enablePhoto={entityLabel === 'mp'}
                    />
                    <span>{showName(rawName)}</span>
                  </div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {metrics.map(m => (
            <tr key={m.key}>
              <td style={{ fontWeight: 500 }}>{m.label}</td>
              {data.map(d => {
                const val = d[m.key];
                let display = val != null ? val : t('common.na');
                if (m.currency && typeof val === 'number') {
                  display = val >= 10000000 ? `₹${(val / 10000000).toFixed(2)} ${unitCr}` : val >= 100000 ? `₹${(val / 100000).toFixed(1)} ${unitL}` : `₹${val.toLocaleString('en-IN')}`;
                } else if (m.suffix && typeof val === 'number') {
                  display = `${val}${m.suffix}`;
                } else if (typeof val === 'number') {
                  display = val.toLocaleString('en-IN');
                }
                return <td key={d.name} style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace", fontSize: 12 }}>{display}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MPPerformance() {
  const { t } = useTranslation();
  const labels = useLabels();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { house } = useHouse();
  const isRS = house === 'RS';

  const [mpList, setMpList] = useState([]);
  const [constituencyList, setConstituencyList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [profileData, setProfileData] = useState(null);
  const [profileType, setProfileType] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  // { kind: 'notFound' } for a name with no records (404) or
  // { kind: 'failed', what, err, retry } when the call itself failed.
  const [error, setError] = useState(null);
  const [listsError, setListsError] = useState(null);

  const [compareMode, setCompareMode] = useState(false);
  const [compareType, setCompareType] = useState('mp');
  const [compareSelections, setCompareSelections] = useState([]);
  const [compareData, setCompareData] = useState(null);
  const [compareLoading, setCompareLoading] = useState(false);

  const loadLists = useCallback(() => {
    setListsError(null);
    Promise.all([getMps(), getConstituencies()]).then(([mps, cons]) => {
      setMpList(mps.mps || []);
      setConstituencyList((cons.constituencies || []).map(c => c.Constituency));
      setLoading(false);
    }).catch((e) => { setListsError(e); setLoading(false); });
  }, []);

  useEffect(() => { loadLists(); }, [loadLists]);

  // Load whatever profile the URL names, unless it is the one already loaded: the
  // search boxes load first and then write the URL, and a link opened while this
  // page is showing (e.g. from the help chat) changes only the URL.
  const loadedKeyRef = useRef(null);
  useEffect(() => {
    const mp = searchParams.get('mp');
    const cons = searchParams.get('constituency');
    if (mp && loadedKeyRef.current !== `mp|${mp}`) {
      loadProfile('mp', mp);
    } else if (!mp && cons && !isRS && loadedKeyRef.current !== `constituency|${cons}`) {
      loadProfile('constituency', cons);
    }
  }, [searchParams]);

  const loadProfile = useCallback((type, name) => {
    loadedKeyRef.current = `${type}|${name}`;
    setProfileLoading(true);
    setError(null);
    setProfileType(type);
    setCompareMode(false);
    setCompareData(null);
    const fn = type === 'mp' ? getMpPerformance : getConstituencyPerformance;
    fn(name).then(data => {
      if (data.error) { setError({ kind: 'notFound' }); setProfileData(null); }
      else { setProfileData(data); }
      setProfileLoading(false);
    }).catch(err => {
      setProfileData(null);
      setError(isNotFound(err)
        ? { kind: 'notFound' }
        : { kind: 'failed', what: 'mp.what.profile', err, retry: () => loadProfileRef.current(type, name) });
      setProfileLoading(false);
    });
  }, []);
  const loadProfileRef = useRef(loadProfile);
  loadProfileRef.current = loadProfile;

  const loadComparison = useCallback(() => {
    if (compareSelections.length < 2) return;
    setCompareLoading(true);
    setError(null);
    const fn = compareType === 'mp' ? getMpComparison : getConstituencyComparison;
    fn(compareSelections).then(data => {
      setCompareData(data);
      setCompareLoading(false);
    }).catch(err => {
      setCompareData(null);
      setError({ kind: 'failed', what: 'mp.what.compare', err, retry: () => loadComparisonRef.current() });
      setCompareLoading(false);
    });
  }, [compareType, compareSelections]);
  const loadComparisonRef = useRef(loadComparison);
  loadComparisonRef.current = loadComparison;

  if (loading) return <LoadingState message={t('mp.loadingData')} />;

  // Risk percentages use SCORED works as the denominator: recommended-only
  // works have no risk assessment, so counting them would dilute the rate.
  const scoredWorks = profileData
    ? (profileData.scored_works
      ?? Object.values(profileData.risk_distribution || {}).reduce((a, b) => a + b, 0))
    : 0;

  const trendData = profileData?.trend?.map(tr => ({ name: tr.period, works: tr.works, amount: tr.amount })) || [];
  const stageData = profileData?.stage_distribution ? Object.entries(profileData.stage_distribution).map(([k, v]) => ({ name: k, value: v })) : [];
  const catData = profileData?.category_distribution ? Object.entries(profileData.category_distribution).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([k, v]) => ({ name: labels.category(k), count: v, amount: profileData.category_amounts?.[k] || 0 })) : [];
  const priorityData = profileData?.priority_distribution ? Object.entries(profileData.priority_distribution).map(([k, v]) => ({ name: labels.riskShort(k) === k ? k.replace(/_/g, ' ') : labels.riskShort(k), value: v, key: k, color: COLORS[k] || '#667085' })) : [];

  return (
    <div>
      <div className="page-header">
        <h2>{t('nav.browseMp')}</h2>
        <p className="subtitle">{t('mp.subtitle')}</p>
      </div>

      {/* Section 1: Browse & Search */}
      <div className="card mpp-section" style={{ marginBottom: 16 }}>
        <div className="mpp-section-header">
          <div className="mpp-section-icon"><Search size={15} /></div>
          <div className="mpp-section-heading">
            <h3>{t('mp.browse')}</h3>
            <p>{t('mp.browseDesc')}</p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <SearchSelector
            label={t('mp.searchMp')}
            placeholder={t('mp.typeMp')}
            items={mpList}
            icon={Users}
            display={labels.mpName}
            onSelect={(name) => { loadProfile('mp', name); setSearchParams({ mp: name }); }}
          />
          {isRS ? (
            <div style={{ flex: 1, minWidth: 260 }}>
              <HouseNotApplicable feature={t('house.na.consPerformance')} />
            </div>
          ) : (
            <SearchSelector
              label={t('mp.searchCons')}
              placeholder={t('mp.typeCons')}
              items={constituencyList}
              icon={MapPin}
              display={labels.consName}
              onSelect={(name) => { loadProfile('constituency', name); setSearchParams({ constituency: name }); }}
            />
          )}
        </div>
      </div>

      {/* Section 2: Compare */}
      <div className="card mpp-section" style={{ marginBottom: 16 }}>
        <div className="mpp-section-header">
          <div className="mpp-section-icon mpp-section-icon-compare"><BarChart3 size={15} /></div>
          <div className="mpp-section-heading" style={{ flex: 1 }}>
            <h3>{t('mp.compare')}</h3>
            <p>{t('mp.compareDesc')}</p>
          </div>
          <button
            className={`btn ${compareMode ? 'btn-primary' : 'btn-outline'}`}
            onClick={() => { setCompareMode(!compareMode); setCompareData(null); setCompareSelections([]); setProfileData(null); setError(null); loadedKeyRef.current = null; setSearchParams({}); }}
          >
            {compareMode ? t('mp.exitCompare') : t('mp.startCompare')}
          </button>
        </div>

        {compareMode && (
          <div className="mpp-section-body">
            <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
              <button className={`btn ${compareType === 'mp' ? 'btn-primary' : 'btn-outline'}`} onClick={() => { setCompareType('mp'); setCompareSelections([]); setCompareData(null); }}>{t('mp.mps')}</button>
              <button className={`btn ${compareType === 'constituency' ? 'btn-primary' : 'btn-outline'}`} onClick={() => { setCompareType('constituency'); setCompareSelections([]); setCompareData(null); }}>{t('mp.constituencies')}</button>
            </div>
            {isRS && compareType === 'constituency' ? (
              <HouseNotApplicable feature={t('house.na.consComparison')} />
            ) : (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <SearchSelector
                label={compareType === 'mp' ? t('mp.addMp') : t('mp.addCons')}
                placeholder={compareType === 'mp' ? t('mp.typeMp') : t('mp.typeCons')}
                items={compareType === 'mp' ? mpList : constituencyList}
                icon={compareType === 'mp' ? Users : MapPin}
                display={compareType === 'mp' ? labels.mpName : labels.consName}
                onSelect={(name) => { if (!compareSelections.includes(name) && compareSelections.length < 4) { const next = [...compareSelections, name]; setCompareSelections(next); } }}
              />
              {compareSelections.length > 0 && (
                <div style={{ paddingBottom: 2 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>{t('mp.selected')}</div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {compareSelections.map(s => (
                      <span key={s} className="chip">
                        {compareType === 'mp' ? labels.mpName(s) : labels.consName(s)}
                        <X size={12} style={{ cursor: 'pointer' }} onClick={() => setCompareSelections(prev => prev.filter(x => x !== s))} />
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <button className="btn btn-primary" disabled={compareSelections.length < 2 || compareLoading} onClick={loadComparison}>
                {compareLoading ? t('mp.comparing') : t('mp.run')}
              </button>
            </div>
            )}
          </div>
        )}
      </div>

      {/* Comparison Results */}
      {compareData && <ComparisonTable data={compareData.comparison} entityLabel={compareType} />}

      {/* A ?constituency= link opened while Rajya Sabha is selected */}
      {isRS && searchParams.get('constituency') && !compareMode && (
        <div style={{ marginBottom: 16 }}>
          <HouseNotApplicable feature={t('house.na.consPerformance')} />
        </div>
      )}

      {listsError && (
        <ErrorState compact what={t('mp.what.lists')} error={listsError} onRetry={loadLists} />
      )}

      {/* Not found (the name has no records) vs failed to load (the call errored) */}
      {error && error.kind === 'notFound' && <EmptyState message={t('mp.notFound')} />}
      {error && error.kind === 'failed' && (
        <div className="card" style={{ marginBottom: 16 }}>
          <ErrorState what={t(error.what)} error={error.err} onRetry={error.retry} />
        </div>
      )}

      {/* Profile Loading */}
      {profileLoading && <LoadingState message={t('mp.loadingProfile')} />}

      {/* Profile Content */}
      {profileData && !profileLoading && !compareMode && (
        <>
          <ProfileHeader data={profileData} type={profileType} />

          {/* KPI Cards */}
          <div className="metrics-grid">
            <MetricCard label={t('dash.kpi.totalWorks')} value={profileData.total_works} subtitle={t('mp.records', { n: profileData.total_works })} />
            <MetricCard label={t('stage.completed')} value={profileData.completed_works} variant="success" subtitle={t('mp.completionRate', { pct: profileData.completion_rate })} />
            <MetricCard label={t('stage.sanctioned')} value={profileData.sanctioned_works} variant="warning" subtitle={t('mp.inProgress', { n: profileData.stage_distribution?.SANCTIONED || 0 })} />
            <MetricCard label={t('stage.recommended')} value={profileData.recommended_works} variant="info" subtitle={t('mp.pending', { n: profileData.stage_distribution?.RECOMMENDED || 0 })} />
            <MetricCard label={t('mp.recordedValue')} value={`₹${(profileData.total_recorded_amount / 10000000).toFixed(2)} ${t('common.unit.cr')}`} subtitle={t('mp.avgPerWork', { x: (profileData.average_work_amount / 100000).toFixed(1) })} />
            <MetricCard label={t('mp.completedValue')} value={`₹${(profileData.completed_amount / 10000000).toFixed(2)} ${t('common.unit.cr')}`} variant="success" subtitle={t('dash.kpi.ofTotal', { pct: (profileData.completed_amount / profileData.total_recorded_amount * 100).toFixed(1) })} />
            <MetricCard label={t('mp.highPriority')} value={profileData.high_priority_count} variant="danger" subtitle={scoredWorks > 0 ? t('mp.ofScoredWorks', { pct: (profileData.high_priority_count / scoredWorks * 100).toFixed(1), n: scoredWorks.toLocaleString('en-IN') }) : t('mp.noScoredWorks')} />
            <MetricCard label={t('mp.reviewRec')} value={profileData.review_recommended_count} variant="warning" subtitle={t('mp.riskRate', { pct: profileData.risk_rate })} />
          </div>

          {/* Fund / Amount by Stage */}
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-header"><h3>{t('mp.valueByStage')}</h3></div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 16 }}>
              {t('mp.valueNote')}
            </div>
            {Object.entries(profileData.stage_amounts || {}).map(([stage, amount]) => (
              <StageBar
                key={stage}
                label={labels.stageUpper(stage)}
                count={Math.round(amount)}
                total={profileData.total_recorded_amount}
                color={STAGE_COLORS[stage] || '#667085'}
              />
            ))}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginTop: 16 }}>
              {Object.entries(profileData.stage_amounts || {}).map(([stage, amount]) => (
                <div key={stage} style={{ textAlign: 'center', padding: 12, background: 'var(--bg-subtle)', borderRadius: 'var(--radius-md)' }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 4 }}>{labels.stageUpper(stage)}</div>
                  <div style={{ fontSize: 16, fontWeight: 700, color: STAGE_COLORS[stage] || 'var(--text-primary)' }}>
                    {amount >= 10000000 ? `₹${(amount / 10000000).toFixed(2)} ${t('common.unit.cr')}` : `₹${(amount / 100000).toFixed(1)} ${t('common.unit.lakh')}`}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Charts Row: Risk + Category */}
          <div className="charts-grid">
            {/* Risk Distribution — interactive 3D donut */}
            <div className="card">
              <div className="card-header"><h3>{t('dash.riskDist')}</h3></div>
              <Donut3D data={priorityData} centerLabel={t('common.works')} size={200} />
            </div>

            {/* Category Distribution — dimensional animated bars */}
            <div className="card">
              <div className="card-header"><h3>{t('dash.worksByCategory')}</h3></div>
              <Bar3D
                data={catData.map((c, i) => ({ key: c.name, name: c.name, value: c.count, color: paletteColor(i) }))}
                labelWidth={140}
              />
            </div>
          </div>

          {/* Trend */}
          {trendData.length > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <div className="card-header"><h3>{t('mp.trend')}</h3></div>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={trendData} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                  <defs>
                    <linearGradient id="trendWorksGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#3a4c8c" />
                      <stop offset="100%" stopColor="#1c2745" />
                    </linearGradient>
                    <linearGradient id="trendAmountGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#12a394" />
                      <stop offset="100%" stopColor="#0d766c" />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e5ee" />
                  <XAxis dataKey="name" tick={AXIS_STYLE} tickLine={false} axisLine={{ stroke: '#e2e5ee' }} />
                  <YAxis yAxisId="left" tick={AXIS_STYLE} tickLine={false} axisLine={false} />
                  <YAxis yAxisId="right" orientation="right" tick={AXIS_STYLE} tickLine={false} axisLine={false} tickFormatter={(v) => v >= 10000000 ? `${(v / 10000000).toFixed(0)}${t('common.unit.cr')}` : v >= 100000 ? `${(v / 100000).toFixed(0)}${t('common.unit.lakh')}` : v} />
                  <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(20, 28, 51, 0.05)' }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar yAxisId="left" dataKey="works" name={t('common.works')} fill="url(#trendWorksGradient)" radius={[4, 4, 0, 0]} isAnimationActive animationDuration={900} animationEasing="ease-out" />
                  <Bar yAxisId="right" dataKey="amount" name={t('mp.trendAmount')} fill="url(#trendAmountGradient)" radius={[4, 4, 0, 0]} isAnimationActive animationDuration={900} animationEasing="ease-out" animationBegin={150} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Signal Summary */}
          {Object.keys(profileData.signal_summary || {}).length > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <div className="card-header"><h3>{t('common.analyticalSignals')}</h3></div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
                {t('mp.signalNote')}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
                {Object.entries(profileData.signal_summary).map(([signal, data]) => (
                  <div key={signal} style={{ background: 'var(--bg-subtle)', padding: 12, borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, textTransform: 'capitalize', color: 'var(--navy)' }}>{labels.signalText(signal)}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                      {t('common.active')}: <span style={{ fontWeight: 600 }}>{data.active_count}</span>
                    </div>
                    {data.high_count > 0 && (
                      <div style={{ fontSize: 11, color: 'var(--risk-high)' }}>
                        {t('level.high')}: <span style={{ fontWeight: 600 }}>{data.high_count}</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Top Flagged Works */}
          {profileData.top_flagged_works?.length > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <div className="card-header">
                <h3>{t('mp.topFlagged')}</h3>
                <button className="btn btn-ghost" onClick={() => navigate(`/queue?mp=${encodeURIComponent(profileData.mp_name || profileData.constituency_name || '')}`)}>
                  {t('common.viewAll')} <ArrowRight size={14} />
                </button>
              </div>
              <div className="data-table-container">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('common.workId')}</th>
                      <th>{t('common.description')}</th>
                      <th>{t('common.amount')}</th>
                      <th>{t('common.stage')}</th>
                      <th>{t('common.priority')}</th>
                      <th>{t('common.signals')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profileData.top_flagged_works.map((w) => (
                      <tr key={w.record_id} className="clickable" onClick={() => navigate(`/record/${w.record_id}`)}>
                        <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, fontWeight: 500 }}>{w.record_id}</td>
                        <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{w.description}</td>
                        <td style={{ fontWeight: 600 }}>
                          {w.amount != null ? (w.amount >= 10000000 ? `₹${(w.amount / 10000000).toFixed(2)} ${t('common.unit.cr')}` : `₹${(w.amount / 100000).toFixed(1)} ${t('common.unit.lakh')}`) : t('common.na')}
                        </td>
                        <td><span className={`stage-badge ${w.stage}`}>{labels.stageUpper(w.stage)}</span></td>
                        <td><RiskBadge priority={w.priority} size="sm" /></td>
                        <td style={{ color: w.evidence_count > 2 ? 'var(--risk-high)' : 'var(--text-muted)', fontWeight: w.evidence_count > 2 ? 600 : 400 }}>{w.evidence_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {/* Landing State */}
      {!profileData && !profileLoading && !compareMode && !error && (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
          <BarChart3 size={48} style={{ color: 'var(--text-faint)', marginBottom: 16 }} />
          <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
            {t('mp.selectPrompt')}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            {t('mp.selectHint')}
          </div>
        </div>
      )}
    </div>
  );
}
