import React, { useMemo, useState, useEffect } from 'react';
import useCountUp from '../../hooks/useCountUp';
import { shade, withAlpha } from './chartTheme';
import { useTranslation } from '../../i18n';

function polarToCartesian(cx, cy, r, angleDeg) {
  const a = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
}

function donutSlicePath(cx, cy, rOuter, rInner, startAngle, endAngle) {
  const span = Math.min(endAngle - startAngle, 359.99);
  const end = startAngle + span;
  const largeArc = span > 180 ? 1 : 0;
  const p1 = polarToCartesian(cx, cy, rOuter, startAngle);
  const p2 = polarToCartesian(cx, cy, rOuter, end);
  const p3 = polarToCartesian(cx, cy, rInner, end);
  const p4 = polarToCartesian(cx, cy, rInner, startAngle);
  return [
    `M ${p1.x} ${p1.y}`,
    `A ${rOuter} ${rOuter} 0 ${largeArc} 1 ${p2.x} ${p2.y}`,
    `L ${p3.x} ${p3.y}`,
    `A ${rInner} ${rInner} 0 ${largeArc} 0 ${p4.x} ${p4.y}`,
    'Z',
  ].join(' ');
}

/**
 * A dimensional, interactive donut chart: a tilted (rotateX) SVG disc with
 * a darkened extrusion ring beneath it to read as a solid 3D object, each
 * segment animating in on mount and lifting slightly on hover. Built from
 * plain SVG + CSS — no charting/3D library required.
 *
 * data: [{ key, name, value, color }]
 */
export default function Donut3D({ data, size = 232, thickness, centerLabel, onSegmentClick, formatValue }) {
  const { t: translate } = useTranslation();
  const centerText = centerLabel ?? translate('common.total');
  const [hoverKey, setHoverKey] = useState(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const t = requestAnimationFrame(() => setTimeout(() => setMounted(true), 30));
    return () => cancelAnimationFrame(t);
  }, []);

  const total = data.reduce((s, d) => s + d.value, 0);
  const cx = size / 2;
  const cy = size / 2;
  const rOuter = size / 2 - 10;
  const th = thickness || size * 0.16;
  const rInner = rOuter - th;
  const depth = Math.max(8, th * 0.4);

  const segments = useMemo(() => {
    let cursor = 0;
    return data.map((d) => {
      const angle = total > 0 ? (d.value / total) * 360 : 0;
      const seg = { ...d, startAngle: cursor, endAngle: cursor + angle, pct: total > 0 ? (d.value / total) * 100 : 0 };
      cursor += angle;
      return seg;
    });
  }, [data, total]);

  const animatedTotal = useCountUp(total, { duration: 900 });
  const hovered = segments.find((s) => s.key === hoverKey);

  return (
    <div className="donut3d">
      <div className="donut3d-scene">
        <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} className="donut3d-svg">
          <defs>
            {segments.map((s) => (
              <linearGradient id={`donut3d-grad-${s.key}`} key={s.key} x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor={shade(s.color, 28)} />
                <stop offset="100%" stopColor={s.color} />
              </linearGradient>
            ))}
          </defs>

          {/* extrusion / shadow ring — gives the disc physical thickness */}
          <g transform={`translate(0, ${depth})`} opacity="0.9">
            {segments.map((s) => (
              <path key={`shadow-${s.key}`} d={donutSlicePath(cx, cy, rOuter, rInner, s.startAngle, s.endAngle)} fill={shade(s.color, -60)} />
            ))}
          </g>

          {/* top face — the interactive, coloured ring */}
          <g>
            {segments.map((s, i) => {
              const isHover = hoverKey === s.key;
              const midAngle = (s.startAngle + s.endAngle) / 2;
              const pop = isHover ? 5 : 0;
              const rad = ((midAngle - 90) * Math.PI) / 180;
              const dx = Math.cos(rad) * pop;
              const dy = Math.sin(rad) * pop;
              return (
                <path
                  key={s.key}
                  d={donutSlicePath(cx, cy, rOuter, rInner, s.startAngle, s.endAngle)}
                  fill={`url(#donut3d-grad-${s.key})`}
                  stroke="rgba(255,255,255,0.9)"
                  strokeWidth={1.5}
                  className="donut3d-segment"
                  style={{
                    transform: mounted ? `translate(${dx}px, ${dy}px) scale(1)` : 'scale(0.4)',
                    opacity: mounted ? 1 : 0,
                    transitionDelay: `${i * 90}ms`,
                    filter: isHover ? `drop-shadow(0 4px 10px ${withAlpha(s.color, 0.55)}) brightness(1.08)` : 'none',
                  }}
                  onMouseEnter={() => setHoverKey(s.key)}
                  onMouseLeave={() => setHoverKey(null)}
                  onClick={() => onSegmentClick && onSegmentClick(s.key)}
                />
              );
            })}
          </g>
        </svg>

        <div className="donut3d-center">
          {hovered ? (
            <>
              <div className="donut3d-center-value" style={{ color: hovered.color }}>{hovered.pct.toFixed(1)}%</div>
              <div className="donut3d-center-label">{hovered.name}</div>
            </>
          ) : (
            <>
              <div className="donut3d-center-value">{Math.round(animatedTotal).toLocaleString('en-IN')}</div>
              <div className="donut3d-center-label">{centerText}</div>
            </>
          )}
        </div>
      </div>

      <div className="donut3d-legend">
        {segments.map((s) => (
          <button
            key={s.key}
            className={`donut3d-legend-item ${hoverKey === s.key ? 'active' : ''}`}
            onMouseEnter={() => setHoverKey(s.key)}
            onMouseLeave={() => setHoverKey(null)}
            onClick={() => onSegmentClick && onSegmentClick(s.key)}
          >
            <span className="swatch" style={{ background: s.color }} />
            <span className="name">{s.name}</span>
            <span className="value">{formatValue ? formatValue(s.value) : s.value.toLocaleString('en-IN')}</span>
            <span className="pct">{s.pct.toFixed(1)}%</span>
          </button>
        ))}
      </div>
    </div>
  );
}
