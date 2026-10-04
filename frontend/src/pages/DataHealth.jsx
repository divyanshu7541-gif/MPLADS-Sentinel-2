import React, { useState, useEffect, useCallback } from 'react';
import { Database, CheckCircle, AlertTriangle, XCircle, FileStack, Users, MapPinned } from 'lucide-react';
import { getDataHealth } from '../services/api';
import LoadingState from '../components/LoadingState';
import ErrorState from '../components/ErrorState';
import EmptyState from '../components/EmptyState';
import MetricCard from '../components/MetricCard';
import { useTranslation } from '../i18n';
import { useLabels } from '../i18n/labels';

// Source dataset column names -> i18n keys (display only; the raw column
// name is still the lookup key into the API's missingness data).
const COLUMN_KEYS = {
  State: 'common.state',
  MP: 'common.mp',
  Constituency: 'common.constituency',
  'Work Description': 'common.workDescription',
  Date: 'common.date',
  Amount: 'common.amount',
  Stage: 'common.stage',
  'Record ID': 'common.recordId',
};

export default function DataHealth() {
  const { t } = useTranslation();
  const labels = useLabels();
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getDataHealth()
      .then(setHealth)
      .catch((e) => { setHealth(null); setError(e); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <LoadingState message={t('health.loading')} />;
  if (error) return <ErrorState what={t('health.what')} error={error} onRetry={load} />;
  if (!health) return <EmptyState message={t('health.none')} />;

  const completenessPct = health.total_records > 0
    ? ((health.valid_records / health.total_records) * 100).toFixed(1)
    : 0;

  return (
    <div>
      <div className="page-header">
        <h2>{t('health.title')}</h2>
        <p className="subtitle">{t('health.subtitle')}</p>
      </div>

      {/* Overview Stats */}
      <div className="metrics-grid">
        <MetricCard index={0} label={t('health.sourceFile')} value={health.source_file} variant="info" icon={FileStack} />
        <MetricCard index={1} label={t('health.totalRecords')} value={health.total_records} variant="info" icon={Database} />
        <MetricCard index={2} label={t('health.validRecords')} value={health.valid_records} variant="success" icon={CheckCircle} />
        <MetricCard
          index={3}
          label={t('health.completeness')}
          value={`${completenessPct}%`}
          variant={completenessPct > 90 ? 'success' : completenessPct > 70 ? 'warning' : 'danger'}
        />
        <MetricCard index={4} label={t('health.statesCovered')} value={health.state_count} icon={MapPinned} />
        <MetricCard index={5} label={t('health.mpsInDataset')} value={health.mp_count} icon={Users} />
      </div>

      <div className="health-grid">
        {/* Data Quality Overview */}
        <div className="card">
          <div className="card-header">
            <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Database size={16} />
              {t('health.overview')}
            </h3>
          </div>

          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t('health.totalRecords')}</span>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{health.total_records?.toLocaleString('en-IN')}</span>
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 12, color: 'var(--success)' }}>{t('health.validRecords')}</span>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{health.valid_records?.toLocaleString('en-IN')}</span>
            </div>
            <div className="health-bar">
              <div className="fill good" style={{ width: `${(health.valid_records / health.total_records) * 100}%` }}></div>
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 12, color: 'var(--warning)' }}>{t('health.incomplete')}</span>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{health.incomplete_records}</span>
            </div>
            <div className="health-bar">
              <div className="fill warning" style={{ width: `${(health.incomplete_records / health.total_records) * 100}%` }}></div>
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 12, color: 'var(--danger)' }}>{t('health.duplicates')}</span>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{health.duplicate_candidates}</span>
            </div>
            <div className="health-bar">
              <div className="fill bad" style={{ width: `${(health.duplicate_candidates / health.total_records) * 100}%` }}></div>
            </div>
          </div>

          {health.near_duplicate_candidates > 0 && (
            <div style={{ marginBottom: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 12, color: 'var(--warning)' }}>{t('health.nearDuplicates')}</span>
                <span style={{ fontSize: 12, fontWeight: 600 }}>{health.near_duplicate_candidates?.toLocaleString('en-IN')}</span>
              </div>
              <div className="health-bar">
                <div className="fill warning" style={{ width: `${(health.near_duplicate_candidates / health.total_records) * 100}%` }}></div>
              </div>
            </div>
          )}
        </div>

        {/* Column Missingness */}
        <div className="card">
          <div className="card-header">
            <h3>{t('health.columnCompleteness')}</h3>
          </div>
          {health.columns?.map((col) => {
            const pct = health.missingness?.[col] || 0;
            return (
              <div key={col} style={{ marginBottom: 10 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{COLUMN_KEYS[col] ? t(COLUMN_KEYS[col]) : col}</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: pct > 10 ? 'var(--danger)' : pct > 0 ? 'var(--warning)' : 'var(--success)' }}>
                    {pct === 0 ? t('health.complete') : t('health.missing', { pct })}
                  </span>
                </div>
                <div className="health-bar">
                  <div
                    className={`fill ${pct > 10 ? 'bad' : pct > 0 ? 'warning' : 'good'}`}
                    style={{ width: `${Math.max(100 - pct, 1)}%` }}
                  ></div>
                </div>
              </div>
            );
          })}
        </div>

        {/* Signals Status */}
        <div className="card">
          <div className="card-header">
            <h3>{t('common.analyticalSignals')}</h3>
          </div>

          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--success)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
              <CheckCircle size={14} />
              {t('health.activeSignals')}
            </div>
            {health.signals_enabled?.map((signal) => (
              <div key={signal} style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '4px 0', paddingLeft: 20 }}>
                {labels.signalText(signal)}
              </div>
            ))}
          </div>

          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--danger)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
              <XCircle size={14} />
              {t('health.unavailableSignals')}
            </div>
            {health.signals_unavailable?.map((signal) => (
              <div key={signal} style={{ fontSize: 12, color: 'var(--text-muted)', padding: '4px 0', paddingLeft: 20 }}>
                {labels.unavailable(signal).replace(/_/g, ' ')} — {t('health.unavailSuffix')}
              </div>
            ))}
          </div>
        </div>

        {/* Stage Distribution */}
        <div className="card">
          <div className="card-header">
            <h3>{t('dash.stageDist')}</h3>
          </div>
          {Object.entries(health.stage_distribution || {}).map(([stage, count]) => (
            <div key={stage} style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{labels.stageUpper(stage)}</span>
                <span style={{ fontSize: 12, fontWeight: 600 }}>{count.toLocaleString('en-IN')}</span>
              </div>
              <div className="health-bar">
                <div
                  className={`fill ${stage === 'COMPLETED' ? 'good' : stage === 'SANCTIONED' ? 'warning' : ''}`}
                  style={{ width: `${(count / health.total_records) * 100}%`, background: stage === 'RECOMMENDED' ? 'var(--info)' : undefined }}
                ></div>
              </div>
            </div>
          ))}

          {/* Category Inference */}
          {health.category_inference && (
            <div style={{ marginTop: 20, borderTop: '1px solid var(--border-light)', paddingTop: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--navy)', marginBottom: 8 }}>
                {t('health.categoryInference')}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
                {t('health.catTotals', { total: health.category_inference.total?.toLocaleString('en-IN'), inferred: health.category_inference.inferred?.toLocaleString('en-IN') })}
              </div>
              {Object.entries(health.category_inference.categories || {}).slice(0, 5).map(([cat, count]) => (
                <div key={cat} style={{ fontSize: 11, color: 'var(--text-muted)', paddingLeft: 12, padding: '2px 0 2px 12px' }}>
                  {labels.category(cat)}: {count}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
