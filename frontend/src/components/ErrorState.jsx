import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { useTranslation } from '../i18n';

// Shown when an API call FAILED (server error, network down) -- deliberately
// distinct from EmptyState, which means the call succeeded and there is
// genuinely nothing to show. A failure must never read as "no data".
//
// `what` names the thing that failed ("dashboard summary"); `error` is the
// caught Error, whose message is shown small for whoever is diagnosing it;
// `onRetry` adds a retry button. `compact` renders an inline one-line
// notice for secondary data (filter lists, side panels) inside a page that
// otherwise loaded.
export default function ErrorState({ what, error, onRetry, compact = false }) {
  const { t } = useTranslation();
  const title = what ? t('common.loadFailedWhat', { what }) : t('common.loadFailed');
  const detail = error && error.message ? error.message : '';

  if (compact) {
    return (
      <div className="error-inline" role="alert" data-testid="load-failed">
        <AlertTriangle size={13} />
        <span>{title}</span>
        {onRetry && (
          <button type="button" className="error-inline-retry" onClick={onRetry}>{t('common.retry')}</button>
        )}
      </div>
    );
  }

  return (
    <div className="error-state" role="alert" data-testid="load-failed">
      <AlertTriangle size={44} strokeWidth={1.5} />
      <div className="error-title">{title}</div>
      <div className="error-hint">{t('common.loadFailedHint')}</div>
      {detail && <div className="error-detail">{detail}</div>}
      {onRetry && (
        <button type="button" className="btn btn-outline error-retry" onClick={onRetry}>
          <RefreshCw size={14} /> {t('common.retry')}
        </button>
      )}
    </div>
  );
}
