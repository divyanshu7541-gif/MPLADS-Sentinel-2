import React, { useRef, useCallback } from 'react';
import { FileText, IndianRupee, AlertTriangle, Clock3, ShieldCheck } from 'lucide-react';
import useCountUp from '../hooks/useCountUp';

const ICONS = {
  danger: AlertTriangle,
  warning: Clock3,
  success: ShieldCheck,
  info: FileText,
  amount: IndianRupee,
  '': FileText,
};

export default function MetricCard({ label, value, variant = '', subtitle = '', icon, index = 0 }) {
  const animated = useCountUp(value, { duration: 850 });
  const formatted = typeof animated === 'number' ? Math.round(animated).toLocaleString('en-IN') : animated;
  const Icon = icon || ICONS[variant] || FileText;
  const cardRef = useRef(null);

  const handleMouseMove = useCallback((e) => {
    const el = cardRef.current;
    if (!el || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const rect = el.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width - 0.5;
    const py = (e.clientY - rect.top) / rect.height - 0.5;
    el.style.setProperty('--tilt-x', `${(-py * 7).toFixed(2)}deg`);
    el.style.setProperty('--tilt-y', `${(px * 7).toFixed(2)}deg`);
    el.style.setProperty('--glow-x', `${(px * 0.5 + 0.5) * 100}%`);
    el.style.setProperty('--glow-y', `${(py * 0.5 + 0.5) * 100}%`);
  }, []);

  const handleMouseLeave = useCallback(() => {
    const el = cardRef.current;
    if (!el) return;
    el.style.setProperty('--tilt-x', '0deg');
    el.style.setProperty('--tilt-y', '0deg');
  }, []);

  return (
    <div
      ref={cardRef}
      className={`metric-card metric-card-3d ${variant}`}
      style={{ animationDelay: `${index * 55}ms` }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
    >
      <div className="metric-icon"><Icon size={16} /></div>
      <div className="metric-body">
        <div className="metric-label">{label}</div>
        <div className="metric-value">{formatted}</div>
        {subtitle && <div className="metric-change">{subtitle}</div>}
      </div>
    </div>
  );
}
