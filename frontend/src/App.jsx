import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Link, NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard, Search, BarChart3, FileText,
  Database, Map, Users, Menu, Home
} from 'lucide-react';
import ChakraWatermark from './components/ChakraWatermark';
import LanguageSelector from './components/LanguageSelector';
import HouseToggle, { HouseProvider, useHouse } from './components/HouseToggle';
import { useTranslation } from './i18n';
import HelpChatbot from './components/chatbot/HelpChatbot';
import Login from './pages/Login';
import Landing from './pages/Landing';
import Dashboard from './pages/Dashboard';
import InvestigationQueue from './pages/InvestigationQueue';
import RecordDetail from './pages/RecordDetail';
import Analytics from './pages/Analytics';
import DataHealth from './pages/DataHealth';
import Methodology from './pages/Methodology';
import MapPage from './pages/Map';
import MPPerformance from './pages/MPPerformance';

// labelKey/sectionKey are i18n keys resolved at render time, so the
// sidebar re-labels instantly when the language changes.
const NAV_SECTIONS = [
  {
    sectionKey: 'nav.monitor',
    items: [
      { path: '/dashboard', labelKey: 'nav.overview', icon: LayoutDashboard, end: true },
    ],
  },
  {
    sectionKey: 'nav.investigate',
    items: [
      { path: '/map', labelKey: 'nav.map', icon: Map },
      { path: '/queue', labelKey: 'nav.queue', icon: Search },
    ],
  },
  {
    sectionKey: 'nav.analyze',
    items: [
      { path: '/analytics', labelKey: 'nav.analytics', icon: BarChart3 },
      { path: '/mp-performance', labelKey: 'nav.browseMp', icon: Users },
    ],
  },
  {
    sectionKey: 'nav.system',
    items: [
      { path: '/data-health', labelKey: 'nav.dataHealth', icon: Database },
      { path: '/methodology', labelKey: 'nav.methodology', icon: FileText },
    ],
  },
];

const PAGE_META = {
  '/dashboard': { titleKey: 'nav.overview', crumbKey: 'nav.monitor' },
  '/map': { titleKey: 'nav.map', crumbKey: 'nav.investigate' },
  '/queue': { titleKey: 'nav.queue', crumbKey: 'nav.investigate' },
  '/analytics': { titleKey: 'nav.analytics', crumbKey: 'nav.analyze' },
  '/mp-performance': { titleKey: 'nav.browseMp', crumbKey: 'nav.analyze' },
  '/data-health': { titleKey: 'nav.dataHealth', crumbKey: 'nav.system' },
  '/methodology': { titleKey: 'nav.methodology', crumbKey: 'nav.system' },
};

function useClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function Sidebar({ open, onClose }) {
  const { t } = useTranslation();
  return (
    <aside className={`shell-sidebar ${open ? 'open' : ''}`}>
      <div className="shell-brand">
        <div className="shell-emblem">
          <img src="/assets/mplads-sentinel-icon.png" alt={t('shell.emblemAlt')} className="shell-emblem-img" />
        </div>
        <div className="shell-brand-text">
          <h1>MPLADS SENTINEL</h1>
          <div className="tag">{t('shell.brandTag')}</div>
        </div>
      </div>
      <div className="shell-brand-motto">पारदर्शिता • जवाबदेही • सत्यनिष्ठा</div>

      <nav className="shell-nav">
        {NAV_SECTIONS.map((section) => (
          <div className="shell-nav-section" key={section.sectionKey}>
            <div className="shell-nav-section-label">{t(section.sectionKey)}</div>
            {section.items.map(({ path, labelKey, icon: Icon, end }) => (
              <NavLink
                key={path}
                to={path}
                end={!!end}
                onClick={onClose}
                className={({ isActive }) => `shell-nav-link ${isActive ? 'active' : ''}`}
                onMouseMove={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  e.currentTarget.style.setProperty('--mx', `${e.clientX - rect.left}px`);
                  e.currentTarget.style.setProperty('--my', `${e.clientY - rect.top}px`);
                }}
              >
                <Icon size={16} />
                {t(labelKey)}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="shell-footer">
        <div className="shell-status">
          <span className="dot" />
          {t('shell.statusActive')}
        </div>
        <div className="shell-version">{t('shell.version')}</div>
      </div>
    </aside>
  );
}

function Topbar({ onMenuClick }) {
  const location = useLocation();
  const now = useClock();
  const { t, lang } = useTranslation();
  const meta = PAGE_META[location.pathname] || (location.pathname.startsWith('/record/')
    ? { titleKey: 'nav.caseReview', crumbKey: 'nav.investigate' }
    : { title: 'MPLADS Sentinel', crumbKey: '' });

  const timeStr = now.toLocaleTimeString(`${lang}-IN`, { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' });
  const dateStr = now.toLocaleDateString(`${lang}-IN`, { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' });

  return (
    <header className="shell-topbar">
      <div className="shell-topbar-title">
        <button className="shell-menu-btn" onClick={onMenuClick} aria-label={t('shell.openNav')}>
          <Menu size={18} />
        </button>
        <Link to="/" className="shell-home-btn" aria-label={t('shell.home')} title={t('shell.homeTitle')}>
          <Home size={17} />
        </Link>
        {meta.crumbKey && <span className="crumb">{t(meta.crumbKey)} /</span>}
        <h2>{meta.titleKey ? t(meta.titleKey) : meta.title}</h2>
      </div>
      <div className="shell-topbar-right">
        <div className="shell-gov-chip">
          <span className="flag"><span className="s" /><span className="w" /><span className="g" /></span>
          <span>{t('shell.portal')}</span>
        </div>
        <HouseToggle />
        <LanguageSelector variant="light" />
        <div className="shell-clock">
          <div className="time">{timeStr} IST</div>
          <div className="date">{dateStr}</div>
        </div>
      </div>
    </header>
  );
}

function AppLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const { house } = useHouse();

  useEffect(() => { setMenuOpen(false); }, [location.pathname]);

  return (
    <div className="app-shell">
      <ChakraWatermark />
      <div className="shell-body">
        <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />
        <div className={`shell-overlay ${menuOpen ? 'open' : ''}`} onClick={() => setMenuOpen(false)} />
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Topbar onMenuClick={() => setMenuOpen(true)} />
          <main className="shell-content">
            {/* House is part of the key so the current page re-fetches with the new filter. */}
            <div key={`${location.pathname}|${house || 'ALL'}`} className="page-transition">
              <Routes>
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/map" element={<MapPage />} />
                <Route path="/queue" element={<InvestigationQueue />} />
                <Route path="/record/:recordId" element={<RecordDetail />} />
                <Route path="/analytics" element={<Analytics />} />
                <Route path="/mp-performance" element={<MPPerformance />} />
                <Route path="/data-health" element={<DataHealth />} />
                <Route path="/methodology" element={<Methodology />} />
              </Routes>
            </div>
          </main>
        </div>
      </div>
      <HelpChatbot />
    </div>
  );
}

export default function App() {
  return (
    <HouseProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />
          <Route path="/*" element={<AppLayout />} />
        </Routes>
      </BrowserRouter>
    </HouseProvider>
  );
}
