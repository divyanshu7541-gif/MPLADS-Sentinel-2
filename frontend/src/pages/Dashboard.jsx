import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, Clock, BarChart3, TrendingUp, ArrowRight, Map, IndianRupee, Shield, Activity } from 'lucide-react';
import { getSummary } from '../services/api';
import MetricCard from '../components/MetricCard';
import LoadingState from '../components/LoadingState';
import EmptyState from '../components/EmptyState';
import ErrorState from '../components/ErrorState';
import StageBadge from '../components/StageBadge';
import Donut3D from '../components/charts/Donut3D';
import Bar3D from '../components/charts/Bar3D';
import { paletteColor } from '../components/charts/chartTheme';
import { useTranslation } from '../i18n';
import { useLabels } from '../i18n/labels';

const COLORS = {
  CRITICAL: '#a6291f',
  HIGH: '#c45a20',
  MODERATE: '#93630c',
  LOW: '#1c6e46',
};

const RISK_LEVEL_ORDER = ['LOW', 'MODERATE', 'HIGH', 'CRITICAL'];

export default function Dashboard() {
  const { t, lang } = useTranslation();
  const labels = useLabels();
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getSummary()
      .then(setSummary)
      .catch((e) => { setSummary(null); setError(e); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <LoadingState variant="metrics" />;
  if (error) return <ErrorState what={t('dash.what')} error={error} onRetry={load} />;
  if (!summary) return <EmptyState message={t('dash.noData')} />;

  const totalRecords = summary.total_records || 0;
  const criticalCount = summary.critical_count || 0;
  const highCount = summary.high_count || 0;
  const moderateCount = summary.moderate_count || 0;
  const lowCount = summary.low_count || 0;
  const totalAmount = summary.total_amount || 0;
  const amountInCr = (totalAmount / 10000000).toFixed(0);
  const avgRisk = summary.average_risk || 0;
  const highRiskPct = summary.high_risk_percentage || 0;
  const avgConfidence = summary.average_confidence || 0;

  const riskDist = summary.risk_distribution || summary.priority_distribution || {};
  const riskData = RISK_LEVEL_ORDER
    .filter((k) => riskDist[k] != null)
    .map((k) => ({ name: labels.riskShort(k), value: riskDist[k], key: k, color: COLORS[k] }))
    .concat(
      Object.entries(riskDist)
        .filter(([k]) => !RISK_LEVEL_ORDER.includes(k))
        .map(([k, v]) => ({ name: k.replace(/_/g, ' '), value: v, key: k, color: COLORS[k] || '#667085' }))
    );

  const categoryData = Object.entries(summary.category_distribution || {})
    .map(([k, v]) => ({ name: labels.category(k), count: v }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 8);

  const anomalies = summary.top_signals?.cost_anomalies || [];

  const criticalPct = totalRecords > 0 ? ((criticalCount / totalRecords) * 100) : 0;
  const highPct = totalRecords > 0 ? ((highCount / totalRecords) * 100) : 0;

  return (
    <div>
      <div className="page-header">
        <h2>{t('dash.heading')}</h2>
        <p className="subtitle">{t('dash.subtitle')}</p>
      </div>

      {/* KPI Metrics */}
      <div className="metrics-grid">
        <MetricCard index={0} label={t('dash.kpi.totalWorks')} value={totalRecords} variant="info" subtitle={t('dash.kpi.totalWorksSub')} />
        <MetricCard index={1} label={t('dash.kpi.totalAmount')} value={`₹${amountInCr}${t('landing.unit.cr')}`} variant="amount" subtitle={t('dash.kpi.totalAmountSub')} icon={IndianRupee} />
        <MetricCard index={2} label={t('dash.kpi.critical')} value={criticalCount} variant="danger" subtitle={t('dash.kpi.ofTotal', { pct: criticalPct.toFixed(1) })} />
        <MetricCard index={3} label={t('dash.kpi.highRisk')} value={highCount} variant="warning" subtitle={t('dash.kpi.ofTotal', { pct: highPct.toFixed(1) })} />
        <MetricCard index={4} label={t('dash.kpi.confidence')} value={`${avgConfidence.toFixed(0)}%`} variant="success" subtitle={t('dash.kpi.confidenceSub')} icon={Shield} />
      </div>

      {/* Main split: analytical content + sticky rail */}
      <div className="split-layout">
        <div>
          {/* Risk Distribution */}
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-header">
              <h3>{t('dash.riskDist')}</h3>
              <span className="card-header-hint">{t('dash.totalWorksHint', { count: totalRecords.toLocaleString('en-IN') })}</span>
            </div>
            <Donut3D
              data={riskData}
              centerLabel={t('dash.donutCenter')}
              onSegmentClick={(key) => navigate(`/queue?risk_level=${key}`)}
            />
          </div>

          {/* Category chart */}
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-header">
              <h3>{t('dash.worksByCategory')}</h3>
            </div>
            <Bar3D
              data={categoryData.map((c, i) => ({ key: c.name, name: c.name, value: c.count, color: paletteColor(i) }))}
              labelWidth={150}
            />
          </div>

          {/* Top Anomalies */}
          {anomalies.length > 0 && (
            <div className="card">
              <div className="card-header">
                <h3>{t('dash.topAnomalies')}</h3>
                <button className="btn btn-ghost" onClick={() => navigate('/queue?sort_by=cost_anomaly_score')}>
                  {t('common.viewAll')} <ArrowRight size={14} />
                </button>
              </div>
              <div className="ranked-list">
                {anomalies.map((item, i) => (
                  <div key={i} className="ranked-item" onClick={() => navigate(`/record/${item['Record ID']}`)}>
                    <div className="rank">{i + 1}</div>
                    <div className="accent-bar" style={{ background: 'var(--risk-high)' }} />
                    <div className="body">
                      <div className="title">{item['Work Description']}</div>
                      <div className="meta">
                        <span style={{ fontFamily: 'var(--font-mono)' }}>{item['Record ID']}</span>
                        <span>{item['MP']}</span>
                        <span style={{ color: 'var(--risk-high)' }}>{item.cost_anomaly_explanation?.substring(0, 70)}</span>
                      </div>
                    </div>
                    <div className="value">₹{(item['Amount'] / 100000).toFixed(1)} {t('common.unit.lakh')}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Sticky rail */}
        <div className="rail">
          <div className="card">
            <div className="card-header"><h3>{t('dash.riskSummary')}</h3></div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[
                { label: t('dash.risk.critical'), count: criticalCount, color: 'var(--risk-high)' },
                { label: t('dash.risk.high'), count: highCount, color: '#c45a20' },
                { label: t('dash.risk.moderate'), count: moderateCount, color: 'var(--risk-review)' },
                { label: t('dash.risk.low'), count: lowCount, color: 'var(--risk-low)' },
              ].map(({ label, count, color }) => (
                <div key={label} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '10px 12px', background: 'var(--bg-subtle)', borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-light)'
                }}>
                  <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)' }}>{label}</span>
                  <span style={{
                    fontSize: 17, fontWeight: 700, fontFamily: 'var(--font-mono)',
                    color: color
                  }}>
                    {count.toLocaleString('en-IN')}
                  </span>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 12, padding: '10px 12px', background: 'var(--bg-subtle)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{t('dash.avgRiskScore')}</span>
                <span style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)', color: avgRisk > 50 ? 'var(--risk-high)' : 'var(--risk-low)' }}>
                  {avgRisk.toFixed(1)}%
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{t('dash.highCriticalRate')}</span>
                <span style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
                  {highRiskPct.toFixed(1)}%
                </span>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header"><h3>{t('dash.stageDist')}</h3></div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {Object.entries(summary.stage_distribution || {}).map(([stage, count]) => (
                <div key={stage} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '10px 12px', background: 'var(--bg-subtle)', borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-light)'
                }}>
                  <StageBadge stage={stage} />
                  <span style={{
                    fontSize: 17, fontWeight: 700, fontFamily: 'var(--font-mono)',
                    color: stage === 'COMPLETED' ? 'var(--risk-low)' : stage === 'SANCTIONED' ? 'var(--risk-review)' : 'var(--info)'
                  }}>
                    {count.toLocaleString('en-IN')}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-header"><h3>{t('dash.quickActions')}</h3></div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button className="btn btn-primary" style={{ justifyContent: 'space-between' }} onClick={() => navigate('/map')}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Map size={15} /> {t('dash.action.map')}</span>
                <ArrowRight size={14} />
              </button>
              <button className="btn btn-outline" style={{ justifyContent: 'space-between' }} onClick={() => navigate('/queue?risk_level=CRITICAL')}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><AlertTriangle size={15} /> {t('dash.action.critical')}</span>
                <ArrowRight size={14} />
              </button>
              <button className="btn btn-outline" style={{ justifyContent: 'space-between' }} onClick={() => navigate('/queue')}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Clock size={15} /> {t('dash.action.queue')}</span>
                <ArrowRight size={14} />
              </button>
              <button className="btn btn-outline" style={{ justifyContent: 'space-between' }} onClick={() => navigate('/analytics')}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><BarChart3 size={15} /> {t('dash.action.analytics')}</span>
                <ArrowRight size={14} />
              </button>
              <button className="btn btn-outline" style={{ justifyContent: 'space-between' }} onClick={() => navigate('/data-health')}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><TrendingUp size={15} /> {t('dash.action.health')}</span>
                <ArrowRight size={14} />
              </button>
            </div>
          </div>

          {/* Data date (served.data_as_of is a YYYY-MM-DD date) */}
          {summary.served?.data_as_of && (
            <div style={{ textAlign: 'center', fontSize: 10, color: 'var(--text-faint)', padding: '8px 0' }}>
              {t('dash.dataAsOf', {
                date: new Date(`${summary.served.data_as_of}T00:00:00+05:30`).toLocaleDateString(`${lang}-IN`, {
                  day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata',
                }),
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
