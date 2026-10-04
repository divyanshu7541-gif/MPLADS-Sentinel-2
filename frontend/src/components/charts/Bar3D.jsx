import React, { useState, useEffect } from 'react';
import { shade } from './chartTheme';

/**
 * A horizontal bar list with real dimensional styling: a lit top bevel, a
 * shaded right edge, a drop shadow, and a hover tilt (rotateX) — animated
 * grow-in on mount, staggered per row. Pure CSS, no charting library.
 *
 * data: [{ key, name, value, color }]
 */
export default function Bar3D({ data, max, labelWidth, valueFormatter, onBarClick }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const t = requestAnimationFrame(() => setTimeout(() => setMounted(true), 30));
    return () => cancelAnimationFrame(t);
  }, []);

  const maxVal = max || Math.max(1, ...data.map((d) => d.value));

  return (
    <div className="bar3d-list" style={labelWidth ? { '--bar3d-label-w': `${labelWidth}px` } : undefined}>
      {data.map((d, i) => {
        const pct = Math.max(1.5, (d.value / maxVal) * 100);
        return (
          <div
            key={d.key || d.name}
            className="bar3d-row"
            onClick={() => onBarClick && onBarClick(d)}
            style={{ cursor: onBarClick ? 'pointer' : 'default' }}
          >
            <div className="bar3d-label" title={d.name}>{d.name}</div>
            <div className="bar3d-track">
              <div
                className="bar3d-fill"
                style={{
                  width: mounted ? `${pct}%` : '0%',
                  transitionDelay: `${i * 55}ms`,
                  background: `linear-gradient(135deg, ${shade(d.color, 22)}, ${d.color} 65%, ${shade(d.color, -18)})`,
                }}
              />
            </div>
            <div className="bar3d-value">{valueFormatter ? valueFormatter(d.value) : d.value.toLocaleString('en-IN')}</div>
          </div>
        );
      })}
    </div>
  );
}
