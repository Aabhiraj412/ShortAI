// CrazzyDocs Pop Info engine (pop_info_v1, revision 3.28: illustrated environments, kinetic type).
// Flat-vector motion graphics drawn entirely in canvas, after docs/reference_styles/pop_info.html:
// springy pop-ins, decaying wobble, iris wipe with a yellow ring, a teal idea bubble with a counting
// number and a pill tag, a self-drawing dotted path with pins, slow camera push and drifting dust.
// Every frame is a pure function of t. All randomness is seeded. Design space 540 x 960, drawn at 2x.
// Contract with Lemo-Opuscar: window.DUR, window.render(t), window.READY. QC: window.layoutAudit(t).
(() => {
'use strict';
const PLAN = window.PLAN;
const DW = 540, DH = 960;
const SAFE = PLAN.design.safe;
const cvs = document.getElementById('stage'), ctx = cvs.getContext('2d');

// ---------------------------------------------------------------- helpers (reference CM)
const clamp = (v, a = 0, b = 1) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, u) => a + (b - a) * u;
const prog = (t, a, d) => clamp((t - a) / d);
const eOutCubic = x => 1 - Math.pow(1 - x, 3);
const eInOut = x => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const eOutBack = (x, s = 2.0) => 1 + (s + 1) * Math.pow(x - 1, 3) + s * Math.pow(x - 1, 2);
const popS = (t, a, d = 0.45, s = 2.2) => (t < a ? 0 : eOutBack(clamp((t - a) / d), s));
const wobble = (t, a) => { if (t < a) return 0; const x = t - a; return Math.exp(-x * 6) * Math.sin(x * 20) * 0.13; };
const hash = s => { s = String(s); let h = 0x9e3779b1; for (let i = 0; i < s.length; i++) { h = Math.imul(h ^ s.charCodeAt(i), 0x85ebca6b); h ^= h >>> 13; } return h >>> 0; };
const rng = seed => { let a = hash(seed) || 1; return () => { a ^= a << 13; a ^= a >>> 17; a ^= a << 5; return ((a >>> 0) % 100000) / 100000; }; };
const rr = (c, x, y, w, h, r) => { c.beginPath(); c.roundRect(x, y, w, h, r); };

const C = {
  ink: '#3B2A26', teal: '#2F6F73', tealD: '#24585B', tealL: '#3E8A8E', coral: '#E85D4A', coralD: '#C24B39',
  yellow: '#FFD86B', cream: '#FBF8EE', paper: '#FFF6EC', sage: '#D7DCCB', sand: '#E9DFC9', sky: '#D5E3E6',
  blush: '#F3DCCF', orange: '#E07A5F', gold: '#F2CC8F', green: '#81B29A', blue: '#6C9BB8', navy: '#3D405B',
};
const BG = {
  cream: ['#F4EEDF', '#E9DFC9', '#F2CC8F'], sage: ['#D7DCCB', '#C7CEB8', '#81B29A'],
  sand: ['#EADFC8', '#DCCDAE', '#E07A5F'], sky: ['#D5E3E6', '#C1D6DB', '#6C9BB8'], blush: ['#F3DCCF', '#EBC9B8', '#E85D4A'],
};
const FONT = { head: '"Fredoka", "Segoe UI", sans-serif', num: '"Lilita One", "Fredoka", Impact, sans-serif' };

// ---------------------------------------------------------------- text with layout audit
let AUDIT = null;     // when an array, every text box drawn is recorded (design coordinates)
function fitFont(c, text, maxW, size, weight, fam, min = 16) {
  let s = size;
  while (s > min) { c.font = `${weight} ${s}px ${fam}`; if (c.measureText(text).width <= maxW) break; s -= 1; }
  c.font = `${weight} ${s}px ${fam}`; return s;
}
function wrap(c, text, maxW, size, weight, fam, maxLines = 3) {
  c.font = `${weight} ${size}px ${fam}`;
  const words = String(text).split(/\s+/).filter(Boolean), lines = [];
  let cur = '';
  for (const w of words) { const nx = cur ? cur + ' ' + w : w; if (c.measureText(nx).width > maxW && cur) { lines.push(cur); cur = w; } else cur = nx; }
  if (cur) lines.push(cur);
  if (lines.length > maxLines) return null;
  return lines;
}
function layoutLines(c, text, maxW, size, weight, fam, maxLines = 3, min = 22) {
  for (let s = size; s >= min; s -= 2) { const l = wrap(c, text, maxW, s, weight, fam, maxLines); if (l) return { lines: l, size: s }; }
  return { lines: wrap(c, text, maxW, min, weight, fam, 99) || [text], size: min };
}
function txt(c, s, x, y, opts = {}) {
  const { size = 24, weight = 600, fam = FONT.head, color = C.ink, align = 'center', base = 'middle', alpha = 1, tag = 'text' } = opts;
  c.save(); c.globalAlpha *= alpha; c.font = `${weight} ${size}px ${fam}`; c.fillStyle = color; c.textAlign = align; c.textBaseline = base;
  c.fillText(s, x, y);
  if (AUDIT && alpha > 0.05) {
    const w = c.measureText(s).width, m = c.getTransform(), S = cvs.width / DW;
    const x0 = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x, y0 = y - size * 0.62, y1 = y + size * 0.42;
    const p = (px, py) => ({ x: (m.a * px + m.c * py + m.e) / S, y: (m.b * px + m.d * py + m.f) / S });
    const a = p(x0, y0), b = p(x0 + w, y1);
    AUDIT.push({ tag, text: s, x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y), size: size * Math.abs(m.a) / S });
  }
  c.restore();
}

