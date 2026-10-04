import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Link2, FileText, ChevronRight, Shield, RefreshCw } from 'lucide-react';
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, Tooltip } from 'recharts';
import { getRecordDetail, recalculateRisk, isNotFound } from '../services/api';
import RiskBadge from '../components/RiskBadge';
import StageBadge from '../components/StageBadge';
import LoadingState from '../components/LoadingState';
import EmptyState from '../components/EmptyState';
import ErrorState from '../components/ErrorState';
import useCountUp from '../hooks/useCountUp';
import { useTranslation } from '../i18n';
import { useLabels } from '../i18n/labels';

const RISK_COLOR = {
  CRITICAL: 'var(--risk-high)',
  HIGH: '#c45a20',
  MODERATE: 'var(--risk-review)',
  LOW: 'var(--risk-low)',
};

const SIGNAL_AXIS_MAP = {
  COST_ANOMALY: 'cost',
  DESCRIPTION_SIMILARITY: 'description',
  MP_CONCENTRATION: 'mpConcentration',
  CONSTITUENCY_PATTERN: 'constituencyPattern',
  TEMPORAL_ANOMALY: 'temporal',
  STAGE_CONSISTENCY: 'stage',
  CROSS_SIGNAL_PATTERN: 'pattern',
};

const AXIS_ORDER = ['cost', 'description', 'mpConcentration', 'constituencyPattern', 'temporal', 'stage', 'pattern'];

// Radar axis labels (short forms; some reuse the full signal names).
const AXIS_LABEL_KEY = {
  cost: 'sig.cost',
  description: 'sigShort.similarity',
  mpConcentration: 'sig.mpConcentration',
  constituencyPattern: 'sig.constituencyPattern',
  temporal: 'sigShort.temporal',
  stage: 'sigShort.stage',
  pattern: 'sigShort.pattern',
};

// Field labels for the "Project Data" tab (display only).
const SOURCE_FIELD_KEYS = {
  record_id: 'common.recordId',
  mp: 'common.mp',
  constituency: 'common.constituency',
  state: 'common.state',
  description: 'common.description',
  amount: 'common.amount',
  date: 'common.date',
  stage: 'common.stage',
  house: 'house.label',
  district_authority: 'rec.districtAuthority',
};
const NORMALIZED_FIELD_KEYS = {
  amount_numeric: 'rec.amountNumeric',
  date_parsed: 'rec.dateParsed',
  inferred_category: 'rec.inferredCategory',
  category_confidence: 'rec.catConfidence',
  amount_tier: 'rec.amountTier',
  category_source: 'rec.categorySource',
};

const STRENGTH_VALUE = { HIGH: 100, MEDIUM: 60, LOW: 30 };

// Axes are only the signals that were actually evaluated for this work. A
// signal the API lists in `not_evaluated_signals` gets no axis (plotting it
// at 0 would claim "evaluated, nothing found"), and the legacy
// cross-signal-pattern axis appears only if the API sent that signal.
function buildSignalFingerprint(evidenceItems, notEvaluated, t) {
  const skip = new Set((notEvaluated || []).map((s) => SIGNAL_AXIS_MAP[s]).filter(Boolean));
  const present = new Set((evidenceItems || []).map((i) => SIGNAL_AXIS_MAP[i.signal_type]).filter(Boolean));
  const axes = AXIS_ORDER.filter((a) => !skip.has(a) && (a !== 'pattern' || present.has('pattern')));
  const magnitudes = Object.fromEntries(axes.map((a) => [a, 0]));
  (evidenceItems || []).forEach((item) => {
    const axis = SIGNAL_AXIS_MAP[item.signal_type];
    if (!axis || !(axis in magnitudes)) return;
    const v = STRENGTH_VALUE[item.signal_strength] || 20;
    if (v > magnitudes[axis]) magnitudes[axis] = v;
  });
  return axes.map((axis) => ({ axis: t(AXIS_LABEL_KEY[axis]), value: magnitudes[axis] }));
}

