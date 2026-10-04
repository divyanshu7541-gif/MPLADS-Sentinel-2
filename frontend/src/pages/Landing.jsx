import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Map, Search, BarChart3, Users, Database, FileText, ArrowRight, ArrowDown,
  Inbox, Wand2, Radar, GitMerge, Gavel, ShieldCheck,
} from 'lucide-react';
import ChakraWatermark from '../components/ChakraWatermark';
import Reveal from '../components/Reveal';
import useCountUp from '../hooks/useCountUp';
import { getSummary, getConstituencies } from '../services/api';
import LanguageSelector from '../components/LanguageSelector';
import { useTranslation } from '../i18n';

// Live figures come only from /api/summary. There is no hard-coded
// fallback: if the call fails the figures read "—" with a visible
// "failed to load" notice, never numbers from some other dataset.
const NO_FIGURE = '—';

// Text lives in the locale files; these hold only the stable identity
// (number + icon + i18n keys) so steps re-label instantly on switch.
const PIPELINE_STEPS = [
  { n: '01', icon: Inbox,    titleKey: 'landing.step1.title', descKey: 'landing.step1.desc' },
  { n: '02', icon: Wand2,    titleKey: 'landing.step2.title', descKey: 'landing.step2.desc' },
  { n: '03', icon: Radar,    titleKey: 'landing.step3.title', descKey: 'landing.step3.desc' },
  { n: '04', icon: GitMerge, titleKey: 'landing.step4.title', descKey: 'landing.step4.desc' },
  { n: '05', icon: Gavel,    titleKey: 'landing.step5.title', descKey: 'landing.step5.desc' },
];

const SURFACES = [
  { icon: Map,       nameKey: 'nav.map',        descKey: 'landing.surface.map.desc' },
  { icon: Search,    nameKey: 'nav.queue',      descKey: 'landing.surface.queue.desc' },
  { icon: BarChart3, nameKey: 'nav.analytics',  descKey: 'landing.surface.analytics.desc' },
  { icon: Users,     nameKey: 'nav.browseMp',   descKey: 'landing.surface.mp.desc' },
  { icon: Database,  nameKey: 'nav.dataHealth', descKey: 'landing.surface.health.desc' },
  { icon: FileText,  nameKey: 'nav.methodology',descKey: 'landing.surface.method.desc' },
];

const NETWORK_NODES = [
  { x: 30, y: 40 }, { x: 90, y: 20 }, { x: 150, y: 55 }, { x: 210, y: 30 },
  { x: 260, y: 70 }, { x: 60, y: 100 }, { x: 130, y: 110 }, { x: 190, y: 95 },
  { x: 245, y: 130 }, { x: 40, y: 160 }, { x: 105, y: 175 }, { x: 165, y: 160 },
  { x: 225, y: 185 }, { x: 80, y: 220 }, { x: 150, y: 230 }, { x: 205, y: 220 },
];
const NETWORK_LINKS = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [1, 6], [2, 7], [3, 7], [4, 8],
  [5, 6], [6, 7], [7, 8], [5, 9], [6, 10], [7, 11], [8, 12], [9, 10],
  [10, 11], [11, 12], [9, 13], [10, 14], [11, 14], [12, 15], [13, 14], [14, 15],
];

const HEADLINE_KEYS = ['landing.hero.line1', 'landing.hero.line2', 'landing.hero.line3'];

function StatFigure({ label, value, suffix = '', prefix = '' }) {
  const animated = useCountUp(value, { duration: 1200 });
  const numeric = typeof animated === 'number';
  const formatted = numeric ? Math.round(animated).toLocaleString('en-IN') : animated;
  return (
    <div className="land-stat">
      <div className="land-stat-value">{numeric ? prefix : ''}{formatted}{numeric ? suffix : ''}</div>
      <div className="land-stat-label">{label}</div>
    </div>
  );
}

function TickerFigure({ value, label, suffix = '', prefix = '' }) {
  const animated = useCountUp(value, { duration: 1400 });
  const numeric = typeof animated === 'number';
  const formatted = numeric ? Math.round(animated).toLocaleString('en-IN') : animated;
  return (
    <div className="land-ticker-item">
      <span className="land-ticker-value">{numeric ? prefix : ''}{formatted}{numeric ? suffix : ''}</span>
      <span className="land-ticker-label">{label}</span>
    </div>
  );
}

function useScrolled(threshold = 24) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > threshold);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [threshold]);
  return scrolled;
}

