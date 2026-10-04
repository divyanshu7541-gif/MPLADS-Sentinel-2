import React from 'react';
import { FileSearch } from 'lucide-react';
import { useTranslation } from '../i18n';

export default function EmptyState({ message, hint = '' }) {
  const { t } = useTranslation();
  return (
    <div className="empty-state">
      <FileSearch size={44} strokeWidth={1.5} />
      <div className="empty-title">{message ?? t('common.noData')}</div>
      {hint && <div className="empty-hint">{hint}</div>}
    </div>
  );
}
