import { useMemo } from 'react';
import { useTranslation } from './index';

// Display-only lookups from backend enum values to i18n keys. The raw value
// is always what the app stores and sends; these only decide what text a
// person sees. Anything not listed here falls back to the raw value, so new
// or unexpected API values still render.

const RISK_SHORT = {
  CRITICAL: 'dash.risk.critical',
  HIGH: 'dash.risk.high',
  MODERATE: 'dash.risk.moderate',
  LOW: 'dash.risk.low',
  NOT_EVALUATED: 'dash.risk.notEvaluated',
  HIGH_PRIORITY_REVIEW: 'dash.risk.critical',
  REVIEW_RECOMMENDED: 'dash.risk.moderate',
};

const RISK_BADGE = {
  CRITICAL: 'risk.critical',
  HIGH: 'risk.high',
  MODERATE: 'risk.moderate',
  LOW: 'risk.low',
  NOT_EVALUATED: 'risk.notEvaluated',
  HIGH_PRIORITY_REVIEW: 'risk.critical',
  REVIEW_RECOMMENDED: 'risk.moderate',
};

const STAGE = {
  RECOMMENDED: 'stage.recommended',
  SANCTIONED: 'stage.sanctioned',
  COMPLETED: 'stage.completed',
};

const LEVEL = { HIGH: 'level.high', MEDIUM: 'level.medium', LOW: 'level.low' };

const CATEGORY = {
  'Road & Infrastructure': 'cat.road',
  'Water & Sanitation': 'cat.water',
  'Community Building': 'cat.community',
  Education: 'cat.education',
  Health: 'cat.health',
  'Electricity & Lighting': 'cat.electricity',
  'Park & Environment': 'cat.park',
  'Sports & Culture': 'cat.sports',
  'Other Infrastructure': 'cat.otherInfra',
  Other: 'cat.other',
};

// Keyed by the normalised form (upper-case, non-alphanumerics -> "_") so the
// same lookup serves "COST_ANOMALY", "cost_anomaly" and "Cost Anomaly".
const SIGNAL = {
  COST_ANOMALY: 'sig.cost',
  DESCRIPTION_SIMILARITY: 'sig.similarity',
  MP_CONCENTRATION: 'sig.mpConcentration',
  CONSTITUENCY_PATTERN: 'sig.constituencyPattern',
  TEMPORAL: 'sig.temporal',
  TEMPORAL_ANOMALY: 'sig.temporal',
  STAGE_CONSISTENCY: 'sig.stage',
  CROSS_SIGNAL_PATTERN: 'sig.pattern',
};

const UNAVAILABLE = {
  'Contractor Analysis (no contractor data in source)': 'health.unavail.contractor',
  'Tender/Procurement Analysis (no tender data in source)': 'health.unavail.tender',
  'Payment Transaction Analysis (no payment data in source)': 'health.unavail.payment',
  'Beneficiary Analysis (no beneficiary data in source)': 'health.unavail.beneficiary',
  'Implementing Agency Analysis (no agency data in source)': 'health.unavail.agency',
  'Work-level GPS Anomaly Detection (only constituency-level coordinates available)': 'health.unavail.gps',
  'Physical Progress Verification (no progress percentage data in source)': 'health.unavail.progress',
};

const CHAIN_STEP = {
  'SOURCE RECORD': 'rec.step.source',
  'NORMALIZED DATA': 'rec.step.normalized',
  'CONTEXT ENGINE': 'rec.step.context',
  'SIGNAL DETECTION': 'rec.step.signals',
  'SIGNAL FUSION': 'rec.step.fusion',
  'RISK ASSESSMENT': 'rec.step.risk',
};

const RELATIONSHIP = {
  'Same MP, Same Category': 'rec.rel.mpCat',
  'Potential Duplicate': 'rec.rel.dup',
  'Same Constituency, Same Category': 'rec.rel.constCat',
};