// ---------------------------------------------------------------- icon library (flat vector, one shadow tone)
function shadowBlob(c, x, y, w) { c.fillStyle = 'rgba(59,42,38,.13)'; c.beginPath(); c.ellipse(x, y, w, w * 0.22, 0, 0, 6.29); c.fill(); }
const ICONS = {
  atom(c, t) {
    c.fillStyle = C.coral; c.beginPath(); c.arc(0, 0, 12, 0, 6.29); c.fill();
    c.fillStyle = 'rgba(255,255,255,.5)'; c.beginPath(); c.arc(-4, -4, 4, 0, 6.29); c.fill();
    c.lineWidth = 5; c.strokeStyle = C.gold;   // reads on teal discs and on paper alike
    for (let k = 0; k < 3; k++) { c.save(); c.rotate(k * Math.PI / 3 + t * 0.4); c.beginPath(); c.ellipse(0, 0, 46, 17, 0, 0, 6.29); c.stroke();
      const a = t * 2.2 + k * 2.1; c.fillStyle = C.coral; c.beginPath(); c.arc(Math.cos(a) * 46, Math.sin(a) * 17, 5.5, 0, 6.29); c.fill(); c.restore(); }
  },
  planet(c, t) {
    c.fillStyle = C.orange; c.beginPath(); c.arc(0, 0, 32, 0, 6.29); c.fill();
    c.fillStyle = 'rgba(0,0,0,.12)'; c.beginPath(); c.arc(8, 6, 28, 0, 6.29); c.fill();
    c.fillStyle = C.gold; for (const [x, y, r] of [[-12, -10, 6], [10, 12, 4], [-4, 16, 3]]) { c.beginPath(); c.arc(x, y, r, 0, 6.29); c.fill(); }
    c.strokeStyle = C.yellow; c.lineWidth = 5; c.beginPath(); c.ellipse(0, 0, 54, 13, -0.35, 0.1 * Math.PI, 0.9 * Math.PI, true); c.stroke();
  },
  telescope(c) {
    c.save(); c.rotate(-0.55); c.fillStyle = C.navy; rr(c, -40, -11, 70, 22, 6); c.fill(); c.fillStyle = C.blue; rr(c, 22, -15, 20, 30, 5); c.fill();
    c.fillStyle = 'rgba(255,255,255,.35)'; rr(c, -34, -8, 48, 5, 2); c.fill(); c.restore();
    c.strokeStyle = C.ink; c.lineWidth = 5; c.lineCap = 'round'; c.beginPath(); c.moveTo(0, 6); c.lineTo(-18, 44); c.moveTo(0, 6); c.lineTo(18, 44); c.moveTo(0, 6); c.lineTo(0, 46); c.stroke();
  },
  satellite(c, t) {
    c.save(); c.rotate(Math.sin(t) * 0.1);
    c.fillStyle = C.blue; rr(c, -58, -14, 34, 28, 3); c.fill(); rr(c, 24, -14, 34, 28, 3); c.fill();
    c.strokeStyle = 'rgba(255,255,255,.4)'; c.lineWidth = 2; for (const x of [-47, -36, 35, 46]) { c.beginPath(); c.moveTo(x, -14); c.lineTo(x, 14); c.stroke(); }
    c.fillStyle = C.gold; rr(c, -18, -18, 36, 36, 6); c.fill(); c.fillStyle = 'rgba(0,0,0,.12)'; rr(c, 2, -18, 16, 36, 4); c.fill();
    c.strokeStyle = C.ink; c.lineWidth = 3; c.beginPath(); c.moveTo(0, -18); c.lineTo(0, -32); c.stroke(); c.fillStyle = C.coral; c.beginPath(); c.arc(0, -34, 5, 0, 6.29); c.fill(); c.restore();
  },
  rocket(c, t) {
    const f = 0.8 + 0.2 * Math.sin(t * 20);
    c.fillStyle = C.yellow; c.beginPath(); c.moveTo(-9, 30); c.quadraticCurveTo(0, 30 + 26 * f, 9, 30); c.fill();
    c.fillStyle = C.coral; c.beginPath(); c.moveTo(-14, 10); c.lineTo(-26, 32); c.lineTo(-12, 28); c.fill(); c.beginPath(); c.moveTo(14, 10); c.lineTo(26, 32); c.lineTo(12, 28); c.fill();
    c.fillStyle = C.cream; c.beginPath(); c.moveTo(0, -46); c.bezierCurveTo(20, -26, 18, 12, 13, 30); c.lineTo(-13, 30); c.bezierCurveTo(-18, 12, -20, -26, 0, -46); c.fill();
    c.fillStyle = 'rgba(0,0,0,.1)'; c.beginPath(); c.moveTo(0, -46); c.bezierCurveTo(20, -26, 18, 12, 13, 30); c.lineTo(4, 30); c.bezierCurveTo(10, 6, 10, -26, 0, -46); c.fill();
    c.fillStyle = C.teal; c.beginPath(); c.arc(0, -10, 8, 0, 6.29); c.fill(); c.fillStyle = C.sky; c.beginPath(); c.arc(0, -10, 5, 0, 6.29); c.fill();
  },
  coin(c, t) {
    for (const [dx, dy] of [[-16, 18], [14, 22], [0, 8]]) { c.fillStyle = '#D9A93F'; c.beginPath(); c.ellipse(dx, dy + 5, 30, 10, 0, 0, 6.29); c.fill(); c.fillStyle = C.yellow; c.beginPath(); c.ellipse(dx, dy, 30, 10, 0, 0, 6.29); c.fill(); }
    const sp = Math.abs(Math.cos(t * 1.6));
    c.fillStyle = '#D9A93F'; c.beginPath(); c.ellipse(2, -22, 30 * sp + 3, 30, 0, 0, 6.29); c.fill();
    c.fillStyle = C.yellow; c.beginPath(); c.ellipse(0, -22, 30 * sp + 2, 30, 0, 0, 6.29); c.fill();
    if (sp > 0.4) { c.fillStyle = '#C08A2B'; c.font = `700 ${34 * sp}px ${FONT.num}`; c.textAlign = 'center'; c.textBaseline = 'middle'; c.save(); c.scale(sp, 1); c.fillText('$', 0, -20); c.restore(); }
  },
  trophy(c) {
    c.fillStyle = C.yellow; c.beginPath(); c.moveTo(-30, -36); c.lineTo(30, -36); c.bezierCurveTo(30, 0, 14, 10, 0, 12); c.bezierCurveTo(-14, 10, -30, 0, -30, -36); c.fill();
    c.fillStyle = 'rgba(0,0,0,.1)'; c.beginPath(); c.moveTo(6, -36); c.lineTo(30, -36); c.bezierCurveTo(30, 0, 14, 10, 0, 12); c.bezierCurveTo(10, 4, 14, -10, 6, -36); c.fill();
    c.strokeStyle = C.yellow; c.lineWidth = 6; c.beginPath(); c.arc(-30, -22, 12, Math.PI * 0.5, Math.PI * 1.5); c.stroke(); c.beginPath(); c.arc(30, -22, 12, -Math.PI * 0.5, Math.PI * 0.5); c.stroke();
    c.fillStyle = '#D9A93F'; c.fillRect(-6, 12, 12, 16); c.fillStyle = C.navy; rr(c, -24, 28, 48, 14, 4); c.fill();
    c.fillStyle = 'rgba(255,255,255,.5)'; c.beginPath(); c.ellipse(-14, -24, 5, 10, 0.2, 0, 6.29); c.fill();
  },
  brain(c, t) {
    c.fillStyle = '#F0A5A0'; for (const [x, y, r] of [[-20, -14, 22], [6, -22, 22], [26, -6, 20], [14, 16, 20], [-14, 14, 20], [-28, 2, 16]]) { c.beginPath(); c.arc(x, y, r, 0, 6.29); c.fill(); }
    c.fillStyle = 'rgba(0,0,0,.08)'; c.beginPath(); c.arc(16, 12, 20, 0, 6.29); c.fill();
    c.strokeStyle = '#C9706B'; c.lineWidth = 3.5; c.lineCap = 'round';
    for (const p of ['M-24 -16 q8 6 0 14', 'M0 -30 q-6 12 4 20', 'M20 -14 q-8 8 2 16', 'M-10 6 q8 4 14 -2', 'M-30 4 q6 0 8 8']) c.stroke(new Path2D(p));
    const pulse = 0.5 + 0.5 * Math.sin(t * 3); c.fillStyle = `rgba(255,216,107,${0.5 * pulse})`; c.beginPath(); c.arc(30, -36, 7, 0, 6.29); c.fill();
  },
  eye(c, t) {
    c.fillStyle = '#FFFFFF'; c.beginPath(); c.moveTo(-52, 0); c.quadraticCurveTo(0, -42, 52, 0); c.quadraticCurveTo(0, 42, -52, 0); c.fill();
    c.strokeStyle = C.ink; c.lineWidth = 5; c.stroke();   // outline so the eye reads on paper discs too
    const lx = Math.sin(t * 0.9) * 8; c.fillStyle = C.teal; c.beginPath(); c.arc(lx, 0, 18, 0, 6.29); c.fill();
    c.fillStyle = C.ink; c.beginPath(); c.arc(lx, 0, 9, 0, 6.29); c.fill(); c.fillStyle = '#fff'; c.beginPath(); c.arc(lx + 5, -5, 4, 0, 6.29); c.fill();
  },
  leaf(c, t) {
    c.save(); c.rotate(Math.sin(t * 1.3) * 0.08);
    c.fillStyle = C.green; c.beginPath(); c.moveTo(0, 44); c.bezierCurveTo(-46, 10, -30, -40, 0, -48); c.bezierCurveTo(30, -40, 46, 10, 0, 44); c.fill();
    c.fillStyle = 'rgba(0,0,0,.1)'; c.beginPath(); c.moveTo(0, 44); c.bezierCurveTo(30, 10, 30, -36, 0, -48); c.bezierCurveTo(30, -40, 46, 10, 0, 44); c.fill();
    c.strokeStyle = '#5E9478'; c.lineWidth = 3; c.beginPath(); c.moveTo(0, 44); c.lineTo(0, -38); for (const y of [-20, 0, 18]) { c.moveTo(0, y); c.lineTo(-16, y - 12); c.moveTo(0, y); c.lineTo(16, y - 12); } c.stroke(); c.restore();
  },
  wave(c, t) {
    for (let k = 0; k < 3; k++) { c.fillStyle = [C.blue, '#5C8AA6', C.teal][k]; c.beginPath(); c.moveTo(-56, 40);
      for (let x = -56; x <= 56; x += 4) c.lineTo(x, -6 + k * 16 + Math.sin(x / 12 + t * 2 + k) * 7); c.lineTo(56, 40); c.closePath(); c.fill(); }
    c.fillStyle = 'rgba(255,255,255,.5)'; for (const x of [-30, 6, 34]) { c.beginPath(); c.arc(x, -8 + Math.sin(x / 12 + t * 2) * 7, 3, 0, 6.29); c.fill(); }
  },
  rhino(c, t) {
    const b = Math.sin(t * 2) * 1.5; c.fillStyle = '#8D8C93';
    c.beginPath(); c.ellipse(-4, 6 + b, 46, 26, 0, 0, 6.29); c.fill();
    for (const x of [-34, -16, 10, 26]) rr(c, x, 18 + b, 12, 26, 4), c.fill();
    c.beginPath(); c.ellipse(40, -2 + b, 22, 18, 0.2, 0, 6.29); c.fill();
    c.fillStyle = '#E9DFC9'; c.beginPath(); c.moveTo(52, -10 + b); c.lineTo(66, -34 + b); c.lineTo(60, -8 + b); c.fill(); c.beginPath(); c.moveTo(44, -16 + b); c.lineTo(50, -28 + b); c.lineTo(50, -14 + b); c.fill();
    c.fillStyle = 'rgba(0,0,0,.12)'; c.beginPath(); c.ellipse(-4, 20 + b, 44, 12, 0, 0, Math.PI); c.fill();
    c.fillStyle = '#76757C'; c.beginPath(); c.ellipse(28, -20 + b, 6, 9, -0.4, 0, 6.29); c.fill(); c.fillStyle = C.ink; c.beginPath(); c.arc(44, -6 + b, 2.5, 0, 6.29); c.fill();
  },
  drone(c, t) {
    const hov = Math.sin(t * 3) * 3; c.translate(0, hov);
    c.strokeStyle = C.navy; c.lineWidth = 6; c.lineCap = 'round'; c.beginPath(); c.moveTo(-40, -14); c.lineTo(40, 14); c.moveTo(-40, 14); c.lineTo(40, -14); c.stroke();
    for (const [x, y] of [[-40, -14], [40, 14], [-40, 14], [40, -14]]) { c.fillStyle = 'rgba(108,155,184,.45)'; c.beginPath(); c.ellipse(x, y - 8, 22 * Math.abs(Math.sin(t * 30 + x)), 4, 0, 0, 6.29); c.fill(); c.fillStyle = C.navy; c.beginPath(); c.arc(x, y - 4, 4, 0, 6.29); c.fill(); }
    c.fillStyle = C.coral; rr(c, -18, -12, 36, 24, 8); c.fill(); c.fillStyle = C.ink; c.beginPath(); c.arc(0, 14, 7, 0, 6.29); c.fill(); c.fillStyle = C.sky; c.beginPath(); c.arc(0, 14, 3.5, 0, 6.29); c.fill();
  },
  bridge(c) {
    c.fillStyle = C.blue; c.fillRect(-60, 30, 120, 14);
    c.fillStyle = '#B9AE97'; c.fillRect(-60, 6, 120, 10);
    c.strokeStyle = C.coral; c.lineWidth = 6; c.beginPath(); c.moveTo(-52, 6); c.quadraticCurveTo(0, -60, 52, 6); c.stroke();
    c.lineWidth = 2.5; for (let x = -40; x <= 40; x += 10) { const y = 6 - 66 * (1 - Math.pow(x / 52, 2)) * 0.5; c.beginPath(); c.moveTo(x, y); c.lineTo(x, 6); c.stroke(); }
    c.fillStyle = '#9E937D'; c.fillRect(-56, 16, 10, 18); c.fillRect(46, 16, 10, 18);
  },
  building(c) {
    const w = [[-50, 20, 26, 60], [-20, -20, 34, 100], [18, 0, 30, 80]]; const col = [C.blue, C.coral, C.green];
    w.forEach(([x, y, ww, h], k) => { c.fillStyle = col[k]; rr(c, x, y - 30, ww, h, 3); c.fill(); c.fillStyle = 'rgba(255,255,255,.5)'; for (let yy = y - 22; yy < y - 30 + h - 8; yy += 12) for (let xx = x + 5; xx < x + ww - 6; xx += 9) c.fillRect(xx, yy, 5, 6); });
  },
  gear(c, t) {
    c.save(); c.rotate(t * 0.8); c.fillStyle = C.teal; c.beginPath();
    for (let i = 0; i < 16; i++) { const a = i * Math.PI / 8, r = i % 2 ? 34 : 44; c.lineTo(Math.cos(a) * r, Math.sin(a) * r); } c.closePath(); c.fill();
    c.fillStyle = C.cream; c.beginPath(); c.arc(0, 0, 13, 0, 6.29); c.fill(); c.restore();
    c.save(); c.translate(42, 34); c.rotate(-t * 1.4); c.fillStyle = C.coral; c.beginPath(); for (let i = 0; i < 12; i++) { const a = i * Math.PI / 6, r = i % 2 ? 14 : 19; c.lineTo(Math.cos(a) * r, Math.sin(a) * r); } c.closePath(); c.fill(); c.fillStyle = C.cream; c.beginPath(); c.arc(0, 0, 5, 0, 6.29); c.fill(); c.restore();
  },
  chart(c, t) {
    c.fillStyle = C.paper; rr(c, -52, -44, 104, 88, 10); c.fill();
    const h = [26, 40, 30, 58]; h.forEach((v, k) => { const g = eOutBack(clamp(t * 0.8 - k * 0.15)); c.fillStyle = [C.blue, C.green, C.gold, C.coral][k]; rr(c, -40 + k * 22, 34 - v * g, 16, v * g, 3); c.fill(); });
    c.strokeStyle = C.ink; c.lineWidth = 3; c.beginPath(); c.moveTo(-44, 36); c.lineTo(44, 36); c.stroke();
  },
  magnifier(c, t) {
    c.save(); c.rotate(Math.sin(t * 1.2) * 0.12);
    c.strokeStyle = C.navy; c.lineWidth = 12; c.lineCap = 'round'; c.beginPath(); c.moveTo(18, 18); c.lineTo(46, 46); c.stroke();
    c.fillStyle = C.sky; c.beginPath(); c.arc(-6, -6, 32, 0, 6.29); c.fill(); c.lineWidth = 8; c.strokeStyle = C.teal; c.stroke();
    c.fillStyle = 'rgba(255,255,255,.6)'; c.beginPath(); c.ellipse(-16, -16, 8, 13, 0.7, 0, 6.29); c.fill(); c.restore();
  },
  lightbulb(c, t) {
    const g = 0.6 + 0.4 * Math.sin(t * 2.5); c.fillStyle = `rgba(255,216,107,${0.35 * g})`; c.beginPath(); c.arc(0, -10, 50, 0, 6.29); c.fill();
    c.fillStyle = C.yellow; c.beginPath(); c.arc(0, -12, 30, Math.PI * 0.8, Math.PI * 2.2); c.lineTo(12, 22); c.lineTo(-12, 22); c.closePath(); c.fill();
    c.fillStyle = 'rgba(0,0,0,.08)'; c.beginPath(); c.arc(0, -12, 30, -Math.PI * 0.4, Math.PI * 0.2); c.lineTo(12, 22); c.lineTo(4, 22); c.closePath(); c.fill();
    c.fillStyle = '#9E937D'; rr(c, -12, 22, 24, 16, 4); c.fill(); c.strokeStyle = 'rgba(255,255,255,.5)'; c.lineWidth = 2; c.beginPath(); c.moveTo(-12, 28); c.lineTo(12, 28); c.moveTo(-12, 33); c.lineTo(12, 33); c.stroke();
    c.fillStyle = 'rgba(255,255,255,.7)'; c.beginPath(); c.ellipse(-12, -20, 5, 9, 0.4, 0, 6.29); c.fill();
  },
  clock(c, t) {
    c.fillStyle = C.paper; c.beginPath(); c.arc(0, 0, 42, 0, 6.29); c.fill(); c.lineWidth = 7; c.strokeStyle = C.coral; c.stroke();
    c.strokeStyle = C.ink; c.lineCap = 'round'; c.lineWidth = 5; c.beginPath(); c.moveTo(0, 0); c.lineTo(Math.cos(t * 0.5 - 1.57) * 22, Math.sin(t * 0.5 - 1.57) * 22); c.stroke();
    c.lineWidth = 3.5; c.beginPath(); c.moveTo(0, 0); c.lineTo(Math.cos(t * 3 - 1.57) * 32, Math.sin(t * 3 - 1.57) * 32); c.stroke();
    for (let i = 0; i < 12; i++) { const a = i * Math.PI / 6; c.fillStyle = C.ink; c.beginPath(); c.arc(Math.cos(a) * 34, Math.sin(a) * 34, i % 3 ? 1.5 : 3, 0, 6.29); c.fill(); }
  },
  book(c, t) {
    const o = 0.9 + 0.1 * Math.sin(t * 2);
    c.fillStyle = C.teal; c.beginPath(); c.moveTo(0, 30); c.lineTo(-56 * o, 20); c.lineTo(-56 * o, -34); c.lineTo(0, -24); c.lineTo(56 * o, -34); c.lineTo(56 * o, 20); c.closePath(); c.fill();
    c.fillStyle = C.paper; c.beginPath(); c.moveTo(0, 24); c.lineTo(-50 * o, 14); c.lineTo(-50 * o, -38); c.lineTo(0, -28); c.closePath(); c.fill();
    c.fillStyle = C.cream; c.beginPath(); c.moveTo(0, 24); c.lineTo(50 * o, 14); c.lineTo(50 * o, -38); c.lineTo(0, -28); c.closePath(); c.fill();
    c.strokeStyle = 'rgba(59,42,38,.25)'; c.lineWidth = 2.5; for (let k = 0; k < 4; k++) { c.beginPath(); c.moveTo(-42 * o, -24 + k * 10); c.lineTo(-8, -16 + k * 10); c.moveTo(8, -16 + k * 10); c.lineTo(42 * o, -24 + k * 10); c.stroke(); }
  },
  dna(c, t) {
    for (let i = 0; i < 12; i++) { const y = -48 + i * 8.5, a = t * 2 + i * 0.55, x = Math.sin(a) * 26;
      c.strokeStyle = 'rgba(59,42,38,.25)'; c.lineWidth = 2.5; c.beginPath(); c.moveTo(-x, y); c.lineTo(x, y); c.stroke();
      c.fillStyle = Math.cos(a) > 0 ? C.coral : C.blue; c.beginPath(); c.arc(x, y, 5, 0, 6.29); c.fill(); c.fillStyle = Math.cos(a) > 0 ? C.blue : C.coral; c.beginPath(); c.arc(-x, y, 5, 0, 6.29); c.fill(); }
  },
  flame(c, t) {
    const f = Math.sin(t * 9) * 3;
    c.fillStyle = C.coral; c.beginPath(); c.moveTo(0, 40); c.bezierCurveTo(-40, 30, -30, -10, -6 + f, -48); c.bezierCurveTo(4, -20, 34, -14, 30, 14); c.bezierCurveTo(28, 32, 14, 40, 0, 40); c.fill();
    c.fillStyle = C.yellow; c.beginPath(); c.moveTo(0, 40); c.bezierCurveTo(-20, 32, -16, 6, -2 - f, -16); c.bezierCurveTo(6, 0, 18, 6, 16, 22); c.bezierCurveTo(14, 34, 8, 40, 0, 40); c.fill();
  },
  lightning(c, t) {
    const g = 0.7 + 0.3 * Math.abs(Math.sin(t * 6)); c.fillStyle = `rgba(255,216,107,${0.3 * g})`; c.beginPath(); c.arc(0, 0, 48, 0, 6.29); c.fill();
    c.fillStyle = C.yellow; c.beginPath(); c.moveTo(8, -48); c.lineTo(-24, 6); c.lineTo(-2, 6); c.lineTo(-10, 48); c.lineTo(24, -8); c.lineTo(2, -8); c.closePath(); c.fill();
    c.fillStyle = 'rgba(0,0,0,.1)'; c.beginPath(); c.moveTo(24, -8); c.lineTo(-10, 48); c.lineTo(4, 4); c.closePath(); c.fill();
  },
  shield(c) {
    c.fillStyle = C.teal; c.beginPath(); c.moveTo(0, -46); c.lineTo(38, -32); c.bezierCurveTo(38, 10, 22, 34, 0, 46); c.bezierCurveTo(-22, 34, -38, 10, -38, -32); c.closePath(); c.fill();
    c.fillStyle = 'rgba(0,0,0,.12)'; c.beginPath(); c.moveTo(0, -46); c.lineTo(38, -32); c.bezierCurveTo(38, 10, 22, 34, 0, 46); c.closePath(); c.fill();
    c.strokeStyle = C.yellow; c.lineWidth = 7; c.lineCap = 'round'; c.lineJoin = 'round'; c.beginPath(); c.moveTo(-14, 0); c.lineTo(-3, 12); c.lineTo(16, -12); c.stroke();
  },
  globe(c, t) {
    c.fillStyle = C.blue; c.beginPath(); c.arc(0, 0, 42, 0, 6.29); c.fill();
    c.save(); c.beginPath(); c.arc(0, 0, 42, 0, 6.29); c.clip(); c.fillStyle = C.green; const o = (t * 14) % 120 - 60;
    for (const [x, y, w, h] of [[-30, -18, 34, 20], [10, 4, 30, 26], [-16, 20, 20, 12], [40, -24, 26, 16]]) { c.beginPath(); c.ellipse(x + o, y, w / 2, h / 2, 0.3, 0, 6.29); c.fill(); c.beginPath(); c.ellipse(x + o - 120, y, w / 2, h / 2, 0.3, 0, 6.29); c.fill(); }
    c.fillStyle = 'rgba(0,0,0,.12)'; c.beginPath(); c.arc(14, 10, 42, 0, 6.29); c.fill(); c.restore();
    c.strokeStyle = 'rgba(255,255,255,.35)'; c.lineWidth = 2; c.beginPath(); c.ellipse(0, 0, 42, 14, 0, 0, 6.29); c.stroke();
  },
  people(c, t) {
    const cols = [C.coral, C.teal, C.gold]; [[-34, 6, 0.85], [34, 6, 0.85], [0, -2, 1]].forEach(([x, y, s], k) => {
      const b = Math.sin(t * 2 + k) * 1.5; c.save(); c.translate(x, y + b); c.scale(s, s);
      c.fillStyle = cols[k]; c.beginPath(); c.moveTo(-22, 40); c.bezierCurveTo(-22, 8, 22, 8, 22, 40); c.fill();
      c.fillStyle = '#E9B48F'; c.beginPath(); c.arc(0, -10, 15, 0, 6.29); c.fill(); c.restore(); });
  },
  heart(c, t) {
    const s = 1 + 0.06 * Math.max(0, Math.sin(t * 6)); c.scale(s, s);
    c.fillStyle = C.coral; c.beginPath(); c.moveTo(0, 38); c.bezierCurveTo(-54, 2, -30, -46, 0, -18); c.bezierCurveTo(30, -46, 54, 2, 0, 38); c.fill();
    c.fillStyle = 'rgba(255,255,255,.45)'; c.beginPath(); c.ellipse(-20, -16, 6, 10, 0.6, 0, 6.29); c.fill();
  },
  star(c, t) {
    c.save(); c.rotate(t * 0.3); c.fillStyle = C.yellow; c.beginPath(); for (let i = 0; i < 10; i++) { const a = i * Math.PI / 5 - Math.PI / 2, r = i % 2 ? 20 : 46; c.lineTo(Math.cos(a) * r, Math.sin(a) * r); } c.closePath(); c.fill(); c.restore();
  },
  question(c, t) {
    c.fillStyle = C.paper; c.font = `400 92px ${FONT.num}`; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('?', 0, 6 + Math.sin(t * 2) * 2);
  },
};
function icon(c, name, x, y, size, t) {
  const fn = ICONS[name] || ICONS.lightbulb; c.save(); c.translate(x, y); c.scale(size / 100, size / 100); fn(c, t); c.restore();
}

