// CrazzyDocs Pop Info illustrated environments (revision 3.28).
// Layered flat-vector places in the Pop Info palette, after the perspective room in
// docs/reference_styles/pop_info.html: a vanishing point, receding ceiling lights, tiled floor,
// props in perspective, ambient motion (glows, steam, blinking lights, drifting clouds).
// No characters or mascots. Every frame is a pure function of t; all randomness is seeded.
// API: window.POP_ENV.paint(c, name, t, seed) draws the full 540 x 960 frame; window.POP_ENV.NAMES.
(() => {
'use strict';
const DW = 540, DH = 960, VX = 270;
const P = {
  ink: '#3B2A26', teal: '#2F6F73', tealD: '#24585B', tealL: '#3E8A8E', coral: '#E85D4A', coralD: '#C24B39',
  yellow: '#FFD86B', cream: '#FBF8EE', paper: '#FFF6EC', sage: '#D7DCCB', sand: '#E9DFC9', sky: '#D5E3E6',
  blush: '#F3DCCF', orange: '#E07A5F', gold: '#F2CC8F', green: '#81B29A', blue: '#6C9BB8', navy: '#3D405B',
};
const clamp = (v, a = 0, b = 1) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, u) => a + (b - a) * u;
const hash = s => { s = String(s); let h = 0x9e3779b1; for (let i = 0; i < s.length; i++) { h = Math.imul(h ^ s.charCodeAt(i), 0x85ebca6b); h ^= h >>> 13; } return h >>> 0; };
const rng = seed => { let a = hash(seed) || 1; return () => { a ^= a << 13; a ^= a >>> 17; a ^= a << 5; return ((a >>> 0) % 100000) / 100000; }; };
const rr = (c, x, y, w, h, r) => { c.beginPath(); c.roundRect(x, y, w, h, r); };
function poly(c, pts, fill) { c.beginPath(); pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y))); c.closePath(); if (fill) { c.fillStyle = fill; c.fill(); } }
function circ(c, x, y, r, fill) { c.fillStyle = fill; c.beginPath(); c.arc(x, y, Math.max(0.1, r), 0, 6.29); c.fill(); }
function glow(c, x, y, r, rgb, a) { const g = c.createRadialGradient(x, y, 1, x, y, r); g.addColorStop(0, `rgba(${rgb},${a})`); g.addColorStop(1, `rgba(${rgb},0)`); c.fillStyle = g; c.fillRect(x - r, y - r, r * 2, r * 2); }

// ------------------------------------------------------------- perspective room
// Back wall [150..390] x [330..560]; walls meet the frame edge at x -60 / 600 (top 60, bottom 900).
const BX0 = 150, BX1 = 390, BY0 = 330, BY1 = 560;
function wallPt(side, d, v) {           // side -1 left / +1 right, d 0 front..1 back, v 0 top..1 bottom
  const x = side < 0 ? lerp(-60, BX0, d) : lerp(600, BX1, d);
  return [x, lerp(lerp(60, BY0, d), lerp(900, BY1, d), v)];
}
function wallQuad(c, side, d0, d1, v0, v1, fill) { poly(c, [wallPt(side, d0, v0), wallPt(side, d1, v0), wallPt(side, d1, v1), wallPt(side, d0, v1)], fill); }
function place(u, d) { const s = lerp(1, 0.36, d); return { x: VX + u * lerp(330, 120, d), y: lerp(900, BY1, d), s }; }
function room(c, t, k) {
  c.fillStyle = k.side; c.fillRect(-60, -60, 660, 1080);
  poly(c, [[-60, -60], [600, -60], [600, 60], [BX1, BY0], [BX0, BY0], [-60, 60]], k.ceil);
  poly(c, [[600, 60], [BX1, BY0], [BX1, BY1], [600, 900]], k.side2 || k.side);
  poly(c, [[-60, 900], [BX0, BY1], [BX1, BY1], [600, 900], [600, 1020], [-60, 1020]], k.floor);
  if (k.floorL) poly(c, [[VX - 40, BY1], [VX + 40, BY1], [VX + 190, 1000], [VX - 190, 1000]], k.floorL);
  c.strokeStyle = k.tile; c.lineWidth = 1;
  for (let i = 1; i < 10; i++) { const y = BY1 + Math.pow(i / 9, 1.8) * 460; const w = lerp(120, 420, (y - BY1) / 340); c.beginPath(); c.moveTo(VX - w, y); c.lineTo(VX + w, y); c.stroke(); }
  for (let i = -7; i <= 7; i++) { c.beginPath(); c.moveTo(VX + i * 17, BY1); c.lineTo(VX + i * 95, 1000); c.stroke(); }
  c.fillStyle = k.back; c.fillRect(BX0, BY0, BX1 - BX0, BY1 - BY0);
  for (const d of [0.08, 0.32, 0.55, 0.74, 0.9]) {        // ceiling lights receding to the back wall
    const y = lerp(10, BY0 - 14, d), w = lerp(200, 40, d), h = lerp(11, 3, d);
    c.fillStyle = k.light || '#FBF8EE'; rr(c, VX - w / 2, y, w, h, h / 2); c.fill();
    c.fillStyle = 'rgba(255,250,235,.22)'; rr(c, VX - w * 0.65, y - h * 0.6, w * 1.3, h * 2.2, h); c.fill();
  }
  // soft light pooling on the floor
  glow(c, VX, 700, 260, '255,250,235', 0.18 + 0.03 * Math.sin(t * 1.3));
}
function box3(c, u, d, w, h, top, front, side) {        // a floor object: front face + top + visible side
  const p = place(u, d), W = w * p.s, H = h * p.s, x = p.x - W / 2, y = p.y - H, dep = 26 * p.s;
  const off = (VX - p.x) * 0.1;          // the top face leans toward the vanishing point
  poly(c, [[x, y], [x + W, y], [x + W + off, y - dep], [x + off, y - dep]], top);
  c.fillStyle = front; c.fillRect(x, y, W, H);
  return { x, y, W, H, s: p.s };
}

