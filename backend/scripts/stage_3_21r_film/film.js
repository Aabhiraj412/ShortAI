const WIDTH = 1080;
const HEIGHT = 1920;
const ACCENT = '#ff714d';
const CYAN = '#8edcf0';
const WHITE = '#f4f3ef';
const MUTED = '#b7c1ca';
const SHOTS = [
  ['hook', 'WHAT WOULD YOU SEE?', 'EHT / ESO'],
  ['ring', 'THE SHADOW OF M87*', 'RADIO-TELESCOPE DATA'],
  ['spitzer', 'A GALAXY IN INFRARED', 'NASA / SPITZER'],
  ['jet', 'A JET SPANS THE GALAXY', 'NASA / SPITZER'],
  ['grid', 'GRAVITY BENDS LIGHT', 'SCHEMATIC'],
  ['ray', 'LIGHT TAKES A CURVED ROUTE', 'SCHEMATIC'],
  ['polarized', 'A MAGNETIC FIELD SIGNATURE', 'EHT / ESO'],
  ['field', 'POLARIZED RADIO LIGHT', 'EHT / ESO'],
  ['wave', 'LIGHT IS STRETCHED', 'REDSHIFT'],
  ['spectrum', 'REDDER. DIMMER.', 'DISTANT VIEW'],
  ['clock', 'TWO POINTS OF VIEW', 'SCHEMATIC'],
  ['falling', 'THE FALLER STILL HAS A CLOCK', 'LOCAL VIEW'],
  ['boundary', 'NOT A SOLID SURFACE', 'EVENT HORIZON'],
  ['inward', 'EVERY FUTURE PATH POINTS INWARD', 'INSIDE THE HORIZON'],
  ['tides', 'GRAVITY PULLS UNEVENLY', 'TIDAL FORCE / SCHEMATIC'],
  ['mass', 'MASS CHANGES THE CROSSING', 'NOT TO SCALE'],
  ['spitzer', 'THE JET IS NOT THE HORIZON', 'NASA / SPITZER'],
  ['boundary', 'A BOUNDARY IN SPACETIME', 'NOT A SHELL'],
  ['vacuum', 'NOT A COSMIC VACUUM', 'GRAVITY DEPENDS ON MASS'],
  ['final', 'NO WAY BACK', 'M87* / EHT'],
];

