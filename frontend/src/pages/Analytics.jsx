import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { getAnalytics } from '../services/api';
import LoadingState from '../components/LoadingState';
import ErrorState from '../components/ErrorState';
import EmptyState from '../components/EmptyState';
import Donut3D from '../components/charts/Donut3D';
import Bar3D from '../components/charts/Bar3D';
import { useTranslation } from '../i18n';
import { useLabels } from '../i18n/labels';

const COLORS = {
  CRITICAL: '#a6291f',
  HIGH: '#c45a20',
  MODERATE: '#93630c',
  LOW: '#1c6e46',
};

// Canonical display order so the risk donut always reads LOW -> CRITICAL
// left-to-right/clockwise, regardless of which band happens to have the
// most records this load (Object.entries on a value_counts() dict would
// otherwise order segments by count, which looks inconsistent run to run).
const RISK_LEVEL_ORDER = ['LOW', 'MODERATE', 'HIGH', 'CRITICAL'];

const CustomTooltip = ({ active, payload, label, total }) => {
  if (active && payload && payload.length) {
    const name = label || payload[0].name;
    const value = payload[0].value;
    const pct = total > 0 ? ((value / total) * 100).toFixed(1) : null;
    return (
      <div style={{
        background: '#fff',
        border: '1px solid #dfe2ea',
        borderRadius: 6,
        padding: '8px 12px',
        fontSize: 12,
        boxShadow: '0 4px 14px rgba(9,13,26,0.10)',
        lineHeight: 1.5
      }}>
        <div style={{ fontWeight: 600, marginBottom: 2, color: '#141c33', fontSize: 12 }}>{name}</div>
        <div style={{ color: '#454e64' }}>
          {typeof value === 'number' ? value.toLocaleString('en-IN') : value}
          {pct !== null && <span style={{ marginLeft: 6, color: '#737d95', fontSize: 11 }}>({pct}%)</span>}
        </div>
      </div>
    );
  }
  return null;
};

const AXIS_STYLE = { fill: '#737d95', fontSize: 11, fontFamily: 'IBM Plex Sans, sans-serif' };

