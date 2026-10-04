import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ChevronLeft, ChevronRight, ExternalLink, Search } from 'lucide-react';
import { getQueue, getStates } from '../services/api';
import RiskBadge from '../components/RiskBadge';
import StageBadge from '../components/StageBadge';
import LoadingState from '../components/LoadingState';
import EmptyState from '../components/EmptyState';
import ErrorState from '../components/ErrorState';
import { useTranslation } from '../i18n';
import { useLabels } from '../i18n/labels';

const ROW_ACCENT = {
  CRITICAL: 'var(--risk-high)',
  HIGH: '#c45a20',
  MODERATE: 'var(--risk-review)',
  LOW: 'var(--risk-low)',
  HIGH_PRIORITY_REVIEW: 'var(--risk-high)',
  REVIEW_RECOMMENDED: 'var(--risk-review)',
};

const RISK_PILLS = [
  { value: '', labelKey: 'common.allRisk' },
  { value: 'CRITICAL', labelKey: 'risk.critical', tone: 'high' },
  { value: 'HIGH', labelKey: 'risk.high', tone: 'high' },
  { value: 'MODERATE', labelKey: 'risk.moderate', tone: 'review' },
  { value: 'LOW', labelKey: 'risk.low', tone: 'low' },
];

export default function InvestigationQueue() {
  const { t } = useTranslation();
  const labels = useLabels();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const filtersFromUrl = (params) => ({
    risk_level: params.get('risk_level') || params.get('priority') || '',
    stage: params.get('stage') || '',
    state: params.get('state') || '',
    mp: params.get('mp') || '',
    search: params.get('search') || '',
    sort_by: params.get('sort_by') || 'risk_score',
    sort_order: params.get('sort_order') || 'desc',
    page: parseInt(params.get('page')) || 1,
    page_size: 50,
  });
  const [filters, setFilters] = useState(() => filtersFromUrl(searchParams));

  // A link opened while this page is showing (e.g. from the help chat) changes
  // only the URL: re-read the filters from it.
  const urlKey = searchParams.toString();
  const urlKeyRef = useRef(urlKey);
  useEffect(() => {
    if (urlKeyRef.current === urlKey) return;
    urlKeyRef.current = urlKey;
    setFilters(filtersFromUrl(searchParams));
  }, [urlKey]);

  const [states, setStates] = useState([]);
  const [statesError, setStatesError] = useState(null);

  const loadStates = useCallback(() => {
    setStatesError(null);
    getStates().then(d => {
      setStates(d.states || []);
    }).catch(setStatesError);
  }, []);

  useEffect(() => { loadStates(); }, [loadStates]);

  const fetchQueue = useCallback(() => {
    setLoading(true);
    const params = { ...filters };
    Object.keys(params).forEach(k => {
      if (!params[k] || params[k] === '') delete params[k];
    });
    setError(null);
    getQueue(params)
      .then(setData)
      // Clear the previous page's rows: stale results under a failure
      // message would be read as the answer to the new filters.
      .catch((e) => { setData(null); setError(e); })
      .finally(() => setLoading(false));
  }, [filters]);

  useEffect(() => {
    fetchQueue();
  }, [fetchQueue]);

  const updateFilter = (key, value) => {
    setFilters(prev => ({ ...prev, [key]: value, ...(key !== 'page' && { page: 1 }) }));
  };

  const totalRecords = data?.total || 0;
  const startRecord = totalRecords > 0 ? ((filters.page - 1) * filters.page_size) + 1 : 0;
  const endRecord = Math.min(filters.page * filters.page_size, totalRecords);

  return (
    <div>
      <div className="page-header">
        <h2>{t('queue.title')}</h2>
        {!error && (
          <p className="subtitle">
            {t('queue.subtitle', { count: totalRecords.toLocaleString('en-IN') })}
          </p>
        )}
      </div>

      {/* Risk level pills */}
      <div className="toolbar-pills" style={{ marginBottom: 12 }}>
        {RISK_PILLS.map((p) => (
          <button
            key={p.value || 'all'}
            className={`pill-btn ${filters.risk_level === p.value ? 'active' : ''}`}
            data-tone={p.tone}
            onClick={() => updateFilter('risk_level', p.value)}
          >
            {t(p.labelKey)}
          </button>
        ))}
      </div>

      {/* Toolbar */}
      <div className="toolbar">
        <div className="toolbar-group">
          <span className="toolbar-label">{t('common.stage')}</span>
          <select className="toolbar-select" value={filters.stage} onChange={(e) => updateFilter('stage', e.target.value)}>
            <option value="">{t('common.allStages')}</option>
            <option value="RECOMMENDED">{t('stage.recommended')}</option>
            <option value="SANCTIONED">{t('stage.sanctioned')}</option>
            <option value="COMPLETED">{t('stage.completed')}</option>
          </select>
        </div>

        <div className="toolbar-group">
          <span className="toolbar-label">{t('common.state')}</span>
          <select className="toolbar-select" value={filters.state} onChange={(e) => updateFilter('state', e.target.value)}>
            <option value="">{t('common.allStates')}</option>
            {states.map(s => <option key={s} value={s}>{labels.state(s)}</option>)}
          </select>
          {statesError && <ErrorState compact what={t('queue.what.states')} error={statesError} onRetry={loadStates} />}
        </div>

        <div className="toolbar-group">
          <span className="toolbar-label">{t('common.sortBy')}</span>
          <select className="toolbar-select" value={filters.sort_by} onChange={(e) => updateFilter('sort_by', e.target.value)}>
            <option value="risk_score">{t('common.riskScore')}</option>
            <option value="cost_anomaly_score">{t('sig.cost')}</option>
            <option value="evidence_count">{t('queue.sort.evidence')}</option>
            <option value="amount_numeric">{t('common.amount')}</option>
          </select>
        </div>

        <div className="toolbar-group" style={{ flex: 1 }}>
          <span className="toolbar-label">{t('common.search')}</span>
          <div className="toolbar-search">
            <Search size={14} />
            <input
              type="text"
              className="toolbar-input"
              placeholder={t('queue.searchPlaceholder')}
              value={filters.search}
              onChange={(e) => updateFilter('search', e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && fetchQueue()}
            />
          </div>
        </div>
      </div>

      {/* Results Summary */}
      {!loading && data && (
        <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--text-muted)', display: 'flex', justifyContent: 'space-between' }}>
          <span>
            {t('queue.showing', { from: startRecord.toLocaleString('en-IN'), to: endRecord.toLocaleString('en-IN'), total: totalRecords.toLocaleString('en-IN') })}
          </span>
          <span>
            {t('common.pageOf', { page: data.page, pages: data.total_pages })}
          </span>
        </div>
      )}

      {/* Data Table */}
      {loading ? (
        <LoadingState variant="table" />
      ) : error ? (
        <ErrorState what={t('queue.what')} error={error} onRetry={fetchQueue} />
      ) : !data || data.records.length === 0 ? (
        <EmptyState message={t('queue.empty')} hint={t('queue.emptyHint')} />
      ) : (
        <div className="data-table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 80 }}>{t('common.riskLevel')}</th>
                <th style={{ width: 70 }}>{t('common.score')}</th>
                <th style={{ width: 110 }}>{t('common.workId')}</th>
                <th>{t('common.workDescription')}</th>
                <th style={{ width: 130 }}>{t('common.state')}</th>
                <th style={{ width: 150 }}>{t('common.mp')}</th>
                <th style={{ width: 120 }}>{t('common.constituency')}</th>
                <th style={{ width: 90 }}>{t('common.amount')}</th>
                <th style={{ width: 100 }}>{t('common.stage')}</th>
                <th style={{ width: 60 }}>{t('common.signals')}</th>
                <th style={{ width: 50 }}></th>
              </tr>
            </thead>
            <tbody>
              {data.records.map((record) => {
                // No score from the API = not evaluated: no default tier, no 0.
                const riskLevel = record.risk_level || record.priority || 'NOT_EVALUATED';
                const riskScore = record.risk_score != null ? record.risk_score : record.priority_score;
                const scoreDisplay = riskScore == null ? '—' : riskScore > 1 ? riskScore.toFixed(0) : (riskScore * 100).toFixed(0);
                return (
                  <tr
                    key={record.record_id}
                    className="clickable row-accent"
                    style={{ '--row-accent-color': ROW_ACCENT[riskLevel] || 'transparent' }}
                    onClick={() => navigate(`/record/${record.record_id}`)}
                  >
                    <td>
                      <RiskBadge priority={riskLevel} />
                    </td>
                    <td style={{ fontWeight: 600, fontFamily: "'IBM Plex Mono', monospace", fontSize: 12 }}>
                      {scoreDisplay}
                    </td>
                    <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, fontWeight: 500 }}>
                      {record.record_id}
                    </td>
                    <td style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {record.description || <span style={{ color: 'var(--text-faint)', fontStyle: 'italic' }}>{t('common.noDescription')}</span>}
                    </td>
                    <td>{labels.state(record.state)}</td>
                    <td style={{ fontSize: 12 }}>{labels.mpName(record.mp_name)}</td>
                    <td style={{ fontSize: 12 }}>{labels.consName(record.constituency)}</td>
                    <td style={{ fontWeight: 600, fontSize: 12 }}>
                      {record.amount_numeric != null ? `₹${(record.amount_numeric / 100000).toFixed(1)}${t('common.unit.lakh')}` : '-'}
                    </td>
                    <td>
                      <StageBadge stage={record.stage} />
                    </td>
                    <td style={{ textAlign: 'center', fontWeight: 600, color: record.evidence_count > 2 ? 'var(--risk-high)' : 'var(--text-muted)' }}>
                      {record.evidence_count || record.active_signal_count || 0}
                    </td>
                    <td>
                      <ExternalLink size={14} style={{ color: 'var(--text-faint)' }} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {data && data.total_pages > 1 && (
        <div className="pagination">
          <button
            disabled={filters.page <= 1}
            onClick={() => updateFilter('page', filters.page - 1)}
          >
            <ChevronLeft size={14} /> {t('common.previous')}
          </button>
          <span className="page-info">
            {t('common.pageOf', { page: data.page, pages: data.total_pages })}
          </span>
          <button
            disabled={filters.page >= data.total_pages}
            onClick={() => updateFilter('page', filters.page + 1)}
          >
            {t('common.next')} <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
