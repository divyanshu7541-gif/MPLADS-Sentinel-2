/** Lighten/darken a #rrggbb hex colour by `amt` (-255..255). Used to derive
 * bevel/highlight/shadow shades for the 3D chart components from a single
 * base colour, so every chart stays on the platform's existing palette. */
export function shade(hex, amt) {
  const clean = hex.replace('#', '');
  const num = parseInt(clean.length === 3
    ? clean.split('').map((c) => c + c).join('')
    : clean, 16);
  let r = (num >> 16) + amt;
  let g = ((num >> 8) & 0x00ff) + amt;
  let b = (num & 0x0000ff) + amt;
  r = Math.max(0, Math.min(255, r));
  g = Math.max(0, Math.min(255, g));
  b = Math.max(0, Math.min(255, b));
  return `#${(1 << 24 | r << 16 | g << 8 | b).toString(16).slice(1)}`;
}

/** Shared categorical palette for multi-bar charts — drawn from the
 * platform's existing chart tokens (navy, indigo, teal, burgundy, gold)
 * plus a couple of neighbouring tones, so new charts stay on-brand. */
export const CATEGORY_PALETTE = [
  '#1c2745', '#3a4c8c', '#0d766c', '#7f2c40', '#93630c',
  '#5468ac', '#0b5f57', '#9c7a2c', '#454e64', '#2e3d72',
];

export function paletteColor(i) {
  return CATEGORY_PALETTE[i % CATEGORY_PALETTE.length];
}

export function withAlpha(hex, alpha) {
  const clean = hex.replace('#', '');
  const num = parseInt(clean, 16);
  const r = (num >> 16) & 0xff;
  const g = (num >> 8) & 0xff;
  const b = num & 0xff;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
