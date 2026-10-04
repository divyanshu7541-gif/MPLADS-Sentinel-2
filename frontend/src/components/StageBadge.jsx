import React from 'react';
import { useLabels } from '../i18n/labels';

export default function StageBadge({ stage }) {
  const labels = useLabels();
  // Known stages are translated (upper-cased to match the previous English
  // rendering; a no-op for scripts without letter case). Unknown values pass
  // through untouched.
  const translated = labels.stage(stage);
  const text = translated === stage ? stage : translated.toUpperCase();
  return (
    <span className={`stage-badge ${stage}`}>
      {text}
    </span>
  );
}