// ------------------------------------------------------------- small props
function flask(c, x, y, s, liquid, t, seed) {
  c.save(); c.translate(x, y); c.scale(s, s);
  poly(c, [[-7, -46], [7, -46], [7, -24], [26, 8], [-26, 8], [-7, -24]], 'rgba(255,255,255,.75)');
  poly(c, [[-15, -8], [15, -8], [26, 8], [-26, 8]], liquid);
  c.fillStyle = '#C9D6D3'; c.fillRect(-9, -50, 18, 6);
  const r = rng(seed);
  for (let i = 0; i < 4; i++) { const ph = (t * (0.6 + r() * 0.6) + r()) % 1; circ(c, -10 + r() * 20, 4 - ph * 52, 2 + r() * 2.5, `rgba(255,255,255,${0.75 * (1 - ph)})`); }
  c.restore();
}
function steam(c, x, y, s, t) {
  c.save(); c.strokeStyle = 'rgba(255,255,255,.6)'; c.lineWidth = 3 * s; c.lineCap = 'round';
  for (let k = 0; k < 3; k++) { const ph = (t * 0.5 + k / 3) % 1; c.globalAlpha = 1 - ph; c.beginPath(); const yy = y - ph * 40 * s; c.moveTo(x + (k - 1) * 8 * s, yy); c.quadraticCurveTo(x + (k - 1) * 8 * s + 6 * s * Math.sin(t * 3 + k), yy - 10 * s, x + (k - 1) * 8 * s, yy - 20 * s); c.stroke(); }
  c.restore();
}
function blinkGrid(c, x, y, cols, rows, gap, r, t, seed, colors) {
  const g = rng(seed);
  for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) {
    const ph = g(), col = colors[Math.floor(g() * colors.length)], on = Math.sin(t * (1.5 + ph * 3) + ph * 20) > -0.2;
    circ(c, x + i * gap, y + j * gap, r, on ? col : 'rgba(0,0,0,.18)');
  }
}
function screen(c, x, y, w, h, t, kind, seed) {
  c.fillStyle = P.navy; rr(c, x - 3, y - 3, w + 6, h + 6, 4); c.fill();
  c.fillStyle = '#20324A'; c.fillRect(x, y, w, h);
  glow(c, x + w / 2, y + h / 2, Math.max(w, h) * 0.7, '120,200,210', 0.18);
  c.save(); c.beginPath(); c.rect(x, y, w, h); c.clip();
  const r = rng(seed);
  if (kind === 'bars') { const n = 5; for (let i = 0; i < n; i++) { const hh = h * (0.25 + 0.55 * (0.5 + 0.5 * Math.sin(t * 1.4 + i * 1.7 + r() * 3))); c.fillStyle = [P.yellow, P.coral, P.green][i % 3]; c.fillRect(x + w * (0.1 + i * 0.17), y + h - hh - h * 0.08, w * 0.11, hh); } }
  else if (kind === 'wave') { c.strokeStyle = P.green; c.lineWidth = Math.max(1.2, h * 0.05); c.beginPath(); for (let i = 0; i <= 40; i++) { const xx = x + w * i / 40; const yy = y + h / 2 + Math.sin(i * 0.6 + t * 4) * h * 0.25 * Math.sin(i * 0.15 + t); i ? c.lineTo(xx, yy) : c.moveTo(xx, yy); } c.stroke(); }
  else if (kind === 'ecg') { c.strokeStyle = '#7CE0A0'; c.lineWidth = Math.max(1.2, h * 0.05); c.beginPath(); for (let i = 0; i <= 60; i++) { const ph = ((i / 60) * 2 - t * 0.8) % 1, q = ((ph % 1) + 1) % 1; const spike = q > 0.45 && q < 0.5 ? -0.42 : q > 0.5 && q < 0.54 ? 0.25 : 0; const xx = x + w * i / 60, yy = y + h * (0.55 + spike); i ? c.lineTo(xx, yy) : c.moveTo(xx, yy); } c.stroke(); }
  else if (kind === 'globe') { circ(c, x + w / 2, y + h / 2, h * 0.36, P.blue); c.strokeStyle = 'rgba(255,255,255,.5)'; c.lineWidth = 1; for (let k = -2; k <= 2; k++) { c.beginPath(); c.ellipse(x + w / 2 + Math.sin(t + k) * 0, y + h / 2, Math.abs(Math.cos(t * 0.5 + k * 0.6)) * h * 0.36, h * 0.36, 0, 0, 6.29); c.stroke(); } }
  else { for (let i = 0; i < 4; i++) { c.fillStyle = 'rgba(255,255,255,.45)'; c.fillRect(x + w * 0.1, y + h * (0.2 + i * 0.18), w * (0.4 + 0.4 * r()), Math.max(1.5, h * 0.06)); } }
  c.restore();
}
function sky(c, top, bottom, h = DH) { const g = c.createLinearGradient(0, 0, 0, h); g.addColorStop(0, top); g.addColorStop(1, bottom); c.fillStyle = g; c.fillRect(-60, -60, 660, h + 120); }
function cloud(c, x, y, s, col = 'rgba(255,255,255,.85)') { c.save(); c.translate(x, y); c.scale(s, s); c.fillStyle = col; c.beginPath(); c.arc(0, 0, 24, 0, 6.29); c.arc(26, -10, 30, 0, 6.29); c.arc(56, 0, 22, 0, 6.29); c.rect(0, 0, 56, 20); c.fill(); c.restore(); }
function coins(c, x, y, s, n, t, k) {
  for (let i = 0; i < n; i++) { const yy = y - i * 9 * s; c.fillStyle = '#C9A04A'; c.beginPath(); c.ellipse(x, yy + 3 * s, 22 * s, 8 * s, 0, 0, 6.29); c.fill(); c.fillStyle = P.gold; c.beginPath(); c.ellipse(x, yy, 22 * s, 8 * s, 0, 0, 6.29); c.fill(); }
  const sh = (t * 0.7 + k * 0.37) % 1; if (sh < 0.25) { c.fillStyle = `rgba(255,255,255,${0.9 - sh * 3.4})`; c.beginPath(); c.arc(x + 10 * s, y - n * 9 * s + 2, 4 * s, 0, 6.29); c.fill(); }
}
function gear(c, x, y, r, teeth, rot, col, hub) {
  c.save(); c.translate(x, y); c.rotate(rot); c.fillStyle = col; c.beginPath();
  for (let i = 0; i < teeth * 2; i++) { const a = i * Math.PI / teeth, rad = i % 2 ? r : r * 1.16; c.lineTo(Math.cos(a - 0.12) * rad, Math.sin(a - 0.12) * rad); c.lineTo(Math.cos(a + 0.12) * rad, Math.sin(a + 0.12) * rad); }
  c.closePath(); c.fill(); circ(c, 0, 0, r * 0.45, hub); circ(c, 0, 0, r * 0.16, col); c.restore();
}
function column(c, x, y0, y1, w, base, shade) {
  c.fillStyle = base; c.fillRect(x - w / 2, y0, w, y1 - y0);
  c.fillStyle = shade; for (let k = -2; k <= 2; k++) c.fillRect(x + k * w * 0.18 - w * 0.03, y0 + 14, w * 0.06, y1 - y0 - 28);
  c.fillStyle = base; rr(c, x - w * 0.68, y0 - 16, w * 1.36, 18, 4); c.fill(); rr(c, x - w * 0.68, y1 - 2, w * 1.36, 18, 4); c.fill();
  c.fillStyle = shade; c.fillRect(x - w * 0.68, y0 - 2, w * 1.36, 3); c.fillRect(x - w * 0.68, y1 - 2, w * 1.36, 3);
}
function frame(c, pts, inner, t, seed) {        // gold picture frame (quad) with a small painted landscape
  poly(c, pts, '#C9A04A');
  const [a, b, cc, d] = pts, m = 0.12;
  const ins = [[lerp(a[0], cc[0], m), lerp(a[1], cc[1], m)], [lerp(b[0], d[0], m), lerp(b[1], d[1], m)], [lerp(cc[0], a[0], m), lerp(cc[1], a[1], m)], [lerp(d[0], b[0], m), lerp(d[1], b[1], m)]];
  poly(c, ins, inner[0]);
  c.save(); poly(c, ins); c.clip();
  const ys = Math.min(...ins.map(p => p[1])), ye = Math.max(...ins.map(p => p[1])), xs = Math.min(...ins.map(p => p[0])), xe = Math.max(...ins.map(p => p[0]));
  c.fillStyle = inner[1]; c.fillRect(xs, lerp(ys, ye, 0.62), xe - xs, ye - ys);
  circ(c, lerp(xs, xe, 0.7), lerp(ys, ye, 0.35), (ye - ys) * 0.14, inner[2]);
  c.fillStyle = inner[3]; c.beginPath(); c.moveTo(xs, lerp(ys, ye, 0.7)); c.quadraticCurveTo(lerp(xs, xe, 0.3), lerp(ys, ye, 0.4), lerp(xs, xe, 0.55), lerp(ys, ye, 0.68)); c.lineTo(xs, ye); c.fill();
  c.restore();
}

