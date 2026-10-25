import {createContext, useContext, useEffect, useState, type ReactNode} from 'react';

type Preferences = {ppe: string; jurisdiction: 'unknown' | 'US-federal-OSHA'; personConfidence: number};
const storageKey = 'industrial-safety.review-preferences.v1';
const defaults: Preferences = {ppe: '', jurisdiction: 'unknown', personConfidence: .5};

export function validPersonConfidence(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= .05 && value <= .95 ? value : .5;
}

function readPreferences(): Preferences {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
    return {
      ppe: typeof saved?.ppe === 'string' ? saved.ppe : '',
      jurisdiction: saved?.jurisdiction === 'US-federal-OSHA' ? 'US-federal-OSHA' : 'unknown',
      personConfidence: validPersonConfidence(saved?.personConfidence),
    };
  } catch { return defaults; }
}

const Context = createContext<{
  ppe: string; jurisdiction: Preferences['jurisdiction']; personConfidence: number;
  setPersonConfidence: (value: number) => void;
  setPpe: (value: string) => void; setJurisdiction: (value: Preferences['jurisdiction']) => void;
} | null>(null);

export function ReviewPreferencesProvider({children}: {children: ReactNode}) {
  const [preferences, setPreferences] = useState(readPreferences);
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(preferences)); }
    catch { /* Preferences still work for this visit when storage is unavailable. */ }
  }, [preferences]);
  return <Context.Provider value={{...preferences,
    setPpe: ppe => setPreferences(value => ({...value, ppe})),
    setJurisdiction: jurisdiction => setPreferences(value => ({...value, jurisdiction})),
    setPersonConfidence: personConfidence => setPreferences(value => ({...value, personConfidence: validPersonConfidence(personConfidence)})),
  }}>{children}</Context.Provider>;
}

export function useReviewPreferences() {
  const value = useContext(Context);
  if (!value) throw new Error('Review preferences require their provider.');
  return value;
}
