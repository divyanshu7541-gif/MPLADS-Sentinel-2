import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Mail, Lock, Eye, EyeOff, ArrowRight, ShieldCheck, User, Landmark,
  Building2, FileCheck2, Vote, ChevronDown, KeyRound, CheckCircle2,
} from 'lucide-react';
import ChakraWatermark from '../components/ChakraWatermark';
import LanguageSelector from '../components/LanguageSelector';
import { useTranslation } from '../i18n';

// Government roles for the dropdown. Every role currently resolves to the
// same existing Dashboard (no auth/RBAC yet) -- kept distinct so branching
// on role later is trivial.
const GOV_ROLES = [
  { value: 'district', labelKey: 'portal.gov.role.district', icon: Building2 },
  { value: 'state', labelKey: 'portal.gov.role.state', icon: Landmark },
  { value: 'ministry', labelKey: 'portal.gov.role.ministry', icon: FileCheck2 },
  { value: 'mp', labelKey: 'portal.gov.role.mp', icon: Vote },
];

const DEMO_EMAIL = 'demo@mplads-sentinel.gov.in';
const DEMO_PASSWORD = 'demo123';

export default function Login() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [portalType, setPortalType] = useState('citizen');
  const [govRole, setGovRole] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');

  const roleRequired = portalType === 'government';

  const fillDemoCredentials = () => {
    setEmail(DEMO_EMAIL);
    setPassword(DEMO_PASSWORD);
    setError('');
  };

  const handleSubmit = (e) => {
    e.preventDefault();

    if (roleRequired && !govRole) {
      setError(t('login.error.role'));
      return;
    }
    if (!email.trim() || !password.trim()) {
      setError(t('login.error.fields'));
      return;
    }

    // No real authentication/backend yet -- any non-empty credentials (the
    // demo credentials included) proceed straight to the existing Dashboard.
    navigate('/dashboard', { state: { portal: portalType, role: govRole || null } });
  };

  return (
    <div className="login-page">
      <ChakraWatermark />

      <div className="login-shell">
        {/* ---- Left: brand panel ---- */}
        <aside className="login-brand-panel">
          <div className="login-brand-glow" aria-hidden="true" />
          <div className="login-brand-top">
            <img src="/assets/mplads-sentinel-icon.png" alt="" className="login-brand-emblem" />
            <div>
              <h1>MPLADS SENTINEL</h1>
              <span className="login-brand-tag">{t('login.brand.tagline')}</span>
            </div>
          </div>

          <div className="login-brand-mid">
            <h2>{t('login.heading')}</h2>
            <ul className="login-brand-points">
              <li><CheckCircle2 size={16} /> {t('login.brand.point1')}</li>
              <li><CheckCircle2 size={16} /> {t('login.brand.point2')}</li>
              <li><CheckCircle2 size={16} /> {t('login.brand.point3')}</li>
            </ul>
          </div>

          <div className="login-brand-motto">पारदर्शिता • जवाबदेही • सत्यनिष्ठा</div>
        </aside>

        {/* ---- Right: login card ---- */}
        <main className="login-form-panel">
          <div className="login-form-topbar">
            <LanguageSelector />
          </div>

          <div className="login-card">
            <div className="login-card-mobile-brand">
              <img src="/assets/mplads-sentinel-icon.png" alt="" />
              <span>MPLADS SENTINEL</span>
            </div>

            <h2 className="login-card-heading">{t('login.heading')}</h2>
            <p className="login-card-sub">{t('login.sub')}</p>

            <div className="login-portal-toggle" role="tablist" aria-label="Portal">
              <button
                type="button"
                role="tab"
                aria-selected={portalType === 'citizen'}
                className={portalType === 'citizen' ? 'active' : ''}
                onClick={() => { setPortalType('citizen'); setError(''); }}
              >
                <User size={15} /> {t('portal.citizen.title')}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={portalType === 'government'}
                className={portalType === 'government' ? 'active' : ''}
                onClick={() => { setPortalType('government'); setError(''); }}
              >
                <Landmark size={15} /> {t('portal.gov.title')}
              </button>
            </div>

            {portalType === 'government' && (
              <div className="login-field login-field-role">
                <label htmlFor="login-role">{t('portal.gov.roleLabel')}</label>
                <div className="login-select-wrap">
                  <select
                    id="login-role"
                    value={govRole}
                    onChange={(e) => { setGovRole(e.target.value); setError(''); }}
                  >
                    <option value="" disabled>{t('portal.gov.rolePlaceholder')}</option>
                    {GOV_ROLES.map((r) => (
                      <option key={r.value} value={r.value}>{t(r.labelKey)}</option>
                    ))}
                  </select>
                  <ChevronDown size={15} className="login-select-chevron" />
                </div>
              </div>
            )}

            <form onSubmit={handleSubmit} noValidate>
              <div className="login-field">
                <label htmlFor="login-email">{t('login.emailLabel')}</label>
                <div className="login-input-wrap">
                  <Mail size={15} className="login-input-icon" />
                  <input
                    id="login-email"
                    type="email"
                    autoComplete="email"
                    placeholder={t('login.emailPlaceholder')}
                    value={email}
                    onChange={(e) => { setEmail(e.target.value); setError(''); }}
                  />
                </div>
              </div>

              <div className="login-field">
                <label htmlFor="login-password">{t('login.passwordLabel')}</label>
                <div className="login-input-wrap">
                  <Lock size={15} className="login-input-icon" />
                  <input
                    id="login-password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    placeholder={t('login.passwordPlaceholder')}
                    value={password}
                    onChange={(e) => { setPassword(e.target.value); setError(''); }}
                  />
                  <button
                    type="button"
                    className="login-input-toggle"
                    onClick={() => setShowPassword((s) => !s)}
                    aria-label={showPassword ? t('login.hidePassword') : t('login.showPassword')}
                  >
                    {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>

              {error && <div className="login-error" role="alert">{error}</div>}

              <button type="submit" className="btn btn-primary login-submit">
                {t('login.submit')} <ArrowRight size={16} />
              </button>
            </form>

            <div className="login-demo-box">
              <div className="login-demo-label"><KeyRound size={13} /> {t('login.demoLabel')}</div>
              <div className="login-demo-creds">
                <span><strong>{t('login.emailLabel')}:</strong> {DEMO_EMAIL}</span>
                <span><strong>{t('login.passwordLabel')}:</strong> {DEMO_PASSWORD}</span>
              </div>
              <button type="button" className="login-demo-fill" onClick={fillDemoCredentials}>
                {t('login.demoFill')}
              </button>
            </div>

            <div className="login-footnote">
              <ShieldCheck size={13} /> {t('login.footnote')}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