const ACTION = {
  'Review cost estimates against comparable projects in the same area and category': 'rec.action.cost',
  'Examine potentially duplicate or templated work descriptions for consistency': 'rec.action.similarity',
  "Review the MP's concentration of works in this category for unusual patterns": 'rec.action.mpConcentration',
  'Analyze constituency-level patterns in work categories and amount distributions': 'rec.action.constituencyPattern',
  'Investigate temporal clustering for potential batch-processing concerns': 'rec.action.temporal',
  'Verify work stage status and cross-reference with implementation records': 'rec.action.stage',
  'Multiple independent indicators suggest this project warrants priority review': 'rec.action.pattern',
  'Standard review recommended': 'rec.action.standard',
};

const normSignal = (raw) => String(raw).trim().toUpperCase().replace(/[^A-Z0-9]+/g, '_');

// ---- Backend-generated evidence sentences --------------------------------
// The API returns these as English strings built from fixed templates (see
// backend/app/features/*.py). We recognise each template exactly and render
// it from i18n keys, reusing the captured numbers/names verbatim. Anything
// that doesn't match a known template is shown unchanged.
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const COST_DIST = [
  'slightly outside the normal range',
  'notably outside the peer distribution',
  'far outside the peer distribution',
  'extremely far outside the peer distribution',
  'approaching the extreme edge of the peer distribution',
];
const COST_DISP = [
  'a moderately dispersed peer distribution',
  'a relatively tight peer distribution',
  'a tightly clustered peer distribution',
  'an extremely tight peer distribution',
];
const PEER_LEVELS = {
  'No peer group': 0,
  'State + Constituency + Category + Year': 1,
  'State + Category + Year': 2,
  'State + Category': 3,
  'National Category': 4,
};
const LIFECYCLE = [
  'SANCTIONED but not yet COMPLETED for this MP+Constituency',
  'COMPLETED without SANCTIONED record',
  'RECOMMENDED while COMPLETED works exist for same MP+Constituency',
];

const COST_RE = new RegExp(
  '^Project amount is (\\S+)x the contextual peer median of Rs\\.([\\d,]+) and lies ('
  + COST_DIST.map(esc).join('|') + ') (' + COST_DISP.map(esc).join('|')
  + ') \\((\\d+) peers at (.+?) level\\)\\.$'
);
const DUP_RE = /^Exact description match with (\d+) other works by same MP( in same constituency)?( with similar amounts)?$/;
const DUP_NEAR_RE = /^Similar description pattern shared with (\d+) works by same MP$/;
const MP_CONC_RE = /^MP '(.*)' has (\d+) '(.*)' works \(avg: ([\d.]+), ratio: ([\d.]+)x\)$/;
const CONST_RE = /^(.*): (\d+) '(.*)' works \(([\d.]+)% of total, amount ratio: ([\d.]+)x national avg\)$/;
const TEMP_DATE_RE = /^(\d+) works on (\d{2}\/\d{2}\/\d{4}) \(average: ([\d.]+)\)$/;
const TEMP_Q_RE = /^MP has (\d+) works in (.+?) \(average: ([\d.]+)\)$/;
const PATTERN_RE = /^Corroborated pattern: (.+) \((\d+) independent signals\)$/;

// The cost sentence writes level names without spaces ("State+Category");
// the context endpoint writes them with spaces. Normalise before lookup.
const levelIndex = (name) => PEER_LEVELS[String(name).replace(/\s*\+\s*/g, ' + ')];

