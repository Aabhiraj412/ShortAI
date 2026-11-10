const fs = require('fs');
const path = require('path');
const { Resvg } = require('@resvg/resvg-js');

const BASE_DIR = path.resolve(__dirname, '..');
const CHAR_SVG_DIR = path.join(BASE_DIR, 'assets', 'characters', 'host');
const CHAR_SPRITE_DIR = path.join(CHAR_SVG_DIR, 'sprites');
const BG_SVG_DIR = path.join(BASE_DIR, 'assets', 'backgrounds');
const BG_RENDERED_DIR = path.join(BG_SVG_DIR, 'rendered');

fs.mkdirSync(CHAR_SPRITE_DIR, { recursive: true });
fs.mkdirSync(BG_RENDERED_DIR, { recursive: true });

function rasterizeDir(srcDir, outDir, targetWidth) {
    const files = fs.readdirSync(srcDir).filter(f => f.endsWith('.svg'));
    for (const f of files) {
        const svgPath = path.join(srcDir, f);
        const outName = f.replace('.svg', '.png');
        const outPath = path.join(outDir, outName);
        
        const svg = fs.readFileSync(svgPath);
        const opts = targetWidth ? { fitTo: { mode: 'width', value: targetWidth } } : {};
        const resvg = new Resvg(svg, opts);
        const png = resvg.render().asPng();
        fs.writeFileSync(outPath, png);
        console.log(`Rasterized ${f} -> ${outName} (${png.length} bytes)`);
    }
}

console.log('Rasterizing character SVGs to 2x high-res sprites...');
rasterizeDir(CHAR_SVG_DIR, CHAR_SPRITE_DIR, 600);

console.log('Rasterizing background SVGs to 1080x1920...');
rasterizeDir(BG_SVG_DIR, BG_RENDERED_DIR, 1080);

console.log('All vector assets rasterized successfully!');
