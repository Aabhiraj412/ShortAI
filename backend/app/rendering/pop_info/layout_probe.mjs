// Layout probe: opens a Pop Info film page through Lemo-Opuscar's own page loader and returns
// window.layoutAudit(t) for each requested time (text boxes vs. the Shorts safe area).
// usage: node layout_probe.mjs <lemo_root> <film_dir> <t1,t2,...>
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const [lemoRoot, filmDir, times] = process.argv.slice(2);
const { openDemo } = await import(pathToFileURL(path.join(lemoRoot, 'core', 'render', 'page.mjs')).href);
const { browser, page } = await openDemo(path.resolve(filmDir), { w: 1080, h: 1920 });
const out = [];
for (const t of times.split(',').map(Number)) out.push(await page.evaluate(x => window.layoutAudit(x), t));
const extra = await page.evaluate(() => ({ fonts_ok: window.FONTS_OK, asset_fallbacks: window.ASSET_FALLBACKS }));
await browser.close();
process.stdout.write(JSON.stringify({ samples: out, ...extra }));
process.exit(0);
