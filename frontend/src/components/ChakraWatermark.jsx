import React from 'react';

// Authentic 24-spoke Ashoka Chakra (Flag of India), drawn in a 400x400 box.
// Proportions: thick outer rim, small hub, 24 tapered spokes at exact 15deg
// steps, and 24 small dots on the rim's inner edge centred between spokes.
const CX = 200;
const CY = 200;
const R_RIM_OUT = 190;   // outer edge of the rim
const R_RIM_IN = 178;    // inner edge of the rim
const R_HUB = 16;        // hub radius
const R_SPOKE_IN = 16;   // spoke starts at the hub
const R_SPOKE_OUT = 180; // spoke tip tucks just into the rim
const W_IN = 2.2;        // spoke width at the hub
const W_OUT = 6.4;       // spoke width at the rim (rounded tip)
const R_DOT = 168;       // dot orbit radius
const DOT_SIZE = 4.2;    // dot radius

// One spoke, pointing straight up; tapered, with a rounded outer tip.
const SPOKE_PATH = [
  `M ${CX - W_IN / 2} ${CY - R_SPOKE_IN}`,
  `L ${CX - W_OUT / 2} ${CY - R_SPOKE_OUT}`,
  `A ${W_OUT / 2} ${W_OUT / 2} 0 0 1 ${CX + W_OUT / 2} ${CY - R_SPOKE_OUT}`,
  `L ${CX + W_IN / 2} ${CY - R_SPOKE_IN}`,
  'Z',
].join(' ');

const INDEXES = Array.from({ length: 24 }, (_, i) => i);

const DOTS = INDEXES.map((i) => {
  const rad = (i * 15 + 7.5) * (Math.PI / 180);
  return { cx: CX + R_DOT * Math.sin(rad), cy: CY - R_DOT * Math.cos(rad) };
});

/**
 * A dignified, extremely slow-rotating chakra watermark fixed behind all
 * page content. Purely decorative (aria-hidden), never intercepts clicks,
 * and freezes for prefers-reduced-motion.
 *
 * Solid Ashoka Chakra navy (#000080) with a single group opacity so
 * overlapping shapes never darken. The rotation itself (chakra-spin, 220s
 * linear) is unchanged and lives in CSS.
 */
export default function ChakraWatermark() {
  return (
    <div className="chakra-watermark" aria-hidden="true">
      <div className="chakra-watermark-inner">
        <svg className="chakra-watermark-svg" viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg">
          <g fill="#000080" opacity="0.14">
            {/* Rim */}
            <path
              fillRule="evenodd"
              d={`M ${CX - R_RIM_OUT} ${CY} a ${R_RIM_OUT} ${R_RIM_OUT} 0 1 0 ${R_RIM_OUT * 2} 0 a ${R_RIM_OUT} ${R_RIM_OUT} 0 1 0 ${-R_RIM_OUT * 2} 0 Z M ${CX - R_RIM_IN} ${CY} a ${R_RIM_IN} ${R_RIM_IN} 0 1 0 ${R_RIM_IN * 2} 0 a ${R_RIM_IN} ${R_RIM_IN} 0 1 0 ${-R_RIM_IN * 2} 0 Z`}
            />
            {/* Hub */}
            <circle cx={CX} cy={CY} r={R_HUB} />
            {/* 24 spokes */}
            {INDEXES.map((i) => (
              <path key={i} d={SPOKE_PATH} transform={`rotate(${i * 15} ${CX} ${CY})`} />
            ))}
            {/* 24 dots between spokes */}
            {DOTS.map((d, i) => (
              <circle key={i} cx={d.cx} cy={d.cy} r={DOT_SIZE} />
            ))}
          </g>
        </svg>
      </div>
    </div>
  );
}