export function makeLabels(t, lang) {
  const tr = (key, fallback) => {
    const s = t(key);
    return s === key ? fallback : s;
  };
  const look = (map, raw) => (raw != null && map[raw] ? tr(map[raw], raw) : raw);

  const unitCr = t('common.unit.cr');
  const unitL = t('common.unit.lakh');

  return {
    t,
    lang,
    unitCr,
    unitL,
    riskShort: (v) => look(RISK_SHORT, v),
    riskBadge: (v) => look(RISK_BADGE, v),
    // State names: `state.<raw name>` keys; unknown names show as-is.
    state: (v) => (v != null && v !== '' ? tr(`state.${v}`, v) : v),
    // MP / constituency names: `mp.<raw>` / `cons.<raw>` keys; English and
    // any name without an entry show the raw value.
    mpName: (v) => (v ? tr(`mp.${v}`, v) : v),
    consName: (v) => (v ? tr(`cons.${v}`, v) : v),
    stage: (v) => look(STAGE, v),
    // Stage as it was shown before i18n (upper-case); unknown values untouched.
    stageUpper: (v) => (v != null && STAGE[v] ? tr(STAGE[v], v).toUpperCase() : v),
    level: (v) => look(LEVEL, v),
    category: (v) => look(CATEGORY, v),
    signal: (v) => (v != null && SIGNAL[normSignal(v)] ? tr(SIGNAL[normSignal(v)], v) : v),
    // Same as `signal`, but unknown values render the way the pages did
    // before (underscores shown as spaces).
    signalText: (v) => (v != null && SIGNAL[normSignal(v)] ? tr(SIGNAL[normSignal(v)], v) : String(v).replace(/_/g, ' ')),
    unavailable: (v) => look(UNAVAILABLE, v),
    chainStep: (v) => look(CHAIN_STEP, v),
    relationship: (v) => look(RELATIONSHIP, v),
    // Peer-group level name from the context endpoint.
    peerLevel: (name) => {
      if (lang === 'en' || name == null) return name;
      const idx = levelIndex(name);
      return idx != null ? t(`ex.level.${idx}`) : name;
    },
    // Translates a backend evidence sentence when it matches a known
    // template; otherwise (and always in English) returns it unchanged.
    explanation: (text) => {
      if (lang === 'en' || typeof text !== 'string' || !text) return text;
      let m;
      if ((m = COST_RE.exec(text))) {
        const idx = levelIndex(m[6]);
        return t('ex.cost', {
          r: m[1], pm: m[2],
          dist: t(`ex.dist.${COST_DIST.indexOf(m[3]) + 1}`),
          disp: t(`ex.disp.${COST_DISP.indexOf(m[4]) + 1}`),
          ps: m[5],
          ln: idx != null ? t(`ex.level.${idx}`) : m[6],
        });
      }
      if ((m = DUP_RE.exec(text))) {
        return t(`ex.dup.${(m[2] ? 1 : 0) + (m[3] ? 2 : 0)}`, { n: m[1] });
      }
      if ((m = DUP_NEAR_RE.exec(text))) return t('ex.dupNear', { n: m[1] });
      if ((m = MP_CONC_RE.exec(text))) {
        return t('ex.mpConc', { mp: m[1], cnt: m[2], cat: look(CATEGORY, m[3]), avg: m[4], ratio: m[5] });
      }
      if ((m = CONST_RE.exec(text))) {
        return t('ex.constPat', { const: m[1], cnt: m[2], cat: look(CATEGORY, m[3]), pct: m[4], ratio: m[5] });
      }
      if ((m = TEMP_DATE_RE.exec(text))) return t('ex.tempDate', { count: m[1], date: m[2], avg: m[3] });
      if ((m = TEMP_Q_RE.exec(text))) return t('ex.tempQuarter', { count: m[1], period: m[2], avg: m[3] });
      const lf = LIFECYCLE.indexOf(text);
      if (lf >= 0) return t(`ex.life.${lf + 1}`);
      if ((m = PATTERN_RE.exec(text))) {
        const list = m[1].split(', ').map((s) => (SIGNAL[normSignal(s)] ? tr(SIGNAL[normSignal(s)], s) : s)).join(', ');
        return t('ex.pattern', { list, n: m[2] });
      }
      return text;
    },
    // The backend joins its recommendation sentences with "; ".
    recommendations: (text) =>
      typeof text === 'string'
        ? text.split('; ').map((s) => look(ACTION, s)).join('; ')
        : text,
    // Rupee amounts in Indian units (Cr / L). Number grouping stays en-IN.
    inr: (amount) => {
      if (!amount || amount === 0) return '-';
      if (amount >= 10000000) return '₹' + (amount / 10000000).toFixed(1) + ' ' + unitCr;
      if (amount >= 100000) return '₹' + (amount / 100000).toFixed(1) + ' ' + unitL;
      return '₹' + amount.toLocaleString('en-IN');
    },
    // BCP-47 tag used for date/time formatting.
    locale: `${lang}-IN`,
  };
}

export function useLabels() {
  const { t, lang } = useTranslation();
  return useMemo(() => makeLabels(t, lang), [t, lang]);
}
