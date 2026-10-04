import React, { createContext, useContext, useState, useEffect, useMemo, useCallback } from 'react';

import en from './locales/en';
import hi from './locales/hi';
import bn from './locales/bn';
import mr from './locales/mr';
import te from './locales/te';
import ta from './locales/ta';
import gu from './locales/gu';
import kn from './locales/kn';
import ml from './locales/ml';
import pa from './locales/pa';
import or from './locales/or';
import as from './locales/as';

const STORAGE_KEY = 'mplads.lang';

// `native` is deliberately the endonym (the language's own name in its own
// script) so a speaker can find their language without reading English.
export const LANGUAGES = [
  { code: 'en', native: 'English',   english: 'English' },
  { code: 'hi', native: 'हिन्दी',      english: 'Hindi' },
  { code: 'bn', native: 'বাংলা',       english: 'Bengali' },
  { code: 'mr', native: 'मराठी',       english: 'Marathi' },
  { code: 'te', native: 'తెలుగు',      english: 'Telugu' },
  { code: 'ta', native: 'தமிழ்',       english: 'Tamil' },
  { code: 'gu', native: 'ગુજરાતી',     english: 'Gujarati' },
  { code: 'kn', native: 'ಕನ್ನಡ',       english: 'Kannada' },
  { code: 'ml', native: 'മലയാളം',     english: 'Malayalam' },
  { code: 'pa', native: 'ਪੰਜਾਬੀ',      english: 'Punjabi' },
  { code: 'or', native: 'ଓଡ଼ିଆ',       english: 'Odia' },
  { code: 'as', native: 'অসমীয়া',     english: 'Assamese' },
];

const BUNDLES = { en, hi, bn, mr, te, ta, gu, kn, ml, pa, or, as };

const I18nContext = createContext(null);

function readStoredLang() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && BUNDLES[stored]) return stored;
  } catch {
    // localStorage can throw in private-browsing / blocked-cookie modes --
    // language selection just won't persist, which is not fatal.
  }
  return 'en';
}

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(readStoredLang);

  const setLang = useCallback((code) => {
    if (!BUNDLES[code]) return;
    setLangState(code);
    try {
      localStorage.setItem(STORAGE_KEY, code);
    } catch {
      // Non-persistent selection is an acceptable degradation.
    }
  }, []);

  // Keep the document language attribute in sync for screen readers and
  // for correct font/line-breaking behaviour on Indic scripts.
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  // t(key, vars?) -- returns the string for the active language, falling
  // back to English, then to the key itself so a missing key is visible in
  // development rather than rendering as blank.
  const t = useCallback((key, vars) => {
    const bundle = BUNDLES[lang] || en;
    let str = bundle[key];
    if (str === undefined) str = en[key];
    if (str === undefined) return key;
    if (vars) {
      Object.keys(vars).forEach((k) => {
        str = str.replace(new RegExp(`\\{${k}\\}`, 'g'), String(vars[k]));
      });
    }
    return str;
  }, [lang]);

  const value = useMemo(() => ({ lang, setLang, t, languages: LANGUAGES }), [lang, setLang, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useTranslation() {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    // Defensive: if a component somehow renders outside the provider, fall
    // back to plain English rather than crashing the page.
    return {
      lang: 'en',
      setLang: () => {},
      languages: LANGUAGES,
      t: (key, vars) => {
        let str = en[key];
        if (str === undefined) return key;
        if (vars) Object.keys(vars).forEach((k) => {
          str = str.replace(new RegExp(`\\{${k}\\}`, 'g'), String(vars[k]));
        });
        return str;
      },
    };
  }
  return ctx;
}