// ---------------------------------------------------------------- shared scene parts
function background(c, sc, t) {
  const [base, mid, accent] = BG[sc.background] || BG.cream;
  c.fillStyle = base; c.fillRect(-100, -100, DW + 200, DH + 200);
  const r = rng(sc.scene_id + 'bg');
  for (let k = 0; k < 5; k++) {
    const x = r() * DW, y = r() * DH, rad = 90 + r() * 160, dx = Math.sin(t * 0.25 + k) * 14, dy = Math.cos(t * 0.2 + k) * 12;
    c.fillStyle = k % 2 ? mid : 'rgba(255,255,255,.35)'; c.beginPath(); c.arc(x + dx, y + dy, rad, 0, 6.29); c.fill();
  }
  c.fillStyle = mid; c.beginPath(); c.moveTo(-100, 820); c.quadraticCurveTo(DW / 2, 770 + Math.sin(t * 0.6) * 8, DW + 100, 820); c.lineTo(DW + 100, DH + 100); c.lineTo(-100, DH + 100); c.fill();
  c.fillStyle = accent; c.globalAlpha = 0.18; c.beginPath(); c.arc(DW * 0.85, 150, 70, 0, 6.29); c.fill(); c.globalAlpha = 1;
}
const DUST = (() => { const r = rng('dust'); return Array.from({ length: 40 }, () => ({ x: r() * DW, y: r() * DH, s: 0.6 + r() * 1.6, v: 3 + r() * 7, ph: r() * 6.28 })); })();
function dust(c, t) { for (const d of DUST) { const y = ((d.y - t * d.v) % DH + DH) % DH, x = d.x + Math.sin(t * 0.7 + d.ph) * 6; c.fillStyle = 'rgba(255,253,240,.5)'; c.beginPath(); c.arc(x, y, d.s, 0, 6.29); c.fill(); } }
function disc(c, x, y, r, col, s) { if (s <= 0) return; c.save(); c.translate(x, y); c.scale(s, s); c.fillStyle = 'rgba(0,0,0,.1)'; c.beginPath(); c.arc(4, 8, r, 0, 6.29); c.fill(); c.fillStyle = col; c.beginPath(); c.arc(0, 0, r, 0, 6.29); c.fill(); c.restore(); }
function sparkle(c, x, y, s, rot) { if (s <= 0) return; c.save(); c.translate(x, y); c.rotate(rot); c.scale(s, s); c.fillStyle = C.yellow; c.beginPath(); for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4, r = i % 2 ? 2.5 : 9; c.lineTo(Math.cos(a) * r, Math.sin(a) * r); } c.closePath(); c.fill(); c.restore(); }
function pill(c, x, y, label, t0, t, col = C.yellow, ink = C.tealD, size = 22) {
  const s = popS(t, t0, 0.4); if (s <= 0) return;
  c.save(); c.translate(x, y); c.scale(s, s); c.font = `600 ${size}px ${FONT.head}`; const w = c.measureText(label).width + size * 1.2;
  c.fillStyle = 'rgba(0,0,0,.12)'; rr(c, -w / 2 + 2, -size * 0.85 + 4, w, size * 1.7, size * 0.85); c.fill();
  c.fillStyle = col; rr(c, -w / 2, -size * 0.85, w, size * 1.7, size * 0.85); c.fill(); c.restore();
  c.save(); c.translate(x, y); c.scale(s, s); txt(c, label, 0, 1, { size, weight: 600, color: ink, tag: 'pill' }); c.restore();
}
// Headline: words pop in order, the highlight word sits on a yellow pill. Wraps and shrinks to stay in SAFE.
function headline(c, text, hi, cx, cy, maxW, t, t0, size = 46, color = C.ink, align = 'center') {
  if (!text) return;
  maxW = Math.min(maxW, (SAFE.x1 - SAFE.x0) * 0.86);
  const L = layoutLines(c, text, maxW, size, 700, FONT.head, 3, 26);
  const lh = L.size * 1.14; let idx = 0;
  L.lines.forEach((line, li) => {
    const y = cy + (li - (L.lines.length - 1) / 2) * lh;
    c.font = `700 ${L.size}px ${FONT.head}`;
    const words = line.split(' '), sp = c.measureText(' ').width, widths = words.map(w => c.measureText(w).width);
    const total = widths.reduce((a, b) => a + b, 0) + sp * (words.length - 1);
    let x = align === 'center' ? cx - total / 2 : cx;
    words.forEach((w, k) => {
      const s = popS(t, t0 + idx * 0.07, 0.38, 2.0), clean = w.replace(/[.,!?;:"']/g, '');
      if (s > 0) {
        c.save(); c.translate(x + widths[k] / 2, y); c.scale(s, s);
        if (hi && clean.toLowerCase() === String(hi).toLowerCase()) { c.fillStyle = C.yellow; rr(c, -widths[k] / 2 - 8, -L.size * 0.62, widths[k] + 16, L.size * 1.18, 12); c.fill(); }
        txt(c, w, 0, 0, { size: L.size, weight: 700, color, tag: 'headline' }); c.restore();
      }
      x += widths[k] + sp; idx++;
    });
  });
  return L.lines.length * lh;
}
function kicker(c, label, t, t0) { if (!label) return; pill(c, DW / 2, SAFE.y0 + 22, String(label).toUpperCase().slice(0, 26), t0, t, C.teal, C.paper, 18); }
function fmtNum(v, target, unit) {
  const dec = String(target).includes('.') ? 1 : 0; const n = v.toFixed(dec);
  return Number(n).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) + (unit === '%' ? '%' : '');
}

// ---------------------------------------------------------------- scene templates (local time u = t - start)
// photo inside a rectangle: cover-fit, crop centred on the measured focus point, per-scene camera move
function photoInto(c, img, x, y, w, h, u, d, ph) {
  const f = ph.focus || { x: 0.5, y: 0.5 }, e = eInOut(prog(u, 0, Math.max(0.5, d)));
  let z = 1.08, ox = 0, oy = 0;
  switch (ph.motion) {
    case 'push_in': z = lerp(1.02, 1.14, e); break;
    case 'pull_out': z = lerp(1.16, 1.04, e); break;
    case 'pan_left': z = 1.14; ox = lerp(0.05, -0.05, e); break;
    case 'pan_right': z = 1.14; ox = lerp(-0.05, 0.05, e); break;
    case 'rise': z = 1.12; oy = lerp(0.045, -0.045, e); break;
  }
  const iw = img.naturalWidth, ih = img.naturalHeight, s = Math.max(w / iw, h / ih) * z, dw = iw * s, dh = ih * s;
  let dx = x + w / 2 - f.x * dw + ox * w, dy = y + h / 2 - f.y * dh + oy * h;
  dx = Math.min(x, Math.max(x + w - dw, dx)); dy = Math.min(y, Math.max(y + h - dh, dy));
  c.drawImage(img, dx, dy, dw, dh);
}
const T = {
  title(c, sc, u, d) {
    const r = rng(sc.scene_id);
    disc(c, DW / 2, 500, 175, C.teal, popS(u, 0.25, 0.55, 1.8));
    c.save(); c.translate(DW / 2, 500); const w = wobble(u, 0.6); c.scale(1 + w, 1 - w);
    const s = popS(u, 0.45, 0.55, 2.4); if (s > 0) { c.scale(s, s); icon(c, sc.main_subject, 0, 0, 210, u); } c.restore();
    for (let k = 0; k < 5; k++) sparkle(c, DW / 2 + Math.cos(k * 1.3) * (195 + r() * 30), 500 + Math.sin(k * 1.3) * (195 + r() * 30), popS(u, 0.9 + k * 0.08, 0.4) * (0.8 + 0.3 * Math.sin(u * 3 + k)), u + k);
    kicker(c, PLAN.category || 'CrazzyDocs', u, 0.2);
    headline(c, sc.headline, sc.highlight, DW / 2, 215, SAFE.x1 - SAFE.x0, u, 0.55, 54);
    if (sc.supporting_objects[0]) { disc(c, 105, 705, 52, C.paper, popS(u, 1.2, 0.45)); const s2 = popS(u, 1.3, 0.45); if (s2 > 0) { c.save(); c.translate(105, 705); c.scale(s2, s2); icon(c, sc.supporting_objects[0], 0, 0, 60, u); c.restore(); } }
  },
  stat(c, sc, u, d) {
    const n = (sc.numbers || []).find(x => !x.is_year) || sc.numbers[0];
    const s = popS(u, 0.2, 0.55, 1.8), bw = wobble(u, 0.9) * 0.5;
    if (s > 0) {
      c.save(); c.translate(DW / 2, 430); c.scale(s * (1 + bw), s * (1 - bw));
      c.fillStyle = 'rgba(0,0,0,.1)'; c.beginPath(); c.ellipse(6, 12, 210, 150, 0, 0, 6.29); c.fill();
      c.fillStyle = C.teal; c.beginPath(); c.ellipse(0, 0, 210, 150, 0, 0, 6.29); c.fill();
      c.fillStyle = C.tealD; c.beginPath(); c.ellipse(0, 40, 190, 90, 0, 0, Math.PI); c.fill(); c.fillStyle = C.teal; c.beginPath(); c.ellipse(0, 30, 196, 84, 0, 0, Math.PI); c.fill();
      c.strokeStyle = 'rgba(255,255,255,.14)'; c.lineWidth = 5; c.lineCap = 'round'; for (const [x, y, l] of [[-180, -40, 26], [-176, 50, 20], [150, -70, 24], [156, 60, 18]]) { c.beginPath(); c.moveTo(x, y); c.lineTo(x + l, y); c.stroke(); }
      icon(c, sc.main_subject, -110, -10, 110, u);
      if (n) {
        const cp = eOutCubic(prog(u, 0.6, Math.min(1.6, d * 0.45)));
        const val = n.value * cp, ts = popS(u, 0.55, 0.35);
        c.save(); c.translate(70, -30); c.scale(ts, ts);
        const label = fmtNum(val, n.text, n.unit), fs = fitFont(c, fmtNum(n.value, n.text, n.unit), 200, 86, 400, FONT.num, 30);
        txt(c, label, 0, 0, { size: fs, weight: 400, fam: FONT.num, color: C.paper, tag: 'number' }); c.restore();
        if (n.unit && n.unit !== '%') pill(c, 70, 50, n.unit, 0.6 + Math.min(1.6, d * 0.45), u, C.yellow, C.tealD, 20);
      }
      c.restore();
    }
    for (const [k, dx] of [[0, -150], [1, 150]]) { const p = popS(u, 0.3 + k * 0.1, 0.35); if (p > 0) { c.fillStyle = C.teal; c.beginPath(); c.arc(DW / 2 + dx * 0.6, 600 + k * 20, 10 * p, 0, 6.29); c.fill(); } }
    headline(c, sc.headline, sc.highlight, DW / 2, 690, SAFE.x1 - SAFE.x0, u, 1.0, 38);
    kicker(c, sc.stage_label || '', u, 0.1);
  },
  compare(c, sc, u, d) {
    const nums = (sc.numbers || []).filter(x => !x.is_year).slice(0, 2);
    const mx = Math.max(...nums.map(n => n.value), 1), base = 640, H = 330;
    headline(c, sc.headline, sc.highlight, DW / 2, 170, SAFE.x1 - SAFE.x0, u, 0.2, 40);
    c.fillStyle = C.ink; c.globalAlpha = 0.85; rr(c, 70, base, 400, 6, 3); c.fill(); c.globalAlpha = 1;
    nums.forEach((n, k) => {
      const g = eOutBack(prog(u, 0.5 + k * 0.45, 0.8), 1.4), h = Math.max(24, H * n.value / mx) * g, x = 150 + k * 170;
      c.fillStyle = 'rgba(0,0,0,.1)'; rr(c, x - 50 + 5, base - h + 6, 100, h, 14); c.fill();
      c.fillStyle = [C.coral, C.teal][k]; rr(c, x - 50, base - h, 100, h, 14); c.fill();
      c.fillStyle = 'rgba(255,255,255,.18)'; rr(c, x - 42, base - h + 8, 18, Math.max(0, h - 16), 8); c.fill();
      if (g > 0.05) txt(c, fmtNum(n.value * clamp(g), n.text, n.unit), x, base - h - 30, { size: 34, fam: FONT.num, weight: 400, color: C.ink, tag: 'number' });
      if (n.unit && n.unit !== '%') pill(c, x, base + 40, n.unit.slice(0, 16), 0.9 + k * 0.45, u, C.yellow, C.tealD, 16);
    });
    icon(c, sc.main_subject, DW / 2, 270, 70, u);
  },
  timeline(c, sc, u, d) {
    const years = (sc.numbers || []).filter(x => x.is_year).slice(0, 3);
    const pts = [[120, 310], [400, 440], [140, 580], [380, 700]];
    const p = eInOut(prog(u, 0.3, Math.min(2.2, d * 0.6))), n = 60;
    const pt = v => { const seg = Math.min(2, Math.floor(v * 3)), f = v * 3 - seg, a = pts[seg], b = pts[seg + 1]; return [lerp(a[0], b[0], f) + Math.sin(f * Math.PI) * 30 * (seg % 2 ? -1 : 1), lerp(a[1], b[1], f)]; };
    for (let i = 0; i <= n; i++) { const v = i / n; if (v > p) break; const [x, y] = pt(v), a = clamp((p - v) * 12); c.fillStyle = `rgba(232,93,74,${0.9 * a})`; c.beginPath(); c.arc(x, y, 4.2 * (0.6 + 0.4 * a), 0, 6.29); c.fill(); }
    const labels = years.length ? years.map(y => y.text) : [];
    labels.forEach((lab, k) => {
      const at = 0.3 + (k + 1) / (labels.length + 1) * Math.min(2.2, d * 0.6); const [x, y] = pt((k + 1) / (labels.length + 1));
      const s = popS(u, at, 0.5); if (s <= 0) return; const bob = Math.sin((u - at) * 5) * 1.5;
      c.save(); c.translate(x, y - 6 + bob); c.scale(s, s); c.fillStyle = C.coral; c.beginPath(); c.moveTo(0, 12); c.bezierCurveTo(-18, -8, -16, -32, 0, -32); c.bezierCurveTo(16, -32, 18, -8, 0, 12); c.fill();
      c.fillStyle = C.paper; c.beginPath(); c.arc(0, -18, 6.5, 0, 6.29); c.fill(); c.restore();
      pill(c, x + (x < DW / 2 ? 90 : -90), y - 18, lab, at + 0.1, u, C.yellow, C.tealD, 24);
    });
    const end = pts[3]; disc(c, end[0], end[1], 68, C.teal, popS(u, 0.3 + Math.min(2.2, d * 0.6), 0.5));
    const es = popS(u, 0.4 + Math.min(2.2, d * 0.6), 0.5); if (es > 0) { c.save(); c.translate(end[0], end[1]); c.scale(es, es); icon(c, sc.main_subject, 0, 0, 92, u); c.restore(); }
    headline(c, sc.headline, sc.highlight, DW / 2, 180, SAFE.x1 - SAFE.x0, u, 0.2, 40);
  },
  process(c, sc, u, d) {
    const nodes = [sc.main_subject, ...(sc.supporting_objects || [])].slice(0, 3);
    while (nodes.length < 3) nodes.push(['lightbulb', 'gear', 'star'][nodes.length]);
    const ys = [330, 480, 630], xs = [170, 370, 170];
    nodes.forEach((ic, k) => {
      const at = 0.3 + k * 0.55, s = popS(u, at, 0.5);
      if (k > 0) { const a = eInOut(prog(u, at - 0.35, 0.4)); if (a > 0) { const [x0, y0, x1, y1] = [xs[k - 1], ys[k - 1], xs[k], ys[k]];
        c.strokeStyle = C.coral; c.lineWidth = 6; c.lineCap = 'round'; c.setLineDash([2, 14]); c.beginPath(); c.moveTo(x0, y0); c.quadraticCurveTo((x0 + x1) / 2 + 40, (y0 + y1) / 2, lerp(x0, x1, a), lerp(y0, y1, a)); c.stroke(); c.setLineDash([]); } }
      disc(c, xs[k], ys[k], 62, [C.paper, C.teal, C.coral][k], s);
      if (s > 0) { c.save(); c.translate(xs[k], ys[k]); c.scale(s, s); icon(c, ic, 0, 0, 78, u + k); c.restore(); }
      const lab = (sc.labels || [])[k] || ['1', '2', '3'][k];
      pill(c, xs[k] + (xs[k] < DW / 2 ? 130 : -130), ys[k], lab.slice(0, 16), at + 0.15, u, C.yellow, C.tealD, 20);
    });
    headline(c, sc.headline, sc.highlight, DW / 2, 175, SAFE.x1 - SAFE.x0, u, 0.15, 40);
  },
  question(c, sc, u, d) {
    const s = popS(u, 0.2, 0.55, 2.0), sway = Math.sin(u * 1.6) * 0.06;
    c.save(); c.translate(DW / 2, 430); c.rotate(sway); disc(c, 0, 0, 140, C.coral, s); if (s > 0) { c.scale(s, s); icon(c, 'question', 0, 0, 200, u); } c.restore();
    const a = u * 0.9; const os = popS(u, 0.6, 0.5);
    if (os > 0) { const x = DW / 2 + Math.cos(a) * 190, y = 430 + Math.sin(a) * 80; disc(c, x, y, 52, C.paper, os); c.save(); c.translate(x, y); c.scale(os, os); icon(c, sc.main_subject, 0, 0, 66, u); c.restore(); }
    headline(c, sc.headline, sc.highlight, DW / 2, 680, SAFE.x1 - SAFE.x0, u, 0.5, 42);
  },
  spotlight(c, sc, u, d) {
    const s = popS(u, 0.15, 0.55, 2.0);
    disc(c, DW / 2, 420, 150, C.paper, s);
    if (s > 0) { c.save(); c.translate(DW / 2, 420); c.rotate(u * 0.35); c.setLineDash([14, 12]); c.strokeStyle = C.teal; c.lineWidth = 5; c.beginPath(); c.arc(0, 0, 172 * s, 0, 6.29); c.stroke(); c.setLineDash([]); c.restore();
      c.save(); c.translate(DW / 2, 420); const w = wobble(u, 0.5); c.scale(s * (1 + w), s * (1 - w)); icon(c, sc.main_subject, 0, 0, 165, u); c.restore(); }
    (sc.supporting_objects || []).slice(0, 2).forEach((ic, k) => {
      const at = 0.7 + k * 0.25, p = eOutBack(prog(u, at, 0.6), 1.4), side = k ? 1 : -1;
      const x = DW / 2 + side * lerp(360, 190, p) + Math.sin(u + k) * 6, y = 420 + (k ? 120 : -130) + Math.cos(u * 1.2 + k) * 6;
      disc(c, x, y, 46, k ? C.gold : C.sky, clamp(p)); if (p > 0) { c.save(); c.translate(x, y); c.scale(clamp(p), clamp(p)); icon(c, ic, 0, 0, 58, u); c.restore(); }
    });
    headline(c, sc.headline, sc.highlight, DW / 2, 690, SAFE.x1 - SAFE.x0, u, 0.45, 42);
  },
  callout(c, sc, u, d) {
    const r = rng(sc.scene_id + 'co');
    (sc.supporting_objects || []).slice(0, 2).forEach((ic, k) => {
      const s = popS(u, 0.5 + k * 0.2, 0.5) * 0.9, x = k ? 400 : 140, y = k ? 660 : 245;
      if (s > 0) { c.save(); c.globalAlpha = 0.9; disc(c, x, y + Math.sin(u + k) * 5, 44, k ? C.gold : C.sky, s); c.translate(x, y + Math.sin(u + k) * 5); c.scale(s, s); icon(c, ic, 0, 0, 56, u); c.restore(); }
    });
    const p = eOutBack(prog(u, 0.1, 0.6), 1.5), rot = lerp(-0.18, -0.035, clamp(p));
    if (p > 0) {
      c.save(); c.translate(DW / 2, 455); c.rotate(rot); c.scale(clamp(p), clamp(p));
      c.fillStyle = 'rgba(0,0,0,.12)'; rr(c, -196, -150, 400, 312, 28); c.fill();
      c.fillStyle = C.paper; rr(c, -200, -158, 400, 312, 28); c.fill();
      c.fillStyle = C.coral; rr(c, -200, -158, 400, 16, [28, 28, 0, 0]); c.fill();
      const hh = headline(c, sc.headline, sc.highlight, 0, 10, 330, u, 0.35, 52) || 0;
      const sw = eInOut(prog(u, 0.9, 0.5));
      if (sw > 0) { c.strokeStyle = C.coral; c.lineWidth = 7; c.lineCap = 'round'; c.beginPath(); c.moveTo(-120, 10 + hh / 2 + 14);
        c.quadraticCurveTo(0, 10 + hh / 2 + 26, lerp(-120, 120, sw), 10 + hh / 2 + 12); c.stroke(); }
      c.restore();
    }
    const bs = popS(u, 0.55, 0.45, 2.4);
    disc(c, DW / 2 - 175, 300, 50, C.teal, bs); if (bs > 0) { c.save(); c.translate(DW / 2 - 175, 300); c.scale(bs, bs); icon(c, sc.main_subject, 0, 0, 62, u); c.restore(); }
  },
  photo(c, sc, u, d) {
    // Real photographs composited into the Pop Info identity. Treatment and camera move come from
    // the plan (never repeated back to back); the crop is centred on the measured focus point.
    const img = IMAGES[sc.scene_id], ph = sc.photo || {}, kind = ph.treatment || 'card';
    const n = (sc.numbers || []).find(x => !x.is_year);   // a year is never shown as a counter ("2,012")
    if (!(img && img.naturalWidth)) { T.spotlight(c, sc, u, d); return; }   // asset failed to load: vector fallback
    if (kind === 'full_bleed') {
      photoInto(c, img, -12, -12, DW + 24, DH + 24, u, d, ph);
      const g = c.createLinearGradient(0, 470, 0, DH); g.addColorStop(0, 'rgba(36,88,91,0)'); g.addColorStop(0.55, 'rgba(36,88,91,.72)'); g.addColorStop(1, 'rgba(36,88,91,.92)');
      c.fillStyle = g; c.fillRect(-12, 470, DW + 24, DH - 458);
      c.fillStyle = 'rgba(36,88,91,.35)'; c.fillRect(-12, -12, DW + 24, 120);
      const cr = eOutBack(prog(u, 0.35, 0.6), 1.3);
      if (cr > 0) { c.save(); c.translate(0, (1 - cr) * 90); c.globalAlpha = clamp(cr);
        c.fillStyle = 'rgba(0,0,0,.18)'; rr(c, SAFE.x0 + 6, 612, SAFE.x1 - SAFE.x0, 150, 24); c.fill();
        c.fillStyle = C.paper; rr(c, SAFE.x0, 604, SAFE.x1 - SAFE.x0, 150, 24); c.fill();
        c.fillStyle = C.yellow; rr(c, SAFE.x0, 604, 14, 150, [24, 0, 0, 24]); c.fill();
        headline(c, sc.headline, sc.highlight, DW / 2 + 6, 679, SAFE.x1 - SAFE.x0 - 56, u, 0.5, 40); c.restore(); }
      const bs = popS(u, 0.75, 0.4, 2.4); disc(c, SAFE.x0 + 44, SAFE.y0 + 52, 40, C.coral, bs);
      if (bs > 0) { c.save(); c.translate(SAFE.x0 + 44, SAFE.y0 + 52); c.scale(bs, bs); icon(c, sc.main_subject, 0, 0, 52, u); c.restore(); }
    } else if (kind === 'circle') {
      const s = popS(u, 0.1, 0.6, 1.6), R = 172, cx = DW / 2, cy = 360;
      for (let k = 0; k < 10; k++) { c.save(); c.translate(cx, cy); c.rotate(u * 0.18 + k * Math.PI / 5); c.fillStyle = k % 2 ? 'rgba(255,216,107,.28)' : 'rgba(255,255,255,.2)';
        c.beginPath(); c.moveTo(0, 0); c.lineTo(-30, -380); c.lineTo(30, -380); c.closePath(); c.fill(); c.restore(); }
      if (s > 0) { c.save(); c.translate(cx, cy); c.scale(s, s);
        c.fillStyle = 'rgba(0,0,0,.14)'; c.beginPath(); c.arc(6, 10, R + 10, 0, 6.29); c.fill();
        c.fillStyle = C.paper; c.beginPath(); c.arc(0, 0, R + 10, 0, 6.29); c.fill();
        c.save(); c.beginPath(); c.arc(0, 0, R, 0, 6.29); c.clip(); photoInto(c, img, -R, -R, 2 * R, 2 * R, u, d, ph); c.restore();
        const ring = eInOut(prog(u, 0.5, 0.8)); c.strokeStyle = C.yellow; c.lineWidth = 9; c.lineCap = 'round';
        c.beginPath(); c.arc(0, 0, R + 22, -Math.PI / 2, -Math.PI / 2 + ring * 6.283); c.stroke();
        c.setLineDash([6, 12]); c.strokeStyle = 'rgba(47,111,115,.55)'; c.lineWidth = 4; c.rotate(u * 0.3); c.beginPath(); c.arc(0, 0, R + 40, 0, 6.29); c.stroke(); c.setLineDash([]);
        c.restore(); }
      for (let k = 0; k < 4; k++) sparkle(c, cx + Math.cos(k * 1.6 + 0.4) * 230, cy + Math.sin(k * 1.6 + 0.4) * 215, popS(u, 0.8 + k * 0.08, 0.4), u * 2 + k);
      headline(c, sc.headline, sc.highlight, DW / 2, 650, SAFE.x1 - SAFE.x0, u, 0.55, 42);
    } else if (kind === 'split') {
      const p = eOutCubic(prog(u, 0.05, 0.6)), top = SAFE.y0 + 10, h = 330;
      c.save(); c.translate(0, (1 - p) * -60); c.globalAlpha = clamp(p * 1.4);
      c.fillStyle = 'rgba(0,0,0,.14)'; rr(c, SAFE.x0 + 6, top + 10, SAFE.x1 - SAFE.x0, h, 26); c.fill();
      c.save(); rr(c, SAFE.x0, top, SAFE.x1 - SAFE.x0, h, 26); c.clip(); photoInto(c, img, SAFE.x0, top, SAFE.x1 - SAFE.x0, h, u, d, ph); c.restore();
      c.strokeStyle = C.paper; c.lineWidth = 8; rr(c, SAFE.x0, top, SAFE.x1 - SAFE.x0, h, 26); c.stroke(); c.restore();
      const s = popS(u, 0.45, 0.5, 1.8);
      if (s > 0) { c.save(); c.translate(DW / 2, top + h + 120); c.scale(s, s);
        c.fillStyle = 'rgba(0,0,0,.1)'; c.beginPath(); c.ellipse(5, 10, 200, 92, 0, 0, 6.29); c.fill();
        c.fillStyle = C.teal; c.beginPath(); c.ellipse(0, 0, 200, 92, 0, 0, 6.29); c.fill();
        if (n) { const cp = eOutCubic(prog(u, 0.7, Math.min(1.6, d * 0.45)));
          const fs = fitFont(c, fmtNum(n.value, n.text, n.unit), 330, 74, 400, FONT.num, 28);
          txt(c, fmtNum(n.value * cp, n.text, n.unit), 0, 6, { size: fs, weight: 400, fam: FONT.num, color: C.yellow, tag: 'number' });
        } else { icon(c, sc.main_subject, 0, 0, 96, u); }
        c.restore(); }
      headline(c, sc.headline, sc.highlight, DW / 2, top + h + 255, SAFE.x1 - SAFE.x0, u, 0.75, 36);
    } else {
      const p = eOutBack(prog(u, 0.1, 0.7), 1.3), rot = lerp(-0.25, -0.045, clamp(p)), x = lerp(DW + 300, DW / 2, clamp(p));
      c.save(); c.translate(x, 400); c.rotate(rot);
      c.fillStyle = 'rgba(0,0,0,.14)'; rr(c, -200 + 8, -230 + 12, 400, 440, 10); c.fill();
      c.fillStyle = C.paper; rr(c, -200, -230, 400, 440, 10); c.fill();
      c.save(); rr(c, -184, -214, 368, 360, 6); c.clip(); photoInto(c, img, -184, -214, 368, 360, u, d, ph); c.restore();
      c.fillStyle = 'rgba(255,216,107,.85)'; c.save(); c.translate(-150, -232); c.rotate(-0.5); c.fillRect(-36, -12, 72, 24); c.restore(); c.save(); c.translate(150, -232); c.rotate(0.5); c.fillRect(-36, -12, 72, 24); c.restore();
      c.restore();
      headline(c, sc.headline, sc.highlight, DW / 2, 700, SAFE.x1 - SAFE.x0, u, 0.6, 40);
    }
    const credit = ph.credit ? String(ph.credit).slice(0, 52) : '';
    if (credit) { c.save(); c.globalAlpha = clamp(prog(u, 0.6, 0.4)) * 0.92; c.fillStyle = 'rgba(36,42,40,.55)';
      c.font = `500 13px ${FONT.head}`; const w = c.measureText(credit).width + 16; rr(c, SAFE.x0, SAFE.y1 - 22, w, 20, 10); c.fill();
      txt(c, credit, SAFE.x0 + 8, SAFE.y1 - 12, { size: 13, weight: 500, color: '#FFFFFF', align: 'left', tag: 'credit' }); c.restore(); }
  },
  environment(c, sc, u, d) {
    // The place is the picture: a layered illustrated environment (drawn in drawScene) with the
    // subject spotlit at the vanishing point and the idea on a paper card above it.
    const g = 0.55 + 0.12 * Math.sin(u * 2.2), hs = popS(u, 0.35, 0.55, 2.0);
    if (hs > 0) {
      const rg = c.createRadialGradient(DW / 2, 470, 10, DW / 2, 470, 190 * hs); rg.addColorStop(0, `rgba(255,216,107,${g})`); rg.addColorStop(1, 'rgba(255,216,107,0)');
      c.fillStyle = rg; c.fillRect(DW / 2 - 200, 270, 400, 400);
      disc(c, DW / 2, 470, 86, C.paper, hs);
      c.save(); c.translate(DW / 2, 470); c.scale(hs, hs); c.strokeStyle = C.teal; c.lineWidth = 6; c.beginPath(); c.arc(0, 0, 96, 0, 6.29); c.stroke();
      const w = wobble(u, 0.75); c.scale(1 + w, 1 - w); icon(c, sc.main_subject, 0, 0, 118, u); c.restore();
    }
    const cp = eOutBack(prog(u, 0.05, 0.6), 1.3);
    if (cp > 0) {
      c.save(); c.translate(0, (1 - clamp(cp)) * -160); c.globalAlpha = clamp(cp * 1.4);
      c.fillStyle = 'rgba(0,0,0,.14)'; rr(c, SAFE.x0 + 6, 128, SAFE.x1 - SAFE.x0, 166, 26); c.fill();
      c.fillStyle = C.paper; rr(c, SAFE.x0, 120, SAFE.x1 - SAFE.x0, 166, 26); c.fill();
      c.fillStyle = C.coral; rr(c, SAFE.x0, 120, SAFE.x1 - SAFE.x0, 14, [26, 26, 0, 0]); c.fill();
      headline(c, sc.headline, sc.highlight, DW / 2, 208, SAFE.x1 - SAFE.x0 - 40, u, 0.3, 44);
      c.restore();
    }
    if (sc.stage_label) pill(c, DW / 2, 612, sc.stage_label.toUpperCase(), 0.9, u, C.teal, C.paper, 18);
  },
  kinetic(c, sc, u, d) {
    // Typography-led emphasis: the line itself is the visual. Words land one by one on a colour field.
    const field = sc.index % 2 ? C.navy : C.tealD;
    c.fillStyle = field; c.fillRect(-100, -100, DW + 200, DH + 200);
    c.save(); c.globalAlpha = 0.07; c.fillStyle = C.paper;
    for (let k = -6; k < 14; k++) { const x = k * 70 + (u * 26) % 70; c.beginPath(); c.moveTo(x, -100); c.lineTo(x + 34, -100); c.lineTo(x - 266, DH + 100); c.lineTo(x - 300, DH + 100); c.closePath(); c.fill(); }
    c.restore();
    kicker(c, sc.stage_label || PLAN.category, u, 0.05);
    const text = sc.headline || '', hi = String(sc.highlight || '').toLowerCase();
    // every line, including a single long word and its highlight block (+20), must fit the width
    const maxW = (SAFE.x1 - SAFE.x0) * 0.78;
    let L = null;
    for (let sz = 88; sz >= 30 && !L; sz -= 2) {
      const lines = wrap(c, text, maxW, sz, 700, FONT.head, 4);
      if (lines && lines.every(l => { c.font = `700 ${sz}px ${FONT.head}`; return c.measureText(l).width + 20 <= maxW; })) L = { lines, size: sz };
    }
    L = L || layoutLines(c, text, maxW, 30, 700, FONT.head, 6, 22);
    const lh = L.size * 1.08, r = rng(sc.scene_id + 'kt');
    let idx = 0, lastAt = 0.2;
    L.lines.forEach((line, li) => {
      const y = 440 + (li - (L.lines.length - 1) / 2) * lh;
      c.font = `700 ${L.size}px ${FONT.head}`;
      const words = line.split(' '), sp = c.measureText(' ').width, widths = words.map(w => c.measureText(w).width);
      const total = widths.reduce((a, b) => a + b, 0) + sp * (words.length - 1);
      let x = DW / 2 - total / 2;
      words.forEach((w, k) => {
        const t0 = 0.2 + idx * 0.17, e = eOutCubic(prog(u, t0, 0.3)), a = clamp(prog(u, t0, 0.1)), rot = (r() - 0.5) * 0.06;
        lastAt = t0 + 0.3;
        if (a > 0) {
          const isHi = hi && w.replace(/[.,!?;:"']/g, '').toLowerCase() === hi;
          c.save(); c.translate(x + widths[k] / 2, y - (1 - e) * 46); c.rotate(rot * (1 - e) + (isHi ? -0.03 : 0)); c.scale(lerp(1.22, 1, e), lerp(1.22, 1, e)); c.globalAlpha = a;
          if (isHi) { c.fillStyle = C.yellow; rr(c, -widths[k] / 2 - 10, -L.size * 0.62, widths[k] + 20, L.size * 1.2, 14); c.fill(); }
          txt(c, w, 0, 0, { size: L.size, weight: 700, color: isHi ? C.ink : C.paper, tag: 'headline' }); c.restore();
        }
        x += widths[k] + sp; idx++;
      });
    });
    const sw = eInOut(prog(u, lastAt, 0.45)), yb = 440 + (L.lines.length / 2) * lh + 18;
    if (sw > 0) { c.strokeStyle = C.coral; c.lineWidth = 9; c.lineCap = 'round'; c.beginPath(); c.moveTo(DW / 2 - 130, yb); c.quadraticCurveTo(DW / 2, yb + 16, lerp(DW / 2 - 130, DW / 2 + 130, sw), yb - 4); c.stroke(); }
    const bs = popS(u, lastAt + 0.2, 0.45, 2.2);
    disc(c, DW / 2, 700, 44, C.paper, bs); if (bs > 0) { c.save(); c.translate(DW / 2, 700); c.scale(bs, bs); icon(c, sc.main_subject, 0, 0, 56, u); c.restore(); }
  },
  reveal(c, sc, u, d) {
    c.save(); c.translate(DW / 2, 400); c.rotate(u * 0.25); for (let k = 0; k < 12; k++) { c.rotate(Math.PI / 6); c.fillStyle = k % 2 ? 'rgba(255,216,107,.35)' : 'rgba(255,255,255,.25)'; c.beginPath(); c.moveTo(0, 0); c.lineTo(-40, -520); c.lineTo(40, -520); c.closePath(); c.fill(); } c.restore();
    const s = popS(u, 0.15, 0.55, 2.0); disc(c, DW / 2, 400, 135, C.teal, s);
    if (s > 0) { c.save(); c.translate(DW / 2, 400); c.scale(s, s); c.strokeStyle = C.yellow; c.lineWidth = 8; c.beginPath(); c.arc(0, 0, 148, 0, 6.29); c.stroke(); const w = wobble(u, 0.5); c.scale(1 + w, 1 - w); icon(c, sc.main_subject, 0, 0, 150, u); c.restore(); }
    for (let k = 0; k < 6; k++) sparkle(c, DW / 2 + Math.cos(k * 1.05 + u * 0.4) * 200, 400 + Math.sin(k * 1.05 + u * 0.4) * 200, popS(u, 0.6 + k * 0.06, 0.4), u * 2 + k);
    const cr = eOutBack(prog(u, 0.45, 0.6), 1.3);
    if (cr > 0) { c.save(); c.translate(0, (1 - cr) * 120); c.globalAlpha = clamp(cr); c.fillStyle = 'rgba(0,0,0,.1)'; rr(c, SAFE.x0 + 6, 600 + 8, SAFE.x1 - SAFE.x0, 170, 26); c.fill(); c.fillStyle = C.paper; rr(c, SAFE.x0, 600, SAFE.x1 - SAFE.x0, 170, 26); c.fill();
      headline(c, sc.headline, sc.highlight, DW / 2, 685, SAFE.x1 - SAFE.x0 - 40, u, 0.6, 40); c.restore(); }
    const brand = popS(u, Math.max(0.8, d - 1.6), 0.4); if (brand > 0) pill(c, DW / 2, 545, 'CRAZZYDOCS', Math.max(0.8, d - 1.6), u, C.coral, C.paper, 20);
  },
};

// ---------------------------------------------------------------- scene + camera + transitions
const SCENES = PLAN.scenes.map((s, i) => ({ ...s, index: i, stage_label: s.stage === 'HOOK' ? '' : ({ CONTEXT: 'the setup', ESCALATION: 'the twist', REVELATION: 'the reveal', PAYOFF: 'so...' })[s.stage] || '' }));
const DUR = PLAN.duration_sec;
const TR = 0.5;
function drawScene(c, sc, t) {
  const u = t - sc.start_sec, d = sc.end_sec - sc.start_sec;
  c.save();
  const cam = 1.0 + 0.035 * eInOut(prog(u, 0, d)), dx = Math.sin(t * 0.5) * 2, dy = Math.cos(t * 0.4) * 1.5;
  c.translate(DW / 2 + dx, 460 + dy); c.scale(cam, cam); c.translate(-DW / 2, -460);
  if (sc.environment && window.POP_ENV && (sc.template === 'environment' || sc.backdrop === 'environment')) {
    // illustrated place; a slow dolly toward the vanishing point. Diagrams sit on a paper wash over it.
    const dz = sc.template === 'environment' ? lerp(1.0, 1.07, eInOut(prog(u, 0, d))) : 1.0;
    c.save(); c.translate(DW / 2, 470); c.scale(dz, dz); c.translate(-DW / 2, -470); window.POP_ENV.paint(c, sc.environment, t); c.restore();
    if (sc.template !== 'environment') { c.fillStyle = 'rgba(251,248,238,.66)'; c.fillRect(-100, -100, DW + 200, DH + 200); }
  } else background(c, sc, t);
  dust(c, t);
  (T[sc.template] || T.spotlight)(c, sc, u, d);
  c.restore();
}
const LA = document.createElement('canvas'), LB = document.createElement('canvas');
function layerCtx(cv) { if (cv.width !== cvs.width) { cv.width = cvs.width; cv.height = cvs.height; } const k = cv.getContext('2d'); k.setTransform(1, 0, 0, 1, 0, 0); k.clearRect(0, 0, cv.width, cv.height); const S = cvs.width / DW; k.scale(S, S); return k; }
function transition(c, kind, prev, next, t, p) {
  const S = cvs.width / DW, e = eInOut(p);
  const audit = AUDIT; AUDIT = null; const a = layerCtx(LA); drawScene(a, prev, t); AUDIT = audit; const b = layerCtx(LB); drawScene(b, next, t);
  c.save(); c.setTransform(1, 0, 0, 1, 0, 0);
  const W = cvs.width, H = cvs.height;
  if (kind === 'slide') { c.drawImage(LA, -W * e, 0); c.drawImage(LB, W * (1 - e), 0); }
  else if (kind === 'split') { c.drawImage(LB, 0, 0); c.drawImage(LA, 0, 0, W, H / 2, 0, -H / 2 * e, W, H / 2); c.drawImage(LA, 0, H / 2, W, H / 2, 0, H / 2 + H / 2 * e, W, H / 2); }
  else if (kind === 'pop_zoom') { c.drawImage(LA, 0, 0); c.globalAlpha = clamp(p * 1.6); const z = lerp(0.86, 1, eOutBack(p, 1.4)); c.drawImage(LB, W * (1 - z) / 2, H * (1 - z) / 2, W * z, H * z); }
  else if (kind === 'wipe') { c.drawImage(LA, 0, 0); const x = lerp(-0.3, 1.3, e) * W; c.save(); c.beginPath(); c.moveTo(-W, 0); c.lineTo(x + 0.25 * W, 0); c.lineTo(x - 0.25 * W, H); c.lineTo(-W, H); c.clip(); c.drawImage(LB, 0, 0); c.restore();
    c.fillStyle = C.coral; c.beginPath(); c.moveTo(x + 0.25 * W, 0); c.lineTo(x + 0.25 * W + 70 * S, 0); c.lineTo(x - 0.25 * W + 70 * S, H); c.lineTo(x - 0.25 * W, H); c.fill(); }
  else { /* circle_reveal from the subject */ c.drawImage(LA, 0, 0); const r = e * Math.hypot(W, H); c.save(); c.beginPath(); c.arc(W / 2, H * 0.45, Math.max(1, r), 0, 6.29); c.clip(); c.drawImage(LB, 0, 0); c.restore();
    if (p < 0.98) { c.strokeStyle = C.yellow; c.lineWidth = 8 * S; c.beginPath(); c.arc(W / 2, H * 0.45, Math.max(1, r), 0, 6.29); c.stroke(); } }
  c.restore();
}
function iris(c, t) {
  let r = null; const R = 620;
  if (t < 0.7) r = eInOut(prog(t, 0, 0.7)) * R; else if (t > DUR - 0.75) r = (1 - eInOut(prog(t, DUR - 0.75, 0.7))) * R;
  if (r === null) return; r = Math.max(0.01, r);
  c.fillStyle = C.teal; c.beginPath(); c.rect(-20, -20, DW + 40, DH + 40); c.moveTo(DW / 2 + r, 450); c.arc(DW / 2, 450, r, 0, 6.29, true); c.fill('evenodd');
  c.strokeStyle = C.yellow; c.lineWidth = 9; c.beginPath(); c.arc(DW / 2, 450, r, 0, 6.29); c.stroke();
}
function sceneAt(t) { let i = SCENES.findIndex(s => t < s.end_sec); return i < 0 ? SCENES.length - 1 : i; }
function render(t) {
  t = clamp(t, 0, DUR - 1e-6);
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cvs.width, cvs.height);
  const S = cvs.width / DW; ctx.scale(S, S);
  const i = sceneAt(t), sc = SCENES[i];
  const into = t - sc.start_sec;
  if (i > 0 && into < TR) transition(ctx, sc.transition_in, SCENES[i - 1], sc, t, into / TR);
  else drawScene(ctx, sc, t);
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.scale(S, S); iris(ctx, t);
}

// ---------------------------------------------------------------- images, readiness, QC hooks
const IMAGES = {};
async function loadImages() {
  await Promise.all(SCENES.filter(s => s.photo && s.photo.src).map(s => new Promise(res => {
    const im = new Image(); const done = ok => { if (ok) IMAGES[s.scene_id] = im; else window.ASSET_FALLBACKS.push(s.scene_id); res(); };
    const timer = setTimeout(() => done(false), 20000); im.onload = () => { clearTimeout(timer); done(im.naturalWidth > 0); }; im.onerror = () => { clearTimeout(timer); done(false); }; im.src = s.photo.src;
  })));
}
window.ASSET_FALLBACKS = [];
window.DUR = DUR;
window.render = t => render(t);
window.layoutAudit = t => {
  AUDIT = []; render(t); const boxes = AUDIT; AUDIT = null;
  const out = boxes.filter(b => b.x0 < SAFE.x0 - 1 || b.x1 > SAFE.x1 + 1 || b.y0 < SAFE.y0 - 1 || b.y1 > SAFE.y1 + 1);
  const small = boxes.filter(b => b.tag !== 'credit' && b.size < 15);
  const overlaps = [];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) { const a = boxes[i], b = boxes[j];
    if (a.tag === 'headline' && b.tag === 'headline') continue;
    const ix = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0), iy = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
    if (ix > 4 && iy > 4) overlaps.push([a.text, b.text]); }
  return { t, scene: SCENES[sceneAt(t)].scene_id, boxes: boxes.length, outside_safe: out.map(b => ({ text: b.text, box: [b.x0, b.y0, b.x1, b.y1].map(v => Math.round(v)) })), too_small: small.map(b => b.text), overlaps };
};
(async () => {
  try { await Promise.all(['700 40px Fredoka', '600 20px Fredoka', '400 40px "Lilita One"'].map(f => document.fonts.load(f))); await document.fonts.ready; } catch (e) { /* fallback faces */ }
  await loadImages();
  window.FONTS_OK = document.fonts.check('700 40px Fredoka') && document.fonts.check('400 40px "Lilita One"');
  render(0);
  window.READY = true;
})();
})();
