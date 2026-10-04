import { useEffect, useRef, useState } from 'react';

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Animates a numeric value from 0 up to `target` with an ease-out curve.
 * Non-numeric targets (pre-formatted strings like "Rs. 45 Cr") pass through untouched.
 */
export default function useCountUp(target, { duration = 900, decimals = 0 } = {}) {
  const isNumeric = typeof target === 'number' && !Number.isNaN(target);
  const [value, setValue] = useState(isNumeric ? 0 : target);
  const rafRef = useRef(null);

  useEffect(() => {
    if (!isNumeric) {
      setValue(target);
      return;
    }
    if (prefersReducedMotion()) {
      setValue(target);
      return;
    }
    const start = performance.now();
    const from = 0;
    const animate = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(from + (target - from) * eased);
      if (t < 1) rafRef.current = requestAnimationFrame(animate);
    };
    rafRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration, isNumeric]);

  if (!isNumeric) return value;
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}
