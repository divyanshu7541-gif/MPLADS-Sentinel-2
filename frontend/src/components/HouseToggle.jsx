import React, { createContext, useContext, useState, useCallback, useMemo } from 'react';
import { Info } from 'lucide-react';
import { useTranslation } from '../i18n';
import { setApiHouse } from '../services/api';

/**
 * House filter: [ LOK SABHA ] [ RAJYA SABHA ]. Mounted once in the shared
 * topbar. Neither selected (the default) means both houses, which is the
 * unfiltered behaviour. Clicking the selected house again clears it.
 *
 * The choice goes to services/api.js, which appends `house=LS|RS` to the
 * dataset-wide calls only. It is a display filter; peer baselines are
 * always built over both houses.
 */
const STORAGE_KEY = 'mplads.house';
const HOUSES = ['LS', 'RS'];

const HouseContext = createContext({ house: null, setHouse: () => {} });

function readStoredHouse() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (HOUSES.includes(stored)) return stored;
  } catch {
    // Blocked storage just means the selection doesn't persist.
  }
  return null;
}

export function HouseProvider({ children }) {
  // The API module is updated before state changes, so the fetches of the
  // pages that re-mount on the change already carry the new value.
  const [house, setHouseState] = useState(() => {
    const initial = readStoredHouse();
    setApiHouse(initial);
    return initial;
  });

  const setHouse = useCallback((next) => {
    const value = HOUSES.includes(next) ? next : null;
    setApiHouse(value);
    setHouseState(value);
    try {
      if (value) localStorage.setItem(STORAGE_KEY, value);
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Non-persistent selection is an acceptable degradation.
    }
  }, []);

  const value = useMemo(() => ({ house, setHouse }), [house, setHouse]);
  return <HouseContext.Provider value={value}>{children}</HouseContext.Provider>;
}

export function useHouse() {
  return useContext(HouseContext);
}

export default function HouseToggle() {
  const { house, setHouse } = useHouse();
  const { t } = useTranslation();
  const options = [
    { code: 'LS', label: t('house.ls') },
    { code: 'RS', label: t('house.rs') },
  ];
  return (
    <div className="house-toggle" role="group" aria-label={t('house.label')} title={t('house.hint')}>
      {options.map(({ code, label }) => (
        <button
          key={code}
          type="button"
          className={`house-toggle-btn ${house === code ? 'active' : ''}`}
          aria-pressed={house === code}
          data-house={code}
          onClick={() => setHouse(house === code ? null : code)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/** Explicit "not applicable" state for constituency-scoped features while
 *  Rajya Sabha is selected: RS members represent a state, not a seat. */
export function HouseNotApplicable({ feature }) {
  const { t } = useTranslation();
  return (
    <div className="house-na" role="status" data-testid="house-not-applicable">
      <Info size={14} />
      <div>
        <strong>{t('house.na.title', { feature })}</strong>
        <div>{t('house.na.body')}</div>
      </div>
    </div>
  );
}
