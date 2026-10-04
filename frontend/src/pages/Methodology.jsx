import React from 'react';
import { Shield, AlertTriangle, Search, Link2, BarChart3, FileText, ArrowDown } from 'lucide-react';
import { useTranslation } from '../i18n';

// The nine steps that produce the risk score (meth.flow.N), backend_v2/app/analytics.
const STEP_COLORS = [
  'var(--info)', 'var(--info)', 'var(--info)',
  'var(--risk-review)',
  'var(--risk-high)', 'var(--risk-high)', 'var(--risk-high)',
  'var(--success)', 'var(--success)',
];

// The six base signals and their v4-candidate weights (fusion.py V4_WEIGHTS:
// 5/17, 4/17, 2/17 x4), rounded to one decimal.
const SIGNALS = [
  { icon: <BarChart3 size={16} />, nameKey: 'sig.cost', descKey: 'meth.sig6.cost.d', weight: '29.4%' },
  { icon: <Search size={16} />, nameKey: 'sig.similarity', descKey: 'meth.sig6.similarity.d', weight: '23.5%', mlBadge: 'meth.badge.tfidf' },
  { icon: <Link2 size={16} />, nameKey: 'sig.mpConcentration', descKey: 'meth.sig6.mpConcentration.d', weight: '11.8%' },
  { icon: <Link2 size={16} />, nameKey: 'sig.constituencyPattern', descKey: 'meth.sig6.constituencyPattern.d', weight: '11.8%' },
  { icon: <AlertTriangle size={16} />, nameKey: 'sig.temporal', descKey: 'meth.sig6.temporal.d', weight: '11.8%' },
  { icon: <FileText size={16} />, nameKey: 'sig.stage', descKey: 'meth.sig6.stage.d', weight: '11.8%' },
];

// Computed offline, never read by fusion (gate G6). Drawn apart from the
// scoring steps: dashed, unfilled, lettered, no arrow into the score.
const EVIDENCE_ONLY_STEPS = [
  { letter: 'A', key: 'meth.flowEv.1', badges: ['meth.badge.evidenceOnly', 'meth.badge.notShown'] },
  { letter: 'B', key: 'meth.flowEv.2', badges: ['meth.badge.evidenceOnly', 'meth.badge.inactive'] },
];

const BADGE_STYLE = {
  fontSize: 10, color: 'var(--text-muted)', background: 'var(--bg-muted)',
  padding: '2px 6px', borderRadius: 'var(--radius-sm)', whiteSpace: 'nowrap',
};

const LIMITATIONS = [
  'meth.lim.1', 'meth.lim.2', 'meth.lim.3', 'meth.lim.peers', 'meth.lim.weights', 'meth.lim.6', 'meth.lim.7',
];
// Mirrors the evidence_chain the API serves (backend_v2/app/serving/service.py).
const EVIDENCE_CHAIN = ['meth.ev.1', 'meth.ev.2', 'meth.chain.3', 'meth.chain.4', 'meth.chain.5', 'meth.chain.6'];

