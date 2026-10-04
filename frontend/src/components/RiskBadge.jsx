import React from 'react';
import { useLabels } from '../i18n/labels';

export default function RiskBadge({ priority, size = 'sm' }) {
  const { riskBadge } = useLabels();

  // Map legacy values to new ones. A missing level means the work has no
  // risk assessment: show "Not evaluated", never a default tier.
  const mapped = priority === 'HIGH_PRIORITY_REVIEW' ? 'CRITICAL'
    : priority === 'REVIEW_RECOMMENDED' ? 'MODERATE'
    : (priority ?? 'NOT_EVALUATED');

  // Known levels are translated; anything unexpected still renders as before
  // (raw value with underscores turned into spaces).
  const translated = riskBadge(mapped);
  const text = translated === mapped ? mapped?.replace(/_/g, ' ') : translated;

  return (
    <span className={`priority-badge ${mapped}`} style={size === 'lg' ? { fontSize: 12, padding: '5px 12px' } : {}}>
      {text}
    </span>
  );
}