export default function Landing() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [statsError, setStatsError] = useState(null);
  // Constituencies represented in the served data -- the same
  // /api/constituencies list the MP and Map searches use (it was a
  // hard-coded 509 before).
  const [constituencyCount, setConstituencyCount] = useState(null);
  const [consError, setConsError] = useState(null);
  const [heroReady, setHeroReady] = useState(false);
  const scrolled = useScrolled();
  const pipelineRef = useRef(null);

  useEffect(() => {
    getSummary()
      .then((s) => {
        if (s && typeof s.total_records === 'number') setStats(s);
        else setStatsError(new Error('Unexpected summary response'));
      })
      .catch(setStatsError);
    getConstituencies()
      .then((d) => {
        const names = (d.constituencies || []).map((c) => c.Constituency).filter(Boolean);
        setConstituencyCount(new Set(names).size);
      })
      .catch(setConsError);
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setHeroReady(true), 80);
    return () => clearTimeout(t);
  }, []);

  const headlineLines = HEADLINE_KEYS.map((k) => t(k));
  const totalWorks = stats ? stats.total_records : NO_FIGURE;
  const amountCr = stats ? Math.round((stats.total_amount || 0) / 10000000) : NO_FIGURE;
  // "Flagged" means HIGH + CRITICAL everywhere in the app (Dashboard, Map,
  // Analytics flag rates, MP profiles); MODERATE is "review recommended",
  // not flagged.
  const flaggedCount = stats ? (stats.critical_count || 0) + (stats.high_count || 0) : NO_FIGURE;

  return (
    <div className="landing">
      <ChakraWatermark />

      <header className={`land-nav ${scrolled ? 'scrolled' : ''}`}>
        <div className="land-nav-brand">
          <img src="/assets/mplads-sentinel-icon.png" alt="" className="land-nav-emblem" />
          <span>MPLADS SENTINEL</span>
        </div>
        <nav className="land-nav-links">
          <a href="#how-it-works">{t('landing.nav.howItWorks')}</a>
          <a href="#surfaces">{t('landing.nav.inside')}</a>
        </nav>
        <LanguageSelector variant="dark" />
        <button className="btn btn-primary land-nav-cta" onClick={() => navigate('/login')}>
          {t('landing.nav.cta')}
        </button>
      </header>

      {/* ---- Hero ---- */}
      <section className={`land-hero ${heroReady ? 'ready' : ''}`}>
        <div className="land-hero-backdrop" aria-hidden="true">
          <svg className="land-hero-network" viewBox="0 0 280 260" xmlns="http://www.w3.org/2000/svg">
            <g stroke="currentColor" strokeWidth="0.75" fill="none">
              {NETWORK_LINKS.map(([a, b], i) => {
                const p1 = NETWORK_NODES[a];
                const p2 = NETWORK_NODES[b];
                return <line key={i} x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} />;
              })}
            </g>
            <g fill="currentColor">
              {NETWORK_NODES.map((p, i) => (
                <circle key={i} cx={p.x} cy={p.y} r={i % 3 === 0 ? 3.2 : 2} />
              ))}
            </g>
          </svg>
        </div>

        <div className="land-hero-grid">
          <div className="land-hero-body">
            <div className="land-hero-kicker">
              <span className="dot" />
              {t('landing.hero.kicker')}
            </div>

            <h1 className="land-hero-headline" aria-label={headlineLines.join(' ')}>
              {headlineLines.map((line, i) => (
                <span className="land-hero-line-wrap" key={line}>
                  <span className="land-hero-line" style={{ transitionDelay: `${140 + i * 120}ms` }}>
                    {line}
                  </span>
                </span>
              ))}
            </h1>

            <p className="land-hero-sub">{t('landing.hero.sub')}</p>

            <div className="land-hero-actions">
              <button className="btn btn-primary land-hero-cta" onClick={() => navigate('/login')}>
                {t('landing.nav.cta')} <ArrowRight size={16} />
              </button>
              <a className="btn btn-ghost land-hero-ghost" href="#how-it-works">
                {t('landing.hero.seeHow')}
              </a>
            </div>
          </div>

          <div className="land-hero-seal">
            <div className="land-hero-seal-plate">
              <img
                src="/assets/mplads-sentinel-logo.jpeg"
                alt={t('landing.hero.logoAlt')}
                className="land-hero-seal-img"
              />
            </div>
          </div>
        </div>

        <div className="land-ticker">
          <TickerFigure value={totalWorks} label={t('landing.ticker.works')} />
          <span className="land-ticker-sep" />
          <TickerFigure value={amountCr} prefix="₹" suffix={t('landing.unit.cr')} label={t('landing.ticker.value')} />
          <span className="land-ticker-sep" />
          <TickerFigure value={constituencyCount ?? NO_FIGURE} label={t('landing.ticker.constituencies')} />
          <span className="land-ticker-sep" />
          <TickerFigure value={flaggedCount} label={t('landing.ticker.flagged')} />
        </div>
        {(statsError || consError) && (
          <div className="land-figures-failed" role="alert" data-testid="load-failed">
            {t('landing.figuresFailed')}
          </div>
        )}

        <a href="#what-is-mplads" className="land-scroll-cue" aria-label={t('landing.hero.scrollCue')}>
          <ArrowDown size={14} />
        </a>
      </section>

      {/* ---- What is MPLADS ---- */}
      <section className="land-section land-section-white" id="what-is-mplads">
        <div className="land-section-grid">
          <Reveal as="div" className="land-section-copy">
            <span className="land-eyebrow">{t('landing.scheme.eyebrow')}</span>
            <h2>{t('landing.scheme.heading')}</h2>
            <p className="land-dropcap">{t('landing.scheme.p1')}</p>
            <p>{t('landing.scheme.p2')}</p>
          </Reveal>
          <Reveal as="div" className="land-stat-rail" delay={120}>
            <StatFigure label={t('landing.scheme.stat1')} value={totalWorks} />
            <StatFigure label={t('landing.scheme.stat2')} value={amountCr} prefix="₹" suffix={t('landing.unit.cr')} />
            <StatFigure label={t('landing.scheme.stat3')} value={constituencyCount ?? NO_FIGURE} />
          </Reveal>
        </div>
      </section>

      {/* ---- Why oversight is hard ---- */}
      <section className="land-section land-section-muted land-problem">
        <div className="land-problem-grid">
          <Reveal as="div" className="land-scatter" aria-hidden="true">
            {Array.from({ length: 7 }).map((_, i) => (
              <div className={`land-scatter-card sc-${i}`} key={i}>
                {i === 3 && <span className="land-scatter-flag" />}
              </div>
            ))}
          </Reveal>
          <Reveal as="div" className="land-section-narrow" delay={80}>
            <span className="land-eyebrow">{t('landing.problem.eyebrow')}</span>
            <h2>{t('landing.problem.heading')}</h2>
            <p>{t('landing.problem.body')}</p>
          </Reveal>
        </div>
      </section>

      {/* ---- How Sentinel works ---- */}
      <section className="land-section land-section-white" id="how-it-works" ref={pipelineRef}>
        <Reveal as="div" style={{ textAlign: 'center' }}>
          <span className="land-eyebrow land-eyebrow-centered">{t('landing.method.eyebrow')}</span>
          <h2 className="land-section-heading-centered">{t('landing.method.heading')}</h2>
        </Reveal>
        <div className="land-pipeline">
          {PIPELINE_STEPS.map((step, i) => (
            <React.Fragment key={step.n}>
              <Reveal as="div" className="land-pipeline-step" delay={i * 90}>
                <div className="land-pipeline-icon"><step.icon size={18} /></div>
                <div className="land-pipeline-n">{step.n}</div>
                <div className="land-pipeline-title">{t(step.titleKey)}</div>
                <div className="land-pipeline-desc">{t(step.descKey)}</div>
              </Reveal>
              {i < PIPELINE_STEPS.length - 1 && (
                <Reveal as="div" className="land-pipeline-connector" delay={i * 90 + 60} aria-hidden="true" />
              )}
            </React.Fragment>
          ))}
        </div>
      </section>

      {/* ---- The principle, held alone ---- */}
      <section className="land-principle">
        <div className="land-principle-chakra" aria-hidden="true">
          <ShieldCheck size={22} />
        </div>
        <Reveal as="p" className="land-principle-statement">
          {t('landing.principle.statement')}
        </Reveal>
        <Reveal as="p" className="land-principle-caption" delay={100}>
          {t('landing.principle.caption')}
        </Reveal>
        <div className="land-principle-rule" aria-hidden="true">
          <span className="s" /><span className="w" /><span className="g" />
        </div>
      </section>

      {/* ---- Where you can look ---- */}
      <section className="land-section land-section-white" id="surfaces">
        <Reveal as="div" style={{ textAlign: 'center' }}>
          <span className="land-eyebrow land-eyebrow-centered">{t('landing.surfaces.eyebrow')}</span>
          <h2 className="land-section-heading-centered">{t('landing.surfaces.heading')}</h2>
        </Reveal>
        <div className="land-surfaces">
          {SURFACES.map(({ icon: Icon, nameKey, descKey }, i) => (
            <Reveal as="div" className="land-surface" key={nameKey} delay={i * 70}>
              <div className="land-surface-icon-chip"><Icon size={18} /></div>
              <div>
                <div className="land-surface-name">{t(nameKey)}</div>
                <div className="land-surface-desc">{t(descKey)}</div>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ---- Footer ---- */}
      <footer className="land-footer">
        <img src="/assets/mplads-sentinel-icon.png" alt="" className="land-footer-emblem" />
        <div className="land-footer-motto">पारदर्शिता • जवाबदेही • सत्यनिष्ठा</div>
        <div className="land-footer-tagline">{t('landing.footer.tagline')}</div>
        <div className="land-footer-row">
          <span>{t('landing.footer.credit')}</span>
          <button className="btn btn-primary" onClick={() => navigate('/login')}>
            {t('landing.nav.cta')} <ArrowRight size={16} />
          </button>
        </div>
      </footer>
    </div>
  );
}
