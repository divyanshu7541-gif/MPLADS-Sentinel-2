import React from 'react';
import { useTranslation } from '../i18n';

// 24-spoke Ashoka Chakra geometry (15deg increments) -- same precise
// approach as ChakraWatermark, scaled to this spinner's 0-100 viewBox.
const SPINNER_SPOKES = Array.from({ length: 24 }, (_, i) => {
  const rad = (i * 15) * (Math.PI / 180);
  return {
    x1: 50 + 14 * Math.sin(rad),
    y1: 50 - 14 * Math.cos(rad),
    x2: 50 + 34 * Math.sin(rad),
    y2: 50 - 34 * Math.cos(rad),
  };
});

function ChakraSpinner({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" className="chakra-spinner" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeLinecap="round">
        <circle cx="50" cy="50" r="44" strokeWidth="4" opacity="0.18" />
        <circle cx="50" cy="50" r="44" strokeWidth="4" strokeDasharray="60 216" />
        {SPINNER_SPOKES.map((s, i) => (
          <line key={i} x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} strokeWidth="1.5" opacity="0.55" />
        ))}
        <circle cx="50" cy="50" r="6" fill="currentColor" stroke="none" opacity="0.7" />
      </g>
    </svg>
  );
}

function TableSkeleton({ rows = 8 }) {
  return (
    <div className="skeleton-table" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="skeleton-row" key={i}>
          <div className="skeleton-block skeleton-cell narrow" />
          <div className="skeleton-block skeleton-cell narrow" />
          <div className="skeleton-block skeleton-cell wide" />
          <div className="skeleton-block skeleton-cell" />
          <div className="skeleton-block skeleton-cell" />
          <div className="skeleton-block skeleton-cell narrow" />
        </div>
      ))}
    </div>
  );
}

function MetricsSkeleton({ count = 5 }) {
  return (
    <div className="skeleton-metrics" aria-hidden="true">
      {Array.from({ length: count }).map((_, i) => (
        <div className="skeleton-metric-card" key={i}>
          <div className="skeleton-block" />
          <div className="skeleton-block-col">
            <div className="skeleton-block" />
            <div className="skeleton-block" />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function LoadingState({ message, variant = 'spinner' }) {
  const { t } = useTranslation();
  if (variant === 'table') return <TableSkeleton />;
  if (variant === 'metrics') return <MetricsSkeleton />;

  return (
    <div className="loading" role="status" aria-live="polite">
      <ChakraSpinner />
      {message ?? t('common.loading')}
    </div>
  );
}