// ------------------------------------------------------------- environments
const ENV = {
  lab(c, t) {
    room(c, t, { side: '#DCE7E3', side2: '#CFDDD8', ceil: '#EEF3EF', floor: '#B9CBC6', floorL: 'rgba(255,255,255,.18)', back: '#E6EEEA', tile: 'rgba(120,150,145,.35)' });
    // fume hood on the back wall
    c.fillStyle = '#C6D6D2'; c.fillRect(178, 352, 184, 150); c.fillStyle = 'rgba(190,225,235,.75)'; c.fillRect(190, 372, 160, 96);
    glow(c, 270, 420, 120, '175,220,242', 0.35 + 0.08 * Math.sin(t * 2));
    for (let i = 0; i < 4; i++) flask(c, 214 + i * 38, 462, 0.55, [P.coral, P.green, P.yellow, P.blue][i], t, 'hood' + i);
    // wall shelves with bottles in perspective
    for (const side of [-1, 1]) for (const v of [0.3, 0.46]) {
      wallQuad(c, side, 0.25, 0.85, v, v + 0.018, '#A9BDB7');
      for (let k = 0; k < 6; k++) { const d = 0.3 + k * 0.09, [x, y] = wallPt(side, d, v), s = lerp(1, 0.4, d);
        c.fillStyle = [P.coral, P.teal, P.gold, P.green][(k + (side > 0 ? 1 : 0)) % 4]; rr(c, x - 7 * s, y - 34 * s, 14 * s, 34 * s, 3 * s); c.fill(); }
    }
    // benches
    for (const u of [-0.62, 0.62]) { const b = box3(c, u, 0.42, 200, 92, '#F4F7F5', '#3E8A8E'); for (let k = 0; k < 3; k++) flask(c, b.x + b.W * (0.2 + k * 0.3), b.y - 2, 0.62 * b.s, [P.yellow, P.coral, P.green][(k + (u > 0)) % 3], t, 'b' + u + k); }
    // foreground bench edge
    c.fillStyle = '#2F6F73'; c.fillRect(-60, 880, 660, 100); c.fillStyle = '#F4F7F5'; c.fillRect(-60, 862, 660, 22);
    flask(c, 70, 860, 1.25, P.coral, t, 'f1'); flask(c, 470, 860, 1.1, P.green, t, 'f2');
    c.fillStyle = '#C9D6D3'; rr(c, 380, 820, 24, 42, 4); c.fill(); c.fillStyle = P.yellow; c.fillRect(384, 836, 16, 22);
  },
  office(c, t) {
    room(c, t, { side: '#EADFC8', side2: '#DFD2B8', ceil: '#F4EEDF', floor: '#C9B79A', back: '#E9DFC9', tile: 'rgba(150,120,90,.28)' });
    // window with skyline
    c.fillStyle = '#BFD6E0'; c.fillRect(170, 350, 200, 130);
    const r = rng('sky');
    for (let i = 0; i < 9; i++) { const h = 40 + r() * 70, x = 170 + i * 23; c.fillStyle = i % 2 ? '#6C9BB8' : '#557F99'; c.fillRect(x, 480 - h, 21, h);
      for (let j = 0; j < h / 12 - 1; j++) if (Math.sin(t * 0.8 + i * 3 + j * 7) > 0.1) { c.fillStyle = 'rgba(255,230,160,.85)'; c.fillRect(x + 4, 486 - h + j * 12, 4, 5); c.fillRect(x + 12, 486 - h + j * 12, 4, 5); } }
    c.strokeStyle = '#F4EEDF'; c.lineWidth = 6; c.strokeRect(170, 350, 200, 130); c.beginPath(); c.moveTo(270, 350); c.lineTo(270, 480); c.stroke();
    // whiteboard + sticky notes on the right wall, chart poster on the left
    wallQuad(c, 1, 0.3, 0.75, 0.22, 0.48, '#FBFBF7');
    for (let k = 0; k < 5; k++) { const [x, y] = wallPt(1, 0.36 + k * 0.08, 0.27 + (k % 2) * 0.1), s = lerp(1, 0.4, 0.36 + k * 0.08); c.fillStyle = [P.yellow, P.coral, P.green][k % 3]; c.fillRect(x - 12 * s, y, 24 * s, 24 * s); }
    wallQuad(c, -1, 0.35, 0.7, 0.24, 0.46, '#FBF8EE');
    for (let k = 0; k < 4; k++) { const d = 0.4 + k * 0.07, [x, y] = wallPt(-1, d, 0.44), s = lerp(1, 0.4, d), h = (20 + k * 14) * s; c.fillStyle = k === 3 ? P.coral : P.teal; c.fillRect(x - 6 * s, y - h, 12 * s, h); }
    // desks with glowing monitors
    for (const [u, d] of [[-0.6, 0.55], [0.6, 0.55], [-0.55, 0.25], [0.55, 0.25]]) {
      const b = box3(c, u, d, 190, 70, '#B98A5B', '#9C7048');
      screen(c, b.x + b.W * 0.3, b.y - 62 * b.s, 80 * b.s, 52 * b.s, t, ['bars', 'text', 'wave', 'bars'][Math.round((u + 1) * 2 + d * 4) % 4], 'm' + u + d);
      c.fillStyle = P.navy; c.fillRect(b.x + b.W * 0.3 + 34 * b.s, b.y - 10 * b.s, 12 * b.s, 10 * b.s);
    }
    // plant
    const pl = place(0.86, 0.12); c.fillStyle = P.orange; rr(c, pl.x - 22, pl.y - 44, 44, 44, 6); c.fill();
    for (let k = 0; k < 5; k++) { const a = -1.6 + (k - 2) * 0.4 + Math.sin(t * 1.1 + k) * 0.05; c.fillStyle = P.green; c.beginPath(); c.ellipse(pl.x + Math.cos(a) * 34, pl.y - 44 + Math.sin(a) * 40, 9, 24, a + 1.57, 0, 6.29); c.fill(); }
    // foreground desk edge: laptop and steaming mug
    c.fillStyle = '#9C7048'; c.fillRect(-60, 880, 660, 100); c.fillStyle = '#B98A5B'; c.fillRect(-60, 862, 660, 22);
    c.fillStyle = '#5A6070'; rr(c, 60, 790, 150, 74, 6); c.fill(); screen(c, 68, 798, 134, 58, t, 'bars', 'lap');
    c.fillStyle = P.paper; rr(c, 420, 812, 44, 50, 6); c.fill(); c.strokeStyle = P.paper; c.lineWidth = 6; c.beginPath(); c.arc(466, 836, 12, -1.3, 1.3); c.stroke();
    steam(c, 442, 806, 1, t);
  },
  bank(c, t) {
    room(c, t, { side: '#EFE7D6', side2: '#E4D9C2', ceil: '#F6F0E2', floor: '#E2D6BC', back: '#D8CBB0', tile: 'rgba(140,110,70,.3)' });
    // checker floor accents
    for (let i = 0; i < 6; i++) for (let j = -3; j <= 3; j++) if ((i + j) % 2 === 0) { const d0 = 1 - i / 6, d1 = 1 - (i + 1) / 6; const a = place(j / 3.5, d0), b = place((j + 1) / 3.5, d0), cc = place((j + 1) / 3.5, d1), d = place(j / 3.5, d1); poly(c, [[a.x, a.y], [b.x, b.y], [cc.x, cc.y], [d.x, d.y]], 'rgba(160,130,90,.16)'); }
    // vault door on the back wall
    circ(c, 270, 445, 92, '#8E8670'); circ(c, 270, 445, 82, '#B8B19A'); circ(c, 270, 445, 60, '#A39C84');
    for (let k = 0; k < 12; k++) { const a = k * Math.PI / 6; circ(c, 270 + Math.cos(a) * 72, 445 + Math.sin(a) * 72, 4, '#7C7560'); }
    c.save(); c.translate(270, 445); c.rotate(t * 0.35); c.strokeStyle = '#6E6752'; c.lineWidth = 7; c.lineCap = 'round';
    for (let k = 0; k < 3; k++) { c.beginPath(); c.moveTo(0, 0); c.lineTo(Math.cos(k * 2.09) * 44, Math.sin(k * 2.09) * 44); c.stroke(); } circ(c, 0, 0, 12, '#6E6752'); c.restore();
    glow(c, 270, 445, 150, '255,226,150', 0.2 + 0.05 * Math.sin(t * 1.5));
    // columns along the walls and teller windows
    for (const side of [-1, 1]) for (const d of [0.15, 0.5, 0.8]) { const [x, y0] = wallPt(side, d, 0.04), [, y1] = wallPt(side, d, 0.96), s = lerp(1, 0.4, d); column(c, x + side * -18 * s, y0, y1, 34 * s, '#F7F1E3', '#DCD0B4'); }
    for (const side of [-1, 1]) { wallQuad(c, side, 0.27, 0.43, 0.4, 0.62, '#8A6A45'); wallQuad(c, side, 0.29, 0.41, 0.43, 0.6, '#E8DDC6');
      for (let k = 0; k < 4; k++) { const d = 0.3 + k * 0.035, [x, ya] = wallPt(side, d, 0.43), [, yb] = wallPt(side, d, 0.6); c.strokeStyle = '#8A6A45'; c.lineWidth = 2; c.beginPath(); c.moveTo(x, ya); c.lineTo(x, yb); c.stroke(); } }
    // stanchions with a velvet rope
    const pa = place(-0.35, 0.3), pb = place(0.35, 0.3);
    for (const p of [pa, pb]) { c.fillStyle = '#C9A04A'; c.fillRect(p.x - 3, p.y - 70 * p.s, 6, 70 * p.s); circ(c, p.x, p.y - 72 * p.s, 7 * p.s, '#C9A04A'); }
    c.strokeStyle = P.coralD; c.lineWidth = 6; c.beginPath(); c.moveTo(pa.x, pa.y - 66 * pa.s); c.quadraticCurveTo(VX, pa.y - 30 * pa.s + Math.sin(t * 1.2) * 3, pb.x, pb.y - 66 * pb.s); c.stroke();
    // foreground coin stacks and notes
    c.fillStyle = '#8A6A45'; c.fillRect(-60, 884, 660, 100); c.fillStyle = '#A88458'; c.fillRect(-60, 868, 660, 20);
    coins(c, 70, 862, 1.3, 7, t, 1); coins(c, 120, 864, 1.1, 4, t, 2); coins(c, 440, 862, 1.3, 9, t, 3); coins(c, 490, 864, 1.0, 5, t, 4);
    c.fillStyle = P.green; rr(c, 240, 842, 80, 28, 4); c.fill(); circ(c, 280, 856, 8, '#5E9278');
  },
  factory(c, t) {
    room(c, t, { side: '#D9D4C7', side2: '#CFC8B8', ceil: '#C9C2B0', floor: '#A9A28F', back: '#C8C1AE', tile: 'rgba(80,70,50,.25)', light: '#FFF1C9' });
    // roof trusses
    c.strokeStyle = 'rgba(90,80,60,.45)'; c.lineWidth = 3;
    for (const d of [0.15, 0.4, 0.65]) { const [xl, yl] = wallPt(-1, d, 0), [xr] = wallPt(1, d, 0); c.beginPath(); c.moveTo(xl, yl); c.lineTo(xr, yl); for (let k = 0; k <= 6; k++) { const x = lerp(xl, xr, k / 6); c.moveTo(x, yl); c.lineTo(lerp(xl, xr, (k + 0.5) / 6), yl - lerp(36, 12, d)); } c.stroke(); }
    // gears on the back wall
    gear(c, 225, 420, 52, 10, t * 0.6, P.teal, '#C8C1AE'); gear(c, 312, 465, 36, 8, -t * 0.6 * 52 / 36 + 0.2, P.orange, '#C8C1AE'); gear(c, 340, 385, 24, 7, t * 1.2, P.gold, '#C8C1AE');
    // pipes along the walls with steam puffs
    for (const side of [-1, 1]) { wallQuad(c, side, 0.0, 0.95, 0.12, 0.15, '#8E8670'); wallQuad(c, side, 0.0, 0.95, 0.19, 0.21, '#A39C84');
      const [x, y] = wallPt(side, 0.55, 0.12); steam(c, x, y, 0.9, t + side); }
    // conveyor belt across the floor with moving boxes
    const L = place(-1.1, 0.5), R = place(1.1, 0.5);
    c.fillStyle = '#5A5548'; c.fillRect(L.x, L.y - 40, R.x - L.x, 16); c.fillStyle = '#3F3B32'; c.fillRect(L.x, L.y - 24, R.x - L.x, 10);
    for (let k = 0; k < 6; k++) { const x = L.x + (((t * 40 + k * (R.x - L.x) / 6) % (R.x - L.x)) + (R.x - L.x)) % (R.x - L.x); c.fillStyle = '#C49A6C'; c.fillRect(x - 16, L.y - 72, 32, 32); c.fillStyle = '#A97F55'; c.fillRect(x - 16, L.y - 60, 32, 4); }
    for (let k = 0; k < 9; k++) circ(c, L.x + (R.x - L.x) * k / 8, L.y - 19, 5, '#8E8670');
    // hazard stripe floor line
    c.save(); poly(c, [[-60, 830], [600, 830], [600, 852], [-60, 852]]); c.clip(); c.fillStyle = P.yellow; c.fillRect(-60, 830, 660, 22);
    c.fillStyle = P.ink; for (let x = -80; x < 640; x += 36) poly(c, [[x, 852], [x + 18, 852], [x + 40, 830], [x + 22, 830]], P.ink); c.restore();
    // foreground crates
    for (const [x, w] of [[30, 120], [430, 110]]) { c.fillStyle = '#C49A6C'; c.fillRect(x, 860, w, 110); c.strokeStyle = '#8A6A45'; c.lineWidth = 6; c.strokeRect(x + 3, 863, w - 6, 104); c.beginPath(); c.moveTo(x, 860); c.lineTo(x + w, 970); c.stroke(); }
  },
  research_facility(c, t) {
    room(c, t, { side: '#D3DCE3', side2: '#C7D2DB', ceil: '#E3EAF0', floor: '#AEBBC6', back: '#C6D2DB', tile: 'rgba(70,90,110,.25)' });
    // detector ring on the back wall
    circ(c, 270, 445, 100, '#3D405B'); circ(c, 270, 445, 84, '#55597A');
    for (let k = 0; k < 16; k++) { const a = k * Math.PI / 8 + t * 0.15; c.fillStyle = k % 2 ? P.tealL : P.teal; c.beginPath(); c.moveTo(270 + Math.cos(a) * 84, 445 + Math.sin(a) * 84); c.arc(270, 445, 84, a, a + 0.3); c.lineTo(270 + Math.cos(a + 0.3) * 58, 445 + Math.sin(a + 0.3) * 58); c.arc(270, 445, 58, a + 0.3, a, true); c.fill(); }
    circ(c, 270, 445, 50, '#1F2340'); glow(c, 270, 445, 90, '120,220,230', 0.5 + 0.25 * Math.sin(t * 3));
    c.save(); c.translate(270, 445); c.rotate(t * 1.6); c.strokeStyle = 'rgba(255,216,107,.8)'; c.lineWidth = 2; for (let k = 0; k < 3; k++) { c.beginPath(); c.ellipse(0, 0, 40, 12, k * 1.05, 0, 6.29); c.stroke(); } c.restore();
    // control panels with blinking lights and screens on the walls
    for (const side of [-1, 1]) { wallQuad(c, side, 0.3, 0.8, 0.3, 0.62, '#55597A');
      for (let k = 0; k < 3; k++) { const d = 0.36 + k * 0.14, [x, y] = wallPt(side, d, 0.36), s = lerp(1, 0.4, d); screen(c, x - 26 * s, y, 52 * s, 34 * s, t, ['wave', 'bars', 'text'][(k + (side > 0)) % 3], 'rf' + side + k);
        blinkGrid(c, x - 20 * s, y + 52 * s, 4, 2, 13 * s, 3 * s, t, 'bl' + side + k, [P.green, P.yellow, P.coral]); } }
    // cable trays on the floor
    c.strokeStyle = '#3D405B'; c.lineWidth = 5; for (const u of [-0.2, 0.2]) { const a = place(u, 0.95), b = place(u * 2.6, 0); c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke(); }
    // foreground console
    c.fillStyle = '#3D405B'; poly(c, [[-60, 860], [600, 860], [600, 980], [-60, 980]], '#3D405B'); poly(c, [[-60, 860], [600, 860], [560, 830], [-20, 830]], '#55597A');
    blinkGrid(c, 30, 845, 10, 1, 16, 4, t, 'fc', [P.green, P.yellow, P.coral, P.blue]); screen(c, 330, 868, 150, 70, t, 'wave', 'fcs');
  },
  newsroom(c, t) {
    room(c, t, { side: '#4A4E6D', side2: '#43476A', ceil: '#55597A', floor: '#2F3249', back: '#3D405B', tile: 'rgba(255,255,255,.08)', light: '#DDE4F0' });
    // video wall
    const kinds = ['globe', 'bars', 'wave', 'text', 'bars', 'globe'];
    for (let i = 0; i < 3; i++) for (let j = 0; j < 2; j++) screen(c, 166 + i * 70, 352 + j * 64, 64, 56, t + i, kinds[i + j * 3], 'vw' + i + j);
    // on-air lamp
    const on = Math.sin(t * 2.4) > -0.4; c.fillStyle = on ? P.coral : '#7A3A32'; rr(c, 236, 336, 68, 14, 7); c.fill(); if (on) glow(c, 270, 343, 60, '232,93,74', 0.45);
    // ticker bands on both walls
    for (const side of [-1, 1]) { wallQuad(c, side, 0.0, 0.95, 0.56, 0.62, P.coral);
      for (let k = 0; k < 12; k++) { const d = ((k / 12 + t * 0.06 * side) % 1 + 1) % 1, [x, y] = wallPt(side, d * 0.95, 0.585), s = lerp(1, 0.4, d); c.fillStyle = P.paper; c.fillRect(x - 10 * s, y - 2 * s, 20 * s, 4 * s); } }
    // spotlights
    glow(c, 140, 640, 200, '255,240,200', 0.14); glow(c, 400, 640, 200, '255,240,200', 0.14);
    // curved anchor desk with microphone
    const p = place(0, 0.35); c.fillStyle = P.coral; c.beginPath(); c.ellipse(p.x, p.y - 30, 170 * p.s * 1.4, 34, 0, Math.PI, 0); c.lineTo(p.x + 238 * p.s, p.y + 20); c.lineTo(p.x - 238 * p.s, p.y + 20); c.fill();
    c.fillStyle = P.coralD; c.fillRect(p.x - 238 * p.s, p.y - 4, 476 * p.s, 24); c.fillStyle = P.paper; c.fillRect(p.x - 238 * p.s, p.y - 30, 476 * p.s, 6);
    c.fillStyle = '#20324A'; c.fillRect(p.x + 60, p.y - 74, 5, 44); circ(c, p.x + 62, p.y - 78, 8, '#20324A');
    // foreground newspapers
    for (let k = 0; k < 3; k++) { c.save(); c.translate(90 + k * 26, 900 - k * 8); c.rotate(-0.12 + k * 0.07); c.fillStyle = P.paper; c.fillRect(-70, -40, 140, 90); c.fillStyle = P.navy; c.fillRect(-60, -30, 90, 8);
      c.fillStyle = 'rgba(59,42,38,.3)'; for (let l = 0; l < 4; l++) c.fillRect(-60, -14 + l * 12, 120, 4); c.restore(); }
    c.fillStyle = '#20324A'; rr(c, 380, 850, 120, 90, 8); c.fill(); screen(c, 390, 860, 100, 60, t, 'bars', 'np');
  },
  hospital(c, t) {
    room(c, t, { side: '#E3EEF0', side2: '#D7E6E9', ceil: '#F1F7F8', floor: '#C3D6DA', floorL: 'rgba(255,255,255,.22)', back: '#D8E8EC', tile: 'rgba(90,130,140,.25)' });
    // window with blinds and a cross sign
    c.fillStyle = '#BFD6E0'; c.fillRect(186, 356, 120, 110); for (let k = 0; k < 9; k++) { c.fillStyle = 'rgba(255,255,255,.7)'; c.fillRect(186, 360 + k * 12, 120, 5); }
    c.fillStyle = P.coral; c.fillRect(330, 370, 34, 12); c.fillRect(341, 359, 12, 34);
    // bed in perspective
    const b = box3(c, -0.15, 0.45, 230, 50, '#FBFBF7', '#9FC1C8');
    c.fillStyle = P.blue; c.fillRect(b.x + b.W * 0.35, b.y - 10 * b.s, b.W * 0.65, 14 * b.s); c.fillStyle = '#FBFBF7'; rr(c, b.x + 6, b.y - 24 * b.s, 50 * b.s, 22 * b.s, 8); c.fill();
    c.fillStyle = '#8E9FA5'; c.fillRect(b.x, b.y + b.H, 6, 30 * b.s); c.fillRect(b.x + b.W - 6, b.y + b.H, 6, 30 * b.s);
    // IV stand and monitor with ECG
    const iv = place(0.55, 0.5); c.fillStyle = '#8E9FA5'; c.fillRect(iv.x - 2, iv.y - 170 * iv.s, 4, 170 * iv.s); c.fillStyle = 'rgba(200,230,240,.85)'; rr(c, iv.x - 12 * iv.s, iv.y - 170 * iv.s, 24 * iv.s, 36 * iv.s, 6); c.fill();
    screen(c, iv.x + 14, iv.y - 150 * iv.s, 84 * iv.s, 56 * iv.s, t, 'ecg', 'hm');
    // curtain rail on the right wall
    for (let k = 0; k < 8; k++) { const d = 0.2 + k * 0.07, [x, y0] = wallPt(1, d, 0.08), [, y1] = wallPt(1, d, 0.7), s = lerp(1, 0.4, d); c.fillStyle = k % 2 ? '#9FC1C8' : '#B5D2D8'; c.fillRect(x - 12 * s + Math.sin(t + k) * 2, y0, 24 * s, y1 - y0); }
    // foreground: clipboard and stethoscope
    c.fillStyle = '#C3D6DA'; c.fillRect(-60, 880, 660, 100);
    c.save(); c.translate(130, 870); c.rotate(-0.08); c.fillStyle = '#B98A5B'; rr(c, -60, -70, 120, 150, 8); c.fill(); c.fillStyle = P.paper; c.fillRect(-48, -54, 96, 124);
    c.fillStyle = 'rgba(59,42,38,.3)'; for (let l = 0; l < 6; l++) c.fillRect(-40, -38 + l * 16, 70 - (l % 3) * 12, 4); c.fillStyle = '#8E9FA5'; rr(c, -18, -78, 36, 16, 4); c.fill(); c.restore();
    c.strokeStyle = P.navy; c.lineWidth = 7; c.lineCap = 'round'; c.beginPath(); c.moveTo(360, 840); c.bezierCurveTo(330, 940, 480, 950, 470, 860); c.stroke(); circ(c, 470, 852, 14, '#8E9FA5');
  },
  classroom(c, t) {
    room(c, t, { side: '#EADBC2', side2: '#E0CFB2', ceil: '#F3EADB', floor: '#C4A27A', back: '#E2D2B6', tile: 'rgba(120,80,40,.25)' });
    // chalkboard that draws a little diagram over time
    c.fillStyle = '#8A6A45'; c.fillRect(162, 372, 216, 120); c.fillStyle = '#3E6B5A'; c.fillRect(168, 378, 204, 108);
    const pr = (t * 0.25) % 1; c.strokeStyle = 'rgba(255,255,255,.85)'; c.lineWidth = 2.5; c.lineCap = 'round';
    c.beginPath(); c.arc(215, 430, 22, 0, 6.29 * clamp(pr * 3)); c.stroke();
    if (pr > 0.33) { c.beginPath(); c.moveTo(242, 430); c.lineTo(lerp(242, 300, clamp((pr - 0.33) * 3)), 430); c.stroke(); }
    if (pr > 0.66) { c.strokeRect(306, 410, 44 * clamp((pr - 0.66) * 3), 40); }
    // clock with moving hands
    circ(c, 270, 352, 15, P.paper); c.strokeStyle = P.ink; c.lineWidth = 2; c.beginPath(); c.moveTo(270, 352); c.lineTo(270 + Math.cos(t * 0.5 - 1.57) * 11, 352 + Math.sin(t * 0.5 - 1.57) * 11); c.moveTo(270, 352); c.lineTo(276, 348); c.stroke();
    // windows on the left wall, posters on the right
    for (const d of [0.2, 0.5]) { wallQuad(c, -1, d, d + 0.2, 0.18, 0.5, '#F3EADB'); wallQuad(c, -1, d + 0.015, d + 0.185, 0.21, 0.47, '#BFD6E0'); }
    for (let k = 0; k < 3; k++) wallQuad(c, 1, 0.25 + k * 0.2, 0.38 + k * 0.2, 0.22, 0.42, [P.coral, P.gold, P.green][k]);
    // rows of desks
    for (const d of [0.7, 0.5, 0.3]) for (const u of [-0.55, 0, 0.55]) { const b = box3(c, u, d, 92, 48, '#D9B98E', '#B98A5B'); c.fillStyle = '#8A6A45'; c.fillRect(b.x + 4, b.y + b.H, 4, 22 * b.s); c.fillRect(b.x + b.W - 8, b.y + b.H, 4, 22 * b.s); }
    // foreground desk: books, apple, globe
    c.fillStyle = '#9C7048'; c.fillRect(-60, 884, 660, 100); c.fillStyle = '#B98A5B'; c.fillRect(-60, 866, 660, 22);
    [[P.teal, 0], [P.coral, 1], [P.gold, 2]].forEach(([col, k]) => { c.fillStyle = col; rr(c, 40 + k * 4, 840 - k * 22, 130 - k * 10, 24, 3); c.fill(); });
    circ(c, 230, 846, 18, P.coral); c.fillStyle = P.green; c.beginPath(); c.ellipse(236, 824, 6, 10, 0.6, 0, 6.29); c.fill();
    circ(c, 450, 790, 54, P.blue); c.save(); c.beginPath(); c.arc(450, 790, 54, 0, 6.29); c.clip(); c.fillStyle = P.green;
    for (let k = 0; k < 3; k++) { const x = 450 + ((((t * 14 + k * 50) % 160) + 160) % 160) - 80; c.beginPath(); c.ellipse(x, 774 + k * 18, 22, 12, 0.3, 0, 6.29); c.fill(); } c.restore();
    c.fillStyle = '#8A6A45'; c.fillRect(446, 844, 8, 22); c.fillRect(420, 862, 60, 8);
  },
  city_street(c, t) {
    sky(c, '#BFD9E2', '#F4EEDF', 560); circ(c, 400, 200, 46, 'rgba(255,216,107,.85)'); glow(c, 400, 200, 140, '255,216,107', 0.3);
    for (let k = 0; k < 3; k++) cloud(c, ((k * 210 + t * 8) % 760) - 120, 120 + k * 60, 0.8 + k * 0.2);
    // far skyline
    const r = rng('city'); for (let i = 0; i < 14; i++) { const h = 60 + r() * 120; c.fillStyle = 'rgba(85,127,153,.55)'; c.fillRect(110 + i * 23, 470 - h, 22, h); }
    // ground and road
    c.fillStyle = '#C8C1AE'; c.fillRect(-60, 470, 660, 560);
    poly(c, [[245, 470], [295, 470], [600, 1000], [-60, 1000]], '#5A5E70');
    for (let k = 0; k < 9; k++) { const ph = ((k / 9 + t * 0.12) % 1), d = 1 - ph, y0 = lerp(990, 470, d), y1 = lerp(990, 470, Math.min(1, d + 0.04)); const w0 = lerp(10, 1.4, d), w1 = lerp(10, 1.4, Math.min(1, d + 0.04)); poly(c, [[VX - w0, y0], [VX + w0, y0], [VX + w1, y1], [VX - w1, y1]], P.yellow); }
    // building facades on both sides
    for (const side of [-1, 1]) {
      const fx = side < 0 ? [-60, 190] : [600, 350];
      poly(c, [[fx[0], 0], [fx[1], 260], [fx[1], 480], [fx[0], 1000]], side < 0 ? '#E07A5F' : '#6C9BB8');
      for (let row = 0; row < 9; row++) for (let col = 0; col < 4; col++) {
        const d0 = col / 4 + 0.04, d1 = (col + 1) / 4 - 0.06, v0 = 0.08 + row * 0.1, v1 = v0 + 0.06;
        const pt = (d, v) => [lerp(fx[0], fx[1], d), lerp(lerp(0, 260, d), lerp(1000, 480, d), v)];
        const lit = Math.sin(t * 0.6 + row * 3.1 + col * 5.7 + side) > 0.3;
        poly(c, [pt(d0, v0), pt(d1, v0), pt(d1, v1), pt(d0, v1)], lit ? 'rgba(255,230,160,.95)' : 'rgba(255,255,255,.35)');
      }
    }
    // street lamps receding
    for (const side of [-1, 1]) for (const d of [0.2, 0.5, 0.75]) { const p = { x: lerp(side < 0 ? 40 : 500, side < 0 ? 238 : 302, d), y: lerp(980, 474, d), s: lerp(1, 0.2, d) };
      c.fillStyle = P.navy; c.fillRect(p.x - 3 * p.s, p.y - 210 * p.s, 6 * p.s, 210 * p.s); circ(c, p.x, p.y - 214 * p.s, 10 * p.s, P.yellow); glow(c, p.x, p.y - 214 * p.s, 40 * p.s, '255,216,107', 0.35); }
    // traffic light
    c.fillStyle = P.navy; c.fillRect(480, 560, 8, 280); rr(c, 466, 520, 36, 92, 8); c.fill();
    const ph = Math.floor(t / 2.5) % 3; [[P.coral, 0], [P.yellow, 1], [P.green, 2]].forEach(([col, k]) => circ(c, 484, 540 + k * 28, 10, ph === k ? col : 'rgba(0,0,0,.35)'));
  },
  space(c, t) {
    sky(c, '#1F2340', '#3D405B');
    const r = rng('stars'); for (let i = 0; i < 120; i++) { const x = r() * DW, y = r() * DH, s = 0.6 + r() * 1.8, ph = r() * 6.28; circ(c, x, y, s, `rgba(255,253,240,${0.35 + 0.55 * (0.5 + 0.5 * Math.sin(t * (1 + r() * 2) + ph))})`); }
    // galaxy swirl
    c.save(); c.translate(150, 260); c.rotate(t * 0.05); const g = rng('gal');
    for (let i = 0; i < 160; i++) { const a = i * 0.21, rad = 4 + i * 0.55; circ(c, Math.cos(a) * rad * 1.5 + (g() - 0.5) * 8, Math.sin(a) * rad * 0.7 + (g() - 0.5) * 6, 1.2 + g(), i % 3 ? 'rgba(200,180,255,.55)' : 'rgba(255,216,107,.6)'); }
    c.restore(); glow(c, 150, 260, 70, '220,200,255', 0.35);
    // ringed planet
    c.save(); c.translate(400, 330); c.rotate(-0.35); c.strokeStyle = 'rgba(242,204,143,.85)'; c.lineWidth = 9; c.beginPath(); c.ellipse(0, 0, 120, 28, 0, Math.PI, 2 * Math.PI); c.stroke();
    circ(c, 0, 0, 70, P.orange); c.fillStyle = 'rgba(194,75,57,.5)'; c.fillRect(-70, -12, 140, 12); c.fillRect(-66, 22, 132, 8);
    c.beginPath(); c.ellipse(0, 0, 120, 28, 0, 0, Math.PI); c.stroke(); c.restore();
    circ(c, 90, 520, 22, '#B8B19A'); circ(c, 84, 514, 5, '#A39C84');
    // satellite drifting
    const sx = 330 + Math.sin(t * 0.3) * 60, sy = 560 + Math.cos(t * 0.25) * 20; c.save(); c.translate(sx, sy); c.rotate(Math.sin(t * 0.4) * 0.3);
    c.fillStyle = P.blue; c.fillRect(-48, -8, 32, 16); c.fillRect(16, -8, 32, 16); c.fillStyle = P.gold; rr(c, -14, -12, 28, 24, 4); c.fill(); c.strokeStyle = P.paper; c.lineWidth = 2; c.beginPath(); c.moveTo(0, -12); c.lineTo(0, -26); c.stroke(); circ(c, 0, -28, 3, P.coral); c.restore();
    // foreground moon surface
    c.fillStyle = '#A39C84'; c.beginPath(); c.moveTo(-60, 860); c.quadraticCurveTo(270, 760, 600, 860); c.lineTo(600, 1000); c.lineTo(-60, 1000); c.fill();
    for (const [x, y, rx] of [[90, 860, 40], [300, 830, 26], [460, 880, 50]]) { c.fillStyle = '#8E8670'; c.beginPath(); c.ellipse(x, y, rx, rx * 0.3, 0, 0, 6.29); c.fill(); }
  },
  nature(c, t) {
    sky(c, '#BFD9E2', '#F4EEDF', 600);
    c.save(); c.translate(380, 210); c.rotate(t * 0.1); c.fillStyle = 'rgba(255,216,107,.35)'; for (let k = 0; k < 12; k++) { c.rotate(Math.PI / 6); c.fillRect(-4, 60, 8, 50); } c.restore(); circ(c, 380, 210, 50, P.yellow);
    for (let k = 0; k < 3; k++) cloud(c, ((k * 230 + t * 10) % 780) - 140, 110 + k * 70, 0.9);
    // mountains
    poly(c, [[-60, 520], [90, 330], [210, 470], [320, 300], [470, 460], [600, 380], [600, 600], [-60, 600]], '#8FA9B5');
    poly(c, [[90, 330], [118, 368], [62, 368]], P.paper); poly(c, [[320, 300], [352, 342], [290, 342]], P.paper);
    // hills
    c.fillStyle = '#9CC1A3'; c.beginPath(); c.moveTo(-60, 600); c.quadraticCurveTo(150, 480, 330, 570); c.quadraticCurveTo(470, 520, 600, 560); c.lineTo(600, 1000); c.lineTo(-60, 1000); c.fill();
    c.fillStyle = P.green; c.beginPath(); c.moveTo(-60, 700); c.quadraticCurveTo(250, 600, 600, 690); c.lineTo(600, 1000); c.lineTo(-60, 1000); c.fill();
    // river winding toward the viewer
    c.fillStyle = '#7FB3C9'; c.beginPath(); c.moveTo(262, 590); c.bezierCurveTo(300, 680, 170, 760, 230, 1000); c.lineTo(420, 1000); c.bezierCurveTo(320, 760, 330, 680, 278, 590); c.fill();
    for (let k = 0; k < 6; k++) { const ph = (t * 0.2 + k / 6) % 1, y = lerp(600, 980, ph); c.fillStyle = `rgba(255,255,255,${0.6 * (1 - ph)})`; c.fillRect(lerp(268, 300, ph) + Math.sin(k) * 20, y, lerp(6, 40, ph), 3); }
    // trees swaying
    for (const [x, y, s] of [[60, 690, 1.1], [140, 650, 0.8], [430, 660, 0.9], [500, 700, 1.2], [360, 620, 0.6]]) { const sw = Math.sin(t * 1.2 + x) * 0.04;
      c.save(); c.translate(x, y); c.rotate(sw); c.fillStyle = '#8A6A45'; c.fillRect(-6 * s, -10 * s, 12 * s, 70 * s); circ(c, 0, -40 * s, 44 * s, '#5E9278'); circ(c, -20 * s, -20 * s, 30 * s, '#6FA388'); circ(c, 22 * s, -26 * s, 30 * s, '#6FA388'); c.restore(); }
    // birds
    c.strokeStyle = P.navy; c.lineWidth = 2; for (let k = 0; k < 4; k++) { const x = ((t * 30 + k * 60) % 700) - 80, y = 260 + k * 18 + Math.sin(t * 2 + k) * 6, f = Math.sin(t * 8 + k) * 5; c.beginPath(); c.moveTo(x - 9, y - f); c.lineTo(x, y); c.lineTo(x + 9, y - f); c.stroke(); }
    // foreground grass and flowers
    for (let k = 0; k < 60; k++) { const x = -40 + k * 11, h = 30 + (k * 37 % 30), sw = Math.sin(t * 1.6 + k * 0.4) * 6; c.strokeStyle = k % 3 ? '#5E9278' : '#4C7F66'; c.lineWidth = 4; c.beginPath(); c.moveTo(x, 990); c.quadraticCurveTo(x + sw * 0.5, 990 - h * 0.6, x + sw, 990 - h - 40); c.stroke(); }
    for (let k = 0; k < 6; k++) { const x = 40 + k * 90, y = 930 - (k % 2) * 20; circ(c, x + Math.sin(t * 1.6 + k) * 4, y, 9, [P.coral, P.yellow, P.paper][k % 3]); circ(c, x + Math.sin(t * 1.6 + k) * 4, y, 3.5, P.orange); }
  },
  historical(c, t) {
    room(c, t, { side: '#9B4A3A', side2: '#8E4434', ceil: '#E9DFC9', floor: '#B98A5B', back: '#8A3F33', tile: 'rgba(80,50,30,.35)', light: '#FFF1C9' });
    // skylight beams
    c.save(); c.globalAlpha = 0.16 + 0.04 * Math.sin(t * 0.8); c.fillStyle = '#FFF1C9'; poly(c, [[200, 40], [340, 40], [420, 760], [120, 760]], '#FFF1C9'); c.restore();
    // paintings: one large on the back wall, more along the side walls
    frame(c, [[180, 364], [360, 364], [360, 486], [180, 486]], ['#BFD6E0', '#6C9BB8', P.yellow, '#557F99'], t, 'p0');
    for (const side of [-1, 1]) for (const [d0, d1] of [[0.15, 0.35], [0.5, 0.66]]) {
      const pts = [wallPt(side, d0, 0.22), wallPt(side, d1, 0.22), wallPt(side, d1, 0.46), wallPt(side, d0, 0.46)];
      frame(c, pts, side < 0 ? ['#F2CC8F', P.orange, P.paper, '#C24B39'] : ['#D5E3E6', P.green, P.gold, '#5E9278'], t, 'p' + side + d0);
      const [lx, ly] = wallPt(side, (d0 + d1) / 2, 0.2); glow(c, lx, ly + 30, 60 * lerp(1, 0.4, d0), '255,240,200', 0.3);
    }
    // columns framing the view
    column(c, 30, 60, 960, 70, '#F1E8D6', '#D9CBB0'); column(c, 510, 60, 960, 70, '#F1E8D6', '#D9CBB0');
    // pedestal with an amphora
    const p = place(0.0, 0.42); c.fillStyle = '#F1E8D6'; c.fillRect(p.x - 34 * p.s * 1.4, p.y - 110 * p.s, 68 * p.s * 1.4, 110 * p.s);
    c.save(); c.translate(p.x, p.y - 110 * p.s); c.scale(p.s * 1.4, p.s * 1.4); c.fillStyle = P.orange; c.beginPath(); c.moveTo(-10, -86); c.quadraticCurveTo(-34, -60, -24, -20); c.quadraticCurveTo(-16, 0, 0, 0); c.quadraticCurveTo(16, 0, 24, -20); c.quadraticCurveTo(34, -60, 10, -86); c.fill();
    c.fillStyle = P.ink; c.fillRect(-26, -50, 52, 6); c.fillRect(-12, -92, 24, 8); c.restore();
    // velvet rope
    c.strokeStyle = P.coralD; c.lineWidth = 6; c.beginPath(); c.moveTo(110, 780); c.quadraticCurveTo(270, 830 + Math.sin(t) * 4, 430, 780); c.stroke();
    for (const x of [110, 430]) { c.fillStyle = '#C9A04A'; c.fillRect(x - 4, 780, 8, 120); circ(c, x, 778, 10, '#C9A04A'); }
  },
  digital_network(c, t) {
    sky(c, '#14363A', '#24585B');
    // perspective grid floor moving toward the viewer
    c.strokeStyle = 'rgba(62,138,142,.6)'; c.lineWidth = 1.2;
    for (let i = -10; i <= 10; i++) { c.beginPath(); c.moveTo(VX + i * 8, 520); c.lineTo(VX + i * 90, 1000); c.stroke(); }
    for (let k = 0; k < 12; k++) { const ph = ((k / 12 + t * 0.08) % 1), y = 520 + Math.pow(ph, 2) * 480; c.globalAlpha = ph; c.beginPath(); c.moveTo(-60, y); c.lineTo(600, y); c.stroke(); } c.globalAlpha = 1;
    glow(c, VX, 520, 300, '62,138,142', 0.35);
    // nodes and edges with travelling packets
    const r = rng('net'), N = []; for (let i = 0; i < 16; i++) N.push({ x: 40 + r() * 460, y: 90 + r() * 400, s: 5 + r() * 7 });
    const E = []; for (let i = 0; i < N.length; i++) { let best = -1, bd = 1e9; for (let j = 0; j < N.length; j++) if (j !== i) { const d = Math.hypot(N[i].x - N[j].x, N[i].y - N[j].y); if (d < bd && !E.some(e => (e[0] === j && e[1] === i))) { bd = d; best = j; } } E.push([i, best]); if (i % 3 === 0) E.push([i, (i + 5) % N.length]); }
    c.strokeStyle = 'rgba(213,227,230,.35)'; c.lineWidth = 1.5; for (const [a, b] of E) { c.beginPath(); c.moveTo(N[a].x, N[a].y); c.lineTo(N[b].x, N[b].y); c.stroke(); }
    E.forEach(([a, b], k) => { const ph = (t * 0.35 + k * 0.137) % 1; circ(c, lerp(N[a].x, N[b].x, ph), lerp(N[a].y, N[b].y, ph), 3, P.yellow); });
    N.forEach((n, k) => { const pulse = 1 + 0.15 * Math.sin(t * 2 + k); glow(c, n.x, n.y, n.s * 4, '129,178,154', 0.35); circ(c, n.x, n.y, n.s * pulse, k % 4 ? P.green : P.coral); circ(c, n.x, n.y, n.s * 0.4, '#14363A'); });
    // floating data panels
    for (const [x, y, w, k] of [[40, 640, 150, 'bars'], [350, 600, 150, 'wave']]) { const yy = y + Math.sin(t * 0.8 + x) * 6; c.fillStyle = 'rgba(213,227,230,.12)'; rr(c, x - 6, yy - 6, w + 12, 92, 10); c.fill(); screen(c, x, yy, w, 80, t, k, 'dp' + x); }
  },
};
const NAMES = Object.keys(ENV);
window.POP_ENV = {
  NAMES,
  paint(c, name, t) { c.save(); (ENV[name] || ENV.lab)(c, t); c.restore(); },
};
})();