export async function createFilm(timing) {
  const canvas = document.getElementById('frame');
  const ctx = canvas.getContext('2d', { alpha: false });
  if (!ctx) throw new Error('Canvas 2D context is unavailable');
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';

  const assets = {};
  for (const [key, file] of Object.entries({
    m87: 'm87_eht.jpg',
    polarized: 'm87_polarized.jpg',
    spitzer: 'm87_spitzer.jpg',
  })) {
    assets[key] = await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error(`Unable to load local visual asset ${file}`));
      image.src = `./assets/${file}`;
    });
  }

  function drawBackground(t) {
    const gradient = ctx.createLinearGradient(0, 0, WIDTH, HEIGHT);
    gradient.addColorStop(0, '#07101b');
    gradient.addColorStop(.52, '#080a10');
    gradient.addColorStop(1, '#10131a');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    for (let i = 0; i < 80; i++) {
      const x = (i * 197 + 31) % WIDTH;
      const y = (i * 419 + 67) % HEIGHT;
      const alpha = .12 + .22 * ((i % 7) / 6);
      ctx.fillStyle = `rgba(218,235,255,${alpha})`;
      ctx.beginPath();
      ctx.arc(x, y, i % 11 === 0 ? 2 : 1, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = 'rgba(3,5,9,.54)';
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    const pulse = .07 + .05 * Math.sin(t * .22);
    const halo = ctx.createRadialGradient(WIDTH * .52, HEIGHT * .47, 20, WIDTH * .52, HEIGHT * .47, 820);
    halo.addColorStop(0, `rgba(27,105,143,${pulse})`);
    halo.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
  }

  function cover(image, x, y, w, h, zoom = 1, focusX = .5, focusY = .5) {
    const sourceRatio = image.width / image.height;
    const targetRatio = w / h;
    let cropW = image.width;
    let cropH = image.height;
    if (sourceRatio > targetRatio) cropW = image.height * targetRatio;
    else cropH = image.width / targetRatio;
    cropW /= zoom;
    cropH /= zoom;
    const sx = Math.max(0, Math.min(image.width - cropW, focusX * image.width - cropW / 2));
    const sy = Math.max(0, Math.min(image.height - cropH, focusY * image.height - cropH / 2));
    ctx.drawImage(image, sx, sy, cropW, cropH, x, y, w, h);
  }

  function photo(image, t, x = 56, y = 260, w = 968, h = 1080, zoom = 1.04, focusX = .5, focusY = .5) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    cover(image, x, y, w, h, zoom + .018 * Math.sin(t * .45), focusX, focusY);
    const shade = ctx.createLinearGradient(0, y, 0, y + h);
    shade.addColorStop(0, 'rgba(4,8,12,.04)');
    shade.addColorStop(.68, 'rgba(4,8,12,.04)');
    shade.addColorStop(1, 'rgba(4,8,12,.78)');
    ctx.fillStyle = shade;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = 'rgba(211,228,237,.45)';
    ctx.lineWidth = 2;
    ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
    ctx.restore();
  }

  function label(text, x, y, size = 27, color = WHITE, weight = 600, align = 'left') {
    ctx.save();
    ctx.font = `${weight} ${size}px Arial, sans-serif`;
    ctx.textAlign = align;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = color;
    ctx.shadowColor = 'rgba(0,0,0,.7)';
    ctx.shadowBlur = 12;
    ctx.fillText(text, x, y, WIDTH - 100);
    ctx.restore();
  }

  function line(points, color = CYAN, width = 3, alpha = 1) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    points.forEach(([x, y], index) => index ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.stroke();
    ctx.restore();
  }

  function curve(points, color = CYAN, width = 3, alpha = 1) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    ctx.bezierCurveTo(points[1][0], points[1][1], points[2][0], points[2][1], points[3][0], points[3][1]);
    ctx.stroke();
    ctx.restore();
  }

  function ring(cx, cy, rx, ry, color = ACCENT, width = 5, alpha = 1) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, -.1, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  function drawGrid(cx, cy, progress = 1) {
    const radius = 510;
    for (let i = -4; i <= 4; i++) {
      const base = i * 120;
      const horizontal = [];
      const vertical = [];
      for (let j = 0; j <= 32; j++) {
        const u = -1 + j / 16;
        const bend = 180 * Math.exp(-u * u * 2.4);
        horizontal.push([cx + u * radius, cy + base + Math.sign(base || 1) * bend]);
        vertical.push([cx + base + Math.sign(base || 1) * bend, cy + u * radius]);
      }
      const reveal = Math.min(1, progress * 1.5);
      line(horizontal.slice(0, Math.max(2, Math.ceil(horizontal.length * reveal))), '#6da4b9', 2, .47);
      line(vertical.slice(0, Math.max(2, Math.ceil(vertical.length * reveal))), '#6da4b9', 2, .38);
    }
    ctx.fillStyle = '#020306';
    ctx.beginPath();
    ctx.arc(cx, cy, 132, 0, Math.PI * 2);
    ctx.fill();
    ring(cx, cy, 181, 111, ACCENT, 5, .92);
  }

  function drawWave(y, stretch = 1, color = CYAN) {
    const pts = [];
    for (let i = 0; i <= 70; i++) {
      const x = 80 + i * 13.2;
      const phase = i / 70 * Math.PI * 10 / stretch;
      pts.push([x, y + 62 * Math.sin(phase)]);
    }
    line(pts, color, 5, .92);
  }

  function drawClock(cx, cy, radius, angle, color, text) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.stroke();
    for (let i = 0; i < 12; i++) {
      const a = i * Math.PI / 6;
      line([[cx + Math.sin(a) * radius * .78, cy - Math.cos(a) * radius * .78],
        [cx + Math.sin(a) * radius * .9, cy - Math.cos(a) * radius * .9]], color, 3, .8);
    }
    line([[cx, cy], [cx + Math.sin(angle) * radius * .62, cy - Math.cos(angle) * radius * .62]], color, 5);
    label(text, cx, cy + radius + 58, 24, MUTED, 500, 'center');
  }

  function drawShot(kind, t, progress, beatIndex) {
    switch (kind) {
      case 'hook':
        photo(assets.m87, t, 0, 250, WIDTH, 1120, 1.12, .5, .53);
        label('A FALL TOWARD A BLACK HOLE', 72, 196, 39, WHITE, 700);
        ring(540, 790, 274, 266, ACCENT, 4, .58);
        break;
      case 'ring':
      case 'final':
        photo(assets.m87, t, 72, 330, 936, 930, kind === 'final' ? 1.22 : 1.12, .5, .5);
        ring(540, 780, 260, 250, ACCENT, 5, .72);
        label(kind === 'final' ? 'NO WAY BACK' : 'M87* / 2017 OBSERVATION', 78, 250, 34, WHITE, 700);
        break;
      case 'spitzer':
      case 'jet':
        photo(assets.spitzer, t, 56, 430, 968, 790, kind === 'jet' ? 1.6 : 1.05, kind === 'jet' ? .69 : .5, .52);
        label(kind === 'jet' ? 'A RELATIVISTIC JET' : 'M87 IN INFRARED', 74, 330, 34, WHITE, 700);
        if (kind === 'jet') curve([[248, 810], [420, 690], [690, 630], [920, 600]], ACCENT, 7, .9);
        break;
      case 'polarized':
      case 'field':
        photo(assets.polarized, t, 66, 350, 948, 900, kind === 'field' ? 1.25 : 1.02, .5, .5);
        label(kind === 'field' ? 'POLARIZATION TRACES MAGNETIC FIELDS' : 'M87* IN POLARIZED RADIO LIGHT', 70, 258, 31, WHITE, 700);
        if (kind === 'field') ring(540, 800, 230, 215, ACCENT, 4, .8);
        break;
      case 'grid':
      case 'ray':
        drawGrid(540, 1000, progress);
        label('CURVED SPACETIME', 75, 330, 34, WHITE, 700);
        if (kind === 'ray') {
          curve([[80, 730], [350, 700], [420, 500], [540, 505]], ACCENT, 8, .96);
          curve([[540, 505], [710, 520], [760, 745], [1010, 760]], ACCENT, 8, .96);
          label('LIGHT FOLLOWS THE CURVE', 540, 1510, 30, MUTED, 600, 'center');
        } else {
          label('SCHEMATIC / NOT TO SCALE', 540, 1510, 24, MUTED, 500, 'center');
        }
        break;
      case 'wave':
      case 'spectrum':
        label(kind === 'wave' ? 'A FALLING SIGNAL STRETCHES' : 'REDSHIFT TO A DISTANT OBSERVER', 72, 350, 31, WHITE, 700);
        drawWave(780, kind === 'wave' ? 1.35 : .72, CYAN);
        drawWave(1100, kind === 'wave' ? .8 : 1.25, ACCENT);
        line([[90, 920], [990, 920]], MUTED, 2, .55);
        for (let i = 0; i < 6; i++) line([[120 + i * 165, 900], [120 + i * 165, 941]], MUTED, 2, .5);
        label('SHORTER WAVELENGTH', 90, 1010, 21, CYAN, 500);
        label('LONGER WAVELENGTH', 990, 1010, 21, ACCENT, 500, 'right');
        break;
      case 'clock':
      case 'falling':
        label(kind === 'clock' ? 'DISTANCE CHANGES WHAT IS SEEN' : 'LOCAL TIME STILL PASSES', 68, 340, 32, WHITE, 700);
        drawClock(315, 930, 145, t * .7, CYAN, 'DISTANT VIEW');
        drawClock(770, 930, 145, t * .7 + .25, ACCENT, 'FALLING VIEW');
        line([[540, 650], [540, 1260]], MUTED, 2, .36);
        label(kind === 'clock' ? 'REDSHIFT + FADING' : 'FINITE TIME TO CROSS', 540, 1340, 24, MUTED, 500, 'center');
        break;
      case 'boundary':
      case 'inward':
        label(kind === 'boundary' ? 'EVENT HORIZON' : 'INSIDE: FUTURE PATHS LEAD INWARD', 68, 340, 32, WHITE, 700);
        ctx.fillStyle = '#020306';
        ctx.beginPath();
        ctx.arc(540, 960, 190, 0, Math.PI * 2);
        ctx.fill();
        ring(540, 960, 244, 182, ACCENT, 6);
        if (kind === 'inward') {
          for (let i = 0; i < 8; i++) {
            const a = i * Math.PI / 4 + t * .025;
            const start = [540 + Math.cos(a) * 470, 960 + Math.sin(a) * 340];
            const end = [540 + Math.cos(a) * 205, 960 + Math.sin(a) * 150];
            line([start, end], CYAN, 4, .9);
            const head = [end[0] + Math.cos(a) * 20, end[1] + Math.sin(a) * 20];
            line([end, head], ACCENT, 6, .95);
          }
        }
        label(kind === 'boundary' ? 'A BOUNDARY, NOT A SURFACE' : 'NO FUTURE-DIRECTED ESCAPE ROUTE', 540, 1390, 25, MUTED, 500, 'center');
        break;
      case 'tides':
      case 'mass':
        label(kind === 'tides' ? 'TIDAL GRAVITY' : 'THE MASS MATTERS', 74, 340, 34, WHITE, 700);
        if (kind === 'tides') {
          line([[540, 610], [540, 1270]], CYAN, 10, .8);
          label('FAR SIDE', 540, 570, 25, MUTED, 500, 'center');
          label('NEAR SIDE', 540, 1320, 25, MUTED, 500, 'center');
          for (let i = 0; i < 5; i++) {
            const y = 700 + i * 116;
            line([[440, y], [320, y]], ACCENT, 5, .9);
            line([[640, y], [760, y]], ACCENT, 5, .9);
          }
          label('UNEQUAL PULL', 540, 1500, 26, WHITE, 600, 'center');
        } else {
          ctx.fillStyle = '#020306';
          ctx.beginPath(); ctx.arc(305, 970, 95, 0, Math.PI * 2); ctx.fill();
          ctx.beginPath(); ctx.arc(760, 970, 230, 0, Math.PI * 2); ctx.fill();
          ring(305, 970, 120, 83, ACCENT, 5);
          ring(760, 970, 264, 188, ACCENT, 5);
          label('STELLAR-MASS', 305, 1270, 22, MUTED, 500, 'center');
          label('SUPERMASSIVE', 760, 1270, 22, MUTED, 500, 'center');
          label('HORIZON TIDES CAN BE GENTLER', 540, 1450, 24, WHITE, 600, 'center');
        }
        break;
      case 'vacuum':
        label('FAR AWAY, GRAVITY FOLLOWS MASS', 70, 350, 30, WHITE, 700);
        ctx.fillStyle = '#020306';
        ctx.beginPath(); ctx.arc(540, 900, 115, 0, Math.PI * 2); ctx.fill();
        ring(540, 900, 150, 100, ACCENT, 5);
        for (let i = 0; i < 4; i++) {
          const y = 650 + i * 165;
          curve([[85, y], [350, y - 100], [730, y + 100], [995, y]], CYAN, 4, .72);
        }
        label('NOT A VACUUM CLEANER', 540, 1410, 34, ACCENT, 700, 'center');
        break;
    }
  }

  function wrap(text, maxWidth) {
    const words = text.split(/\s+/);
    const rows = [];
    let row = '';
    for (const word of words) {
      const candidate = row ? `${row} ${word}` : word;
      if (row && ctx.measureText(candidate).width > maxWidth) {
        rows.push(row);
        row = word;
      } else row = candidate;
    }
    if (row) rows.push(row);
    return rows;
  }

  function drawCaption(time) {
    const cue = timing.cues.find(item => time >= item.start_sec && time < item.end_sec);
    if (!cue) return;
    const text = cue.text;
    ctx.save();
    ctx.font = '500 37px Arial, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const rows = wrap(text, 920);
    const y0 = 1570 - (rows.length - 1) * 25;
    const fade = Math.min(1, (time - cue.start_sec) / .16, (cue.end_sec - time) / .12);
    ctx.globalAlpha = Math.max(.72, fade);
    const shade = ctx.createLinearGradient(0, 1460, 0, 1780);
    shade.addColorStop(0, 'rgba(4,6,9,0)');
    shade.addColorStop(.36, 'rgba(4,6,9,.74)');
    shade.addColorStop(1, 'rgba(4,6,9,.05)');
    ctx.fillStyle = shade;
    ctx.fillRect(0, 1450, WIDTH, 340);
    ctx.fillStyle = WHITE;
    ctx.shadowColor = '#000';
    ctx.shadowBlur = 14;
    rows.slice(0, 3).forEach((row, index) => ctx.fillText(row, WIDTH / 2, y0 + index * 52, 930));
    ctx.restore();
  }

  function render(time, thumbnail = false) {
    const t = Math.max(0, Math.min(time, timing.duration_sec));
    drawBackground(t);
    if (thumbnail) {
      photo(assets.m87, t, 0, 160, WIDTH, 1320, 1.32, .5, .5);
      const shade = ctx.createLinearGradient(0, 970, 0, HEIGHT);
      shade.addColorStop(0, 'rgba(2,4,8,0)');
      shade.addColorStop(1, 'rgba(2,4,8,.95)');
      ctx.fillStyle = shade;
      ctx.fillRect(0, 800, WIDTH, HEIGHT - 800);
      ring(540, 820, 285, 272, ACCENT, 7, .82);
      label('BEYOND THE', 74, 1500, 55, WHITE, 700);
      label('HORIZON', 74, 1580, 78, WHITE, 700);
      label('M87* / EHT RADIO OBSERVATION', 78, 1680, 22, MUTED, 600);
      return;
    }

    const beatLength = timing.duration_sec / SHOTS.length;
    const beatIndex = Math.min(SHOTS.length - 1, Math.floor(t / beatLength));
    const progress = (t - beatIndex * beatLength) / beatLength;
    const [kind, title, source] = SHOTS[beatIndex];
    drawShot(kind, t, progress, beatIndex);
    if (kind !== 'hook' && kind !== 'final') label(title, 72, 194, 25, WHITE, 700);
    label(source, 78, 1370, 19, MUTED, 600);
    drawCaption(t);
    const sceneNumber = String(beatIndex + 1).padStart(2, '0');
    label(sceneNumber, 1005, 1830, 19, MUTED, 500, 'right');
  }

  return { render };
}
