import React, { useState, useRef, useEffect } from 'react';
import { Globe, Check } from 'lucide-react';
import { useTranslation } from '../i18n';

/**
 * 🌐 language selector. `variant="dark"` is for placement on the dark
 * landing-page header; the default suits the light dashboard topbar.
 * Switching is instant (React state) and persisted to localStorage by the
 * provider.
 */
export default function LanguageSelector({ variant = 'light' }) {
  const { lang, setLang, languages, t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, []);

  const current = languages.find((l) => l.code === lang) || languages[0];

  return (
    <div className={`lang-select ${variant}`} ref={ref}>
      <button
        type="button"
        className="lang-select-btn"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={t('lang.label')}
        title={t('lang.label')}
      >
        <Globe size={15} />
        <span className="lang-select-current">{current.native}</span>
      </button>

      {open && (
        <div className="lang-select-menu" role="listbox" aria-label={t('lang.label')}>
          {languages.map((l) => (
            <button
              key={l.code}
              type="button"
              role="option"
              aria-selected={l.code === lang}
              className={`lang-select-option ${l.code === lang ? 'active' : ''}`}
              onClick={() => { setLang(l.code); setOpen(false); }}
            >
              <span className="native">{l.native}</span>
              <span className="english">{l.english}</span>
              {l.code === lang && <Check size={13} className="tick" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