export default function Analytics() {
  const { t } = useTranslation();
  const labels = useLabels();
  const navigate = useNavigate();
  const [analytics, setAnalytics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [flagView, setFlagView] = useState('category');

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getAnalytics()
      .then(setAnalytics)
      .catch((e) => { setAnalytics(null); setError(e); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <LoadingState message={t('analytics.loading')} />;
  if (error) return <ErrorState what={t('analytics.what')} error={error} onRetry={load} />;
  if (!analytics) return <EmptyState message={t('analytics.none')} />;

  const priorityDist = analytics.priority_distribution || {};
  const priorityData = RISK_LEVEL_ORDER
    .filter((k) => priorityDist[k] != null)
    .map((k) => ({ name: labels.riskShort(k), value: priorityDist[k], key: k, color: COLORS[k] }))
    // Any unexpected/legacy key not in the canonical order still renders
    // (never silently dropped) rather than disappearing from the chart.
    .concat(
      Object.entries(priorityDist)
        .filter(([k]) => !RISK_LEVEL_ORDER.includes(k))
        .map(([k, v]) => ({ name: k.replace(/_/g, ' '), value: v, key: k, color: COLORS[k] || '#667085' }))
    );

  const rateColor = (rate) => (rate > 50 ? '#a6291f' : rate > 30 ? '#93630c' : '#1c6e46');

  const categoryData = Object.entries(analytics.category_flags || {})
    .map(([k, v]) => ({ name: labels.category(k), flagged: v.flagged, total: v.total, rate: v.flag_rate }))
    .sort((a, b) => b.rate - a.rate);

  const stateData = Object.entries(analytics.state_flags || {})
    .map(([k, v]) => ({ name: k, flagged: v.flagged, total: v.total, rate: v.flag_rate }))
    .sort((a, b) => b.rate - a.rate)
    .slice(0, 20);

  const amountHistData = Object.entries(analytics.amount_histogram || {}).map(([k, v]) => ({
    range: k,
    count: v,
  }));
  const amountTotal = amountHistData.reduce((s, d) => s + d.count, 0);

  const signalDist = analytics.signal_distribution || {};

  return (
    <div>
      <div className="page-header">
        <h2>{t('nav.analytics')}</h2>
        <p className="subtitle">{t('analytics.subtitle')}</p>
      </div>

      <div className="charts-grid">
        {/* Risk Distribution — interactive 3D donut */}
        <div className="card">
          <div className="card-header">
            <h3>{t('dash.riskDist')}</h3>
          </div>
          <Donut3D data={priorityData} centerLabel={t('dash.donutCenter')} onSegmentClick={(key) => navigate(`/queue?risk_level=${key}`)} />
        </div>

        {/* Amount Distribution — animated gradient bar chart */}
        <div className="card">
          <div className="card-header">
            <h3>{t('analytics.amountDist')}</h3>
          </div>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={amountHistData} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
              <defs>
                <linearGradient id="amountBarGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3a4c8c" />
                  <stop offset="100%" stopColor="#1c2745" />
                </linearGradient>
              </defs>
              <XAxis dataKey="range" tick={AXIS_STYLE} angle={-30} textAnchor="end" height={70} tickLine={false} axisLine={{ stroke: '#e2e5ee' }} interval={0} />
              <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} width={50} />
              <Tooltip content={<CustomTooltip total={amountTotal} />} cursor={{ fill: 'rgba(20, 28, 51, 0.05)' }} />
              <Bar dataKey="count" fill="url(#amountBarGradient)" radius={[4, 4, 0, 0]} isAnimationActive animationDuration={900} animationEasing="ease-out" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Signal Distribution */}
      {Object.keys(signalDist).length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-header">
            <h3>{t('analytics.signalDist')}</h3>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
            {Object.entries(signalDist).map(([signal, dist]) => (
              <div key={signal} style={{
                background: 'var(--bg-subtle)',
                padding: 14,
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-light)'
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8, textTransform: 'capitalize', color: 'var(--navy)' }}>
                  {labels.signalText(signal)}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--risk-high)' }}>{t('level.high')}</span>
                    <span style={{ fontWeight: 600 }}>{dist.HIGH}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--risk-review)' }}>{t('level.medium')}</span>
                    <span style={{ fontWeight: 600 }}>{dist.MEDIUM}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--risk-low)' }}>{t('level.low')}</span>
                    <span style={{ fontWeight: 600 }}>{dist.LOW}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Flag Rates — tabbed switcher between Category and State */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-header">
          <h3>{t('analytics.flagRate')}</h3>
          <div className="seg-tabs">
            <button className={flagView === 'category' ? 'active' : ''} onClick={() => setFlagView('category')}>{t('analytics.byCategory')}</button>
            <button className={flagView === 'state' ? 'active' : ''} onClick={() => setFlagView('state')}>{t('analytics.byState')}</button>
          </div>
        </div>

        {flagView === 'category' ? (
          <>
            <Bar3D
              data={categoryData.map((c) => ({ key: c.name, name: c.name, value: c.rate, color: rateColor(c.rate) }))}
              max={100}
              labelWidth={200}
              valueFormatter={(v) => `${v}%`}
            />
            <div style={{ marginTop: 12 }}>
              <div className="data-table-container">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('common.category')}</th>
                      <th>{t('dash.kpi.totalWorks')}</th>
                      <th>{t('analytics.flagged')}</th>
                      <th>{t('analytics.flagRateCol')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {categoryData.map((cat) => (
                      <tr key={cat.name}>
                        <td style={{ fontWeight: 500 }}>{cat.name}</td>
                        <td>{cat.total}</td>
                        <td>{cat.flagged}</td>
                        <td style={{ color: cat.rate > 50 ? 'var(--risk-high)' : cat.rate > 30 ? 'var(--risk-review)' : 'var(--risk-low)', fontWeight: 600 }}>
                          {cat.rate}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        ) : (
          <Bar3D
            data={stateData.map((s) => ({ key: s.name, name: s.name, value: s.rate, color: rateColor(s.rate) }))}
            max={100}
            labelWidth={150}
            valueFormatter={(v) => `${v}%`}
          />
        )}
      </div>
    </div>
  );
}