function ScoreRing({ score, color, size = 116 }) {
  const clamped = Math.max(0, Math.min(1, score || 0));
  const animated = useCountUp(clamped, { duration: 1000, decimals: 4 });
  const strokeWidth = 7;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - animated);

  return (
    <svg width={size} height={size}>
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgba(255,255,255,0.09)" strokeWidth={strokeWidth} />
      <circle
        cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={color} strokeWidth={strokeWidth}
        strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}

function ConfidenceBar({ score, size = 'md' }) {
  const { t } = useTranslation();
  const pct = Math.round((score || 0) * 100);
  const color = pct >= 80 ? 'var(--risk-low)' : pct >= 60 ? 'var(--risk-review)' : 'var(--risk-high)';
  return (
    <div style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{t('common.confidence')}</span>
        <span style={{ fontSize: 12, fontWeight: 600, fontFamily: 'var(--font-mono)', color }}>{pct}%</span>
      </div>
      <div style={{ height: 6, background: 'var(--border-light)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.6s ease' }} />
      </div>
    </div>
  );
}

export default function RecordDetail() {
  const { t } = useTranslation();
  const labels = useLabels();
  const { recordId } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('evidence');
  const [recalculating, setRecalculating] = useState(false);
  const [error, setError] = useState(null);
  const [recalcError, setRecalcError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getRecordDetail(recordId)
      .then(setDetail)
      .catch((e) => { setDetail(null); setError(e); })
      .finally(() => setLoading(false));
  }, [recordId]);

  useEffect(() => { load(); }, [load]);

  const handleRecalculate = async () => {
    setRecalculating(true);
    setRecalcError(null);
    try {
      await recalculateRisk(recordId);
      const updated = await getRecordDetail(recordId);
      setDetail(updated);
    } catch (e) {
      setRecalcError(e);
    }
    setRecalculating(false);
  };

  if (loading) return <LoadingState message={t('rec.loading')} />;
  // 404 = the record genuinely doesn't exist; anything else is a failure.
  if (error && isNotFound(error)) return <EmptyState message={t('rec.notFound')} />;
  if (error) return <ErrorState what={t('rec.what')} error={error} onRetry={load} />;
  if (!detail) return <EmptyState message={t('rec.notFound')} />;

  const { source_record, normalized_record, context, risk_assessment, evidence_items, evidence_chain, related_records, investigation_recommendation, risk_history } = detail;

  // Support both new (risk_assessment) and legacy (priority) formats. A
  // value the API doesn't send stays null -- never defaulted to 0 or LOW.
  const riskScore = risk_assessment?.risk_score
    ?? (detail.priority?.risk_score != null ? detail.priority.risk_score * 100 : null);
  const scored = riskScore != null && risk_assessment?.scored !== false;
  const riskLevel = scored
    ? (risk_assessment?.risk_level ?? detail.priority?.risk_level ?? detail.priority?.priority ?? null)
    : 'NOT_EVALUATED';
  const confidenceScore = risk_assessment?.confidence_percent
    ?? (risk_assessment?.confidence != null ? risk_assessment.confidence * 100
      : (detail.priority?.confidence_score != null ? detail.priority.confidence_score * 100 : null));
  const activeSignalCount = scored
    ? (risk_assessment?.active_signal_count ?? detail.priority?.evidence_count ?? null)
    : null;
  const scoreColor = RISK_COLOR[riskLevel] || 'var(--text-muted)';
  const notEvaluatedSignals = detail.not_evaluated_signals || [];
  const fingerprint = scored ? buildSignalFingerprint(evidence_items, notEvaluatedSignals, t) : [];

  // Derived features (backward compat)
  const derived = detail.derived_features || {};

  // Display helpers. Raw API values stay untouched; these only pick the text.
  const levelText = String(labels.riskShort(riskLevel)).toUpperCase();
  const sigName = (item) => {
    const translated = labels.signal(item.signal_type);
    return translated !== item.signal_type
      ? translated
      : (item.signal_label || item.signal_type.replace(/_/g, ' '));
  };
  const lakh = (v) => `${(v / 100000)?.toFixed(1)} ${t('common.unit.lakh')}`;
  const stepDescription = (step) => {
    switch (step.step) {
      case 'SOURCE RECORD': return t('rec.stepDesc.source', { id: step.details?.record_id ?? '' });
      // Prose steps: the API's own description (the old frontend text
      // claimed "seven independent signals" and a peer comparison even
      // where none existed).
      case 'NORMALIZED DATA': return step.description ?? '';
      case 'CONTEXT ENGINE': return step.description ?? '';
      case 'SIGNAL DETECTION': return t('rec.stepDesc.signals', { n: Array.isArray(step.details) ? step.details.length : 0 });
      case 'SIGNAL FUSION': return step.description ?? '';
      case 'RISK ASSESSMENT': return t('rec.stepDesc.risk', { level: String(labels.riskShort(step.details?.risk_level)).toUpperCase() });
      default: return step.description;
    }
  };

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 }}>
        <button className="btn btn-ghost" onClick={() => navigate(-1)} style={{ padding: '6px 10px' }}>
          <ArrowLeft size={18} />
        </button>
        <div style={{ flex: 1 }}>
          <h2 style={{ fontSize: 19, fontWeight: 600, color: 'var(--shell-800)', fontFamily: 'var(--font-display)' }}>{t('nav.caseReview')}</h2>
          <p className="subtitle">{t('rec.workId', { id: recordId })}</p>
        </div>
        <button
          className="btn btn-outline"
          onClick={handleRecalculate}
          disabled={recalculating}
          style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}
        >
          <RefreshCw size={14} className={recalculating ? 'spinning' : ''} />
          {t('rec.recalc')}
        </button>
      </div>

      <div className="case-layout">
        {/* Main column */}
        <div>
          {recalcError && (
            <div style={{ marginBottom: 16 }}>
              <div className="error-inline" role="alert"><AlertTriangle size={13} /><span>{t('rec.recalcFailed')}</span></div>
            </div>
          )}

          {/* Not evaluated: no score exists, so no number and no explanation
              beyond what the API itself says about why. */}
          {!scored && (
            <div className="card not-evaluated-card" style={{ marginBottom: 16 }} data-testid="not-evaluated">
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: risk_assessment?.not_scored_reason ? 8 : 0, color: 'var(--shell-800)' }}>
                {t('rec.notEvaluated.title')}
              </h3>
              {risk_assessment?.not_scored_reason && (
                <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  {risk_assessment.not_scored_reason}
                </p>
              )}
            </div>
          )}

          {/* Risk Assessment Banner */}
          {scored && riskLevel !== 'LOW' && (
            <div className="card" style={{
              marginBottom: 16,
              borderLeft: `4px solid ${scoreColor}`,
              background: riskLevel === 'CRITICAL' ? 'var(--risk-high-bg)' : 'var(--risk-review-bg)'
            }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8, color: 'var(--shell-800)' }}>
                <AlertTriangle size={16} style={{ color: scoreColor }} />
                {t('rec.banner', { score: riskScore.toFixed(0), level: levelText })}
              </h3>
              {/* Only the API's own explanation text: each contributing
                  signal with the sentence the backend wrote from that
                  signal's stored evidence. No frontend-authored summary; a
                  signal without explanation text shows just its name. */}
              {evidence_items && evidence_items.length > 0 && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }} data-testid="banner-explanations">
                  <strong>{t('rec.contributing')}</strong>
                  <ul style={{ margin: '4px 0 0 16px', paddingLeft: 0, lineHeight: 1.6 }}>
                    {evidence_items.slice(0, 3).map((item, i) => (
                      <li key={i}>
                        <strong style={{ fontWeight: 600 }}>{sigName(item)}</strong>
                        {item.explanation ? <> — {labels.explanation(item.explanation)}</> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Investigation Recommendation */}
          {investigation_recommendation && (
            <div className="card" style={{ marginBottom: 16, borderLeft: '4px solid var(--gov-blue)' }}>
              <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, color: 'var(--shell-800)' }}>{t('rec.actions')}</h3>
              <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                {labels.recommendations(investigation_recommendation)}
              </p>
            </div>
          )}

          {/* Tabs */}
          <div className="tabs">
            <button className={`tab ${activeTab === 'evidence' ? 'active' : ''}`} onClick={() => setActiveTab('evidence')}>
              {t('rec.tab.findings')}
            </button>
            <button className={`tab ${activeTab === 'chain' ? 'active' : ''}`} onClick={() => setActiveTab('chain')}>
              {t('rec.tab.chain')}
            </button>
            <button className={`tab ${activeTab === 'source' ? 'active' : ''}`} onClick={() => setActiveTab('source')}>
              {t('rec.tab.source')}
            </button>
            <button className={`tab ${activeTab === 'related' ? 'active' : ''}`} onClick={() => setActiveTab('related')}>
              {t('rec.tab.related', { n: related_records.length })}
            </button>
          </div>

          {/* Evidence Tab */}
          {activeTab === 'evidence' && (
            <div>
              {/* Signal Fingerprint */}
              <div className="card" style={{ marginBottom: 16 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 4, color: 'var(--shell-800)' }}>
                  {t('rec.fingerprint')}
                </h3>
                <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 8 }}>
                  {t('rec.fingerprintDesc')}
                </p>
                {fingerprint.length < 3 ? (
                  <p style={{ fontSize: 12.5, color: 'var(--text-muted)', padding: '12px 0' }}>{t('rec.fingerprintNone')}</p>
                ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <RadarChart data={fingerprint} outerRadius="72%">
                    <PolarGrid stroke="var(--chart-grid)" />
                    <PolarAngleAxis dataKey="axis" tick={{ fill: 'var(--text-secondary)', fontSize: 11, fontFamily: 'IBM Plex Sans, sans-serif' }} />
                    <PolarRadiusAxis angle={90} domain={[0, 100]} tick={false} axisLine={false} />
                    <Radar
                      dataKey="value"
                      stroke={scoreColor}
                      fill={scoreColor}
                      fillOpacity={0.22}
                      strokeWidth={2}
                      isAnimationActive={true}
                      animationDuration={700}
                    />
                    <Tooltip
                      formatter={(v) => [`${v}%`, t('rec.signalStrength')]}
                      contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid var(--border-light)', fontFamily: 'IBM Plex Sans, sans-serif' }}
                    />
                  </RadarChart>
                </ResponsiveContainer>
                )}
                {scored && notEvaluatedSignals.length > 0 && (
                  <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 4 }}>
                    {t('rec.notEvaluatedSignals', { list: notEvaluatedSignals.map((s) => labels.signalText(s)).join(', ') })}
                  </p>
                )}
              </div>

              {/* Project Identification */}
              <div className="card" style={{ marginBottom: 16 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--shell-800)' }}>
                  {t('rec.identification')}
                </h3>
                <div className="detail-grid">
                  <div className="detail-item">
                    <div className="label">{t('common.state')}</div>
                    <div className="value">{source_record.state}</div>
                  </div>
                  <div className="detail-item">
                    <div className="label">{t('common.mpFull')}</div>
                    <div className="value">{source_record.mp}</div>
                  </div>
                  <div className="detail-item">
                    <div className="label">{t('common.constituency')}</div>
                    <div className="value">{source_record.constituency}</div>
                  </div>
                  <div className="detail-item">
                    <div className="label">{t('common.amount')}</div>
                    <div className="value" style={{ fontWeight: 600 }}>₹{source_record.amount}</div>
                  </div>
                  <div className="detail-item">
                    <div className="label">{t('common.stage')}</div>
                    <div className="value"><StageBadge stage={source_record.stage} /></div>
                  </div>
                  <div className="detail-item">
                    <div className="label">{t('common.category')}</div>
                    <div className="value">{labels.category(normalized_record.inferred_category)}</div>
                  </div>
                </div>
                <div style={{ marginTop: 12 }}>
                  <div className="detail-item">
                    <div className="label">{t('common.workDescription')}</div>
                    <div className="value" style={{ lineHeight: 1.6 }}>{source_record.description}</div>
                  </div>
                </div>
              </div>

              {/* Context */}
              {context && context.peer_group_size > 0 && (
                <div className="card" style={{ marginBottom: 16 }}>
                  <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--shell-800)' }}>
                    {t('rec.peerCompare')}
                  </h3>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
                    {t('rec.peerGroup', { name: labels.peerLevel(context.peer_group_level_name) || t('rec.level', { n: context.peer_group_level }) })}
                  </div>
                  <div className="detail-grid">
                    <div className="detail-item">
                      <div className="label">{t('rec.peerSize')}</div>
                      <div className="value">{t('rec.comparableWorks', { n: context.peer_group_size })}</div>
                    </div>
                    <div className="detail-item">
                      <div className="label">{t('rec.peerMedianAmount')}</div>
                      <div className="value">₹{lakh(context.peer_median)}</div>
                    </div>
                    <div className="detail-item">
                      <div className="label">{t('rec.percentile')}</div>
                      <div className="value">{context.peer_percentile?.toFixed(1)}%</div>
                    </div>
                    <div className="detail-item">
                      <div className="label">{t('rec.deviationMedian')}</div>
                      <div className="value" style={{ color: context.deviation_ratio > 2 ? 'var(--risk-high)' : 'inherit', fontWeight: context.deviation_ratio > 2 ? 600 : 400 }}>
                        {context.deviation_ratio?.toFixed(2)}x
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Signal Breakdown */}
              <div className="card">
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--shell-800)' }}>
                  {t('rec.findingsCount', { n: evidence_items.length })}
                </h3>
                {!scored ? (
                  // Nothing was evaluated, so "no anomalies found" would be a
                  // claim the analysis never made.
                  <p style={{ fontSize: 12.5, color: 'var(--text-muted)', padding: '4px 0' }}>{t('rec.fingerprintNone')}</p>
                ) : evidence_items.length === 0 ? (
                  null
                ) : (
                  evidence_items.map((item, i) => (
                    <div key={i} className={`evidence-item ${item.signal_strength}`}>
                      <div className="signal-type">{sigName(item)}</div>
                      <div style={{ flex: 1 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span className={`confidence-badge ${item.signal_strength === 'HIGH' ? 'HIGH' : item.signal_strength === 'MEDIUM' ? 'MEDIUM' : 'LOW'}`}>
                            {String(labels.level(item.signal_strength)).toUpperCase()}
                          </span>
                          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                            {t('rec.scoreLabel', { pct: (item.signal_score * 100).toFixed(0) })}
                          </span>
                          {item.peer_group_size > 0 && (
                            <span style={{ fontSize: 10, color: 'var(--text-faint)' }}>
                              {t('rec.vsPeers', { n: item.peer_group_size })}
                            </span>
                          )}
                        </div>
                        <div className="explanation">{labels.explanation(item.explanation)}</div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* Evidence Chain Tab */}
          {activeTab === 'chain' && (
            <div className="card">
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8, color: 'var(--shell-800)' }}>
                <FileText size={16} />
                {t('rec.chainTitle')}
              </h3>
              {evidence_chain.map((step, i) => (
                <div key={i} className="chain-step">
                  <div className="step-number">{i + 1}</div>
                  <div className="step-content" style={{ flex: 1 }}>
                    <h4>{labels.chainStep(step.step)}</h4>
                    <p>{stepDescription(step)}</p>
                    {step.step === 'RISK ASSESSMENT' && (
                      <div style={{ marginTop: 8, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                        <div>
                          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{t('common.riskScore')}: </span>
                          <span style={{ fontWeight: 600, fontFamily: 'var(--font-mono)', color: scoreColor }}>{step.details.risk_score != null ? `${step.details.risk_score}/100` : t('rec.noScore')}</span>
                        </div>
                        <div>
                          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{t('rec.levelLabel')}: </span>
                          <RiskBadge priority={step.details.risk_level} />
                        </div>
                        <div>
                          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{t('common.confidence')}: </span>
                          <span>{typeof step.details.confidence === 'number' ? `${(step.details.confidence <= 1 ? step.details.confidence * 100 : step.details.confidence).toFixed(0)}%` : '—'}</span>
                        </div>
                      </div>
                    )}
                    {step.step === 'SIGNAL DETECTION' && step.details.length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        {step.details.map((sig, j) => (
                          <div key={j} style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 2 }}>
                            <span style={{ color: sig.signal_strength === 'HIGH' ? 'var(--risk-high)' : sig.signal_strength === 'MEDIUM' ? 'var(--risk-review)' : 'var(--text-muted)' }}>
                              {sigName(sig)}
                            </span>
                            {' '}- {labels.explanation(sig.explanation)}
                          </div>
                        ))}
                      </div>
                    )}
                    {step.step === 'SOURCE RECORD' && (
                      <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                        <div>{t('common.mp')}: {step.details.mp}</div>
                        <div>{t('common.constituency')}: {step.details.constituency}</div>
                        <div>{t('common.state')}: {step.details.state}</div>
                        <div>{t('common.amount')}: ₹{step.details.amount}</div>
                        <div>{t('common.stage')}: {labels.stageUpper(step.details.stage)}</div>
                      </div>
                    )}
                    {step.step === 'CONTEXT ENGINE' && (
                      <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                        <div>{t('rec.peerSizeRecords', { n: step.details.peer_group_size })}</div>
                        <div>{t('rec.peerMedian')}: ₹{lakh(step.details.peer_median)}</div>
                        <div>{t('rec.deviationRatio')}: {step.details.deviation_ratio?.toFixed(2)}x</div>
                      </div>
                    )}
                    {step.step === 'NORMALIZED DATA' && (
                      <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                        <div>{t('rec.amountNumeric')}: {step.details.amount_numeric}</div>
                        <div>{t('rec.inferredCategory')}: {labels.category(step.details.inferred_category)}</div>
                        <div>{t('rec.amountTier')}: {step.details.amount_tier}</div>
                      </div>
                    )}
                  </div>
                  {i < evidence_chain.length - 1 && (
                    <ChevronRight size={16} style={{ color: 'var(--text-faint)', flexShrink: 0, marginTop: 8 }} />
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Source Data Tab */}
          {activeTab === 'source' && (
            <div>
              <div className="card" style={{ marginBottom: 16 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--shell-800)' }}>{t('rec.originalRecord')}</h3>
                <div className="detail-grid">
                  {Object.entries(source_record).map(([key, value]) => (
                    <div className="detail-item" key={key}>
                      <div className="label">{SOURCE_FIELD_KEYS[key] ? t(SOURCE_FIELD_KEYS[key]) : key.replace(/_/g, ' ')}</div>
                      <div className="value">{value || <span style={{ color: 'var(--text-faint)' }}>{t('common.empty')}</span>}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="card">
                <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--shell-800)' }}>{t('rec.normalizedValues')}</h3>
                <div className="detail-grid">
                  {Object.entries(normalized_record).map(([key, value]) => (
                    <div className="detail-item" key={key}>
                      <div className="label">{NORMALIZED_FIELD_KEYS[key] ? t(NORMALIZED_FIELD_KEYS[key]) : key.replace(/_/g, ' ')}</div>
                      <div className="value">{value !== null && value !== '' ? (key === 'inferred_category' ? labels.category(String(value)) : String(value)) : <span style={{ color: 'var(--text-faint)' }}>{t('common.na')}</span>}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Related Records Tab */}
          {activeTab === 'related' && (
            <div className="card">
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8, color: 'var(--shell-800)' }}>
                <Link2 size={16} />
                {t('rec.tab.related', { n: related_records.length })}
              </h3>
              {related_records.length === 0 ? (
                <p style={{ color: 'var(--text-muted)', fontSize: 13, padding: 16 }}>{t('rec.noSimilar')}</p>
              ) : (
                related_records.map((rec, i) => (
                  <div
                    key={i}
                    className="related-record"
                    onClick={() => navigate(`/record/${rec.record_id}`)}
                  >
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                        <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, fontWeight: 500 }}>{rec.record_id}</span>
                        <span className="relationship-tag">{labels.relationship(rec.relationship)}</span>
                        <RiskBadge priority={rec.risk_level || rec.priority} />
                      </div>
                      <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{rec.description}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                        {rec.mp_name} | {labels.category(rec.category)} |{rec.amount ? `₹${rec.amount}` : '-'} | <StageBadge stage={rec.stage} />
                      </div>
                    </div>
                    <ChevronRight size={16} style={{ color: 'var(--text-faint)' }} />
                  </div>
                ))
              )}
            </div>
          )}

          {/* Risk History */}
          {risk_history && risk_history.length > 1 && (
            <div className="card" style={{ marginTop: 16 }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--shell-800)' }}>{t('rec.history')}</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {risk_history.map((entry, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px', background: 'var(--bg-subtle)', borderRadius: 'var(--radius-md)' }}>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                      {new Date(entry.timestamp).toLocaleDateString()}
                    </span>
                    <span style={{ fontSize: 13, fontWeight: 600, fontFamily: 'var(--font-mono)', color: entry.risk_score >= 0.6 ? 'var(--risk-high)' : 'var(--text-secondary)' }}>
                      {entry.risk_score != null ? (entry.risk_score * 100).toFixed(0) : '—'}
                    </span>
                    <RiskBadge priority={entry.risk_level} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Sticky case summary rail */}
        <div className="case-rail">
          <div className="case-summary-card">
            <div className="case-id">{source_record.record_id}</div>
            <div className="case-score-hero" style={{ color: scoreColor }}>
              {scored ? (
                <div className="ring-wrap">
                  <ScoreRing score={riskScore / 100} color={scoreColor} />
                  <div className="num">{riskScore.toFixed(0)}</div>
                </div>
              ) : (
                <div className="no-score">{t('rec.noScore')}</div>
              )}
              <div className="lbl">{t('common.riskScore')}</div>
            </div>
            <div className="case-summary-row">
              <span className="k">{t('common.riskLevel')}</span>
              <span className="v"><RiskBadge priority={riskLevel} size="lg" /></span>
            </div>
            <div className="case-summary-row">
              <span className="k">{t('common.confidence')}</span>
              <span className="v" style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{confidenceScore != null ? `${confidenceScore.toFixed(0)}%` : '—'}</span>
            </div>
            <div className="case-summary-row">
              <span className="k">{t('rec.activeSignals')}</span>
              <span className="v">{activeSignalCount != null ? activeSignalCount : '—'}</span>
            </div>
            <div className="case-summary-row">
              <span className="k">{t('common.stage')}</span>
              <span className="v"><StageBadge stage={source_record.stage} /></span>
            </div>
          </div>

          <div className="card">
            <h3 style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 10, color: 'var(--shell-800)' }}>{t('rec.glance')}</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-muted)' }}>{t('common.state')}</span>
                <span style={{ fontWeight: 600 }}>{source_record.state}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-muted)' }}>{t('common.mp')}</span>
                <span style={{ fontWeight: 600, textAlign: 'right' }}>{source_record.mp}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-muted)' }}>{t('common.amount')}</span>
                <span style={{ fontWeight: 600, fontFamily: 'var(--font-mono)' }}>₹{source_record.amount}</span>
              </div>
              {context && context.peer_group_size > 0 && (
                <>
                  <div style={{ borderTop: '1px solid var(--border-light)', paddingTop: 8, marginTop: 4 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--shell-800)', marginBottom: 4 }}>{t('rec.peerContext')}</div>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text-muted)' }}>{t('rec.peerGroupShort')}</span>
                    <span style={{ fontWeight: 600 }}>{context.peer_group_size}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text-muted)' }}>{t('rec.peerMedian')}</span>
                    <span style={{ fontWeight: 600 }}>₹{(context.peer_median / 100000)?.toFixed(1)}{t('common.unit.lakh')}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text-muted)' }}>{t('rec.deviationShort')}</span>
                    <span style={{ fontWeight: 600, color: context.deviation_ratio > 2 ? 'var(--risk-high)' : 'inherit' }}>
                      {context.deviation_ratio?.toFixed(2)}x
                    </span>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}