export default function Methodology() {
  const { t } = useTranslation();

  return (
    <div>
      <div className="page-header">
        <h2>{t('nav.methodology')}</h2>
        <p className="subtitle">{t('meth.subtitle')}</p>
      </div>

      {/* Core Principle */}
      <div className="card" style={{ marginBottom: 16, borderLeft: '4px solid var(--gov-blue)', background: 'var(--gov-blue-subtle)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <Shield size={24} style={{ color: 'var(--gov-blue)' }} />
          <div>
            <h3 style={{ fontSize: 16, fontWeight: 700, color: 'var(--navy)' }}>{t('meth.principle')}</h3>
          </div>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7 }}>
          {t('meth.principleBody')}
        </p>
      </div>

      {/* Processing Pipeline */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <h3>{t('meth.pipeline')}</h3>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, marginBottom: 14 }}>
          {t('meth.flow.intro')}
        </p>
        <div>
          {STEP_COLORS.map((color, i) => {
            const step = i + 1;
            return (
              <div key={step}>
                <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
                  <div style={{
                    width: 32, height: 32, borderRadius: '50%',
                    background: color, color: '#fff',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 13, fontWeight: 600, flexShrink: 0
                  }}>{step}</div>
                  <div style={{ flex: 1, paddingTop: 4 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--navy)' }}>{t(`meth.flow.${step}.t`)}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{t(`meth.flow.${step}.d`)}</div>
                  </div>
                </div>
                {i < STEP_COLORS.length - 1 && (
                  <div className="pipeline-arrow" style={{ marginLeft: 15 }}>
                    <ArrowDown size={16} />
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Evidence-only steps: outside the risk score */}
        <div style={{
          marginTop: 20, padding: 16,
          border: '1px dashed var(--text-muted)', borderRadius: 'var(--radius-md)',
          background: 'var(--bg-subtle)'
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 12, textTransform: 'uppercase', letterSpacing: 0.4 }}>
            {t('meth.flowEv.heading')}
          </div>
          {EVIDENCE_ONLY_STEPS.map(({ letter, key, badges }, i) => (
            <div key={key} style={{ display: 'flex', gap: 16, alignItems: 'flex-start', marginTop: i === 0 ? 0 : 14 }}>
              <div style={{
                width: 32, height: 32, borderRadius: '50%',
                border: '2px dashed var(--text-muted)', color: 'var(--text-muted)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 13, fontWeight: 600, flexShrink: 0
              }}>{letter}</div>
              <div style={{ flex: 1, paddingTop: 4 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>{t(`${key}.t`)}</span>
                  {badges.map((b) => <span key={b} style={BADGE_STYLE}>{t(b)}</span>)}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{t(`${key}.d`)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Anomaly Signals */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <h3>{t('common.analyticalSignals')}</h3>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 14 }}>
          {SIGNALS.map(({ icon, nameKey, descKey, weight, mlBadge }) => (
            <div key={nameKey} style={{
              background: 'var(--bg-subtle)',
              padding: 16,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-light)',
              borderLeft: '3px solid var(--gov-blue)'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                {icon}
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--navy)' }}>{t(nameKey)}</span>
                {mlBadge && (
                  <span style={{ ...BADGE_STYLE, color: 'var(--gov-blue)', background: 'var(--gov-blue-subtle)' }}>{t(mlBadge)}</span>
                )}
                <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 'auto', background: 'var(--bg-muted)', padding: '2px 6px', borderRadius: 'var(--radius-sm)' }}>
                  {t('meth.weight', { w: weight })}
                </span>
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{t(descKey)}</p>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 10 }}>{t('meth.weightsNote')}</p>
      </div>

      {/* Risk vs Confidence */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <h3>{t('meth.rvc')}</h3>
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.8 }}>
          <p style={{ marginBottom: 8 }}><strong>{t('common.riskScore')}</strong> {t('meth.rvc.riskText')}</p>
          <p style={{ marginBottom: 8 }}><strong>{t('meth.rvc.confLabel')}</strong> {t('meth.rvc.confText')}</p>
          <p style={{ marginBottom: 8 }}>{t('meth.rvc.note')}</p>
          <p>
            {t('meth.rvc.bands')}{' '}
            <strong>{t('dash.risk.low').toUpperCase()}</strong> (0-39), <strong>{t('dash.risk.moderate').toUpperCase()}</strong> (40-64), <strong>{t('dash.risk.high').toUpperCase()}</strong> (65-84), <strong>{t('dash.risk.critical').toUpperCase()}</strong> (85-100). {t('meth.rvc.criticalNote')}
          </p>
        </div>
      </div>

      {/* Evidence Model */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <h3>{t('meth.evidence')}</h3>
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.8 }}>
          <p style={{ marginBottom: 12 }}>{t('meth.evidenceIntro')}</p>
          <div style={{
            background: 'var(--bg-subtle)',
            padding: 16,
            borderRadius: 'var(--radius-md)',
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: 12,
            lineHeight: 2,
            border: '1px solid var(--border-light)'
          }}>
            {EVIDENCE_CHAIN.map((key, i) => (
              <React.Fragment key={key}>
                {i === 0 ? null : <>&nbsp;&nbsp;&darr; </>}
                {t(key)}
                {i < EVIDENCE_CHAIN.length - 1 && <br />}
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>

      {/* Important Limitations */}
      <div className="card" style={{ borderLeft: '4px solid var(--risk-review)', background: 'var(--risk-review-bg)' }}>
        <div className="card-header" style={{ borderBottom: 'none', paddingBottom: 0 }}>
          <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--navy)' }}>
            <AlertTriangle size={16} style={{ color: 'var(--risk-review)' }} />
            {t('meth.limits')}
          </h3>
        </div>
        <ul style={{ fontSize: 13, color: 'var(--text-secondary)', paddingLeft: 20, lineHeight: 1.8 }}>
          {LIMITATIONS.map((key) => (
            <li key={key}>{t(key)}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
