/* Photo layout maths - a line-for-line port of portal/layout.py (tests/test_layout.py proves they agree).
   Used for BOTH the on-screen preview and the PDF, so what you see is exactly what prints. */
(function (root) {
  'use strict';
  const EPS = 1e-9;

  function fixedCount(cw, ch, pw, ph, margin, gap) {
    const ax = pw - 2 * margin, ay = ph - 2 * margin;
    const cols = Math.floor((ax + gap) / (cw + gap) + EPS);
    const rows = Math.floor((ay + gap) / (ch + gap) + EPS);
    return [Math.max(cols, 0), Math.max(rows, 0)];
  }

  // -> {rects:[[x,y,w,h],...], cols, rows}   (mm)
  function layoutRects(layout, pw, ph, margin, gap) {
    const t = layout.type || 'fixed';
    if (t === 'full') return { rects: [[margin, margin, pw - 2 * margin, ph - 2 * margin]], cols: 1, rows: 1 };
    if (t === 'grid') {
      const cols = layout.cols | 0, rows = layout.rows | 0;
      const cw = (pw - 2 * margin - (cols - 1) * gap) / cols;
      const ch = (ph - 2 * margin - (rows - 1) * gap) / rows;
      const rects = [];
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) rects.push([margin + c * (cw + gap), margin + r * (ch + gap), cw, ch]);
      return { rects, cols, rows };
    }
    const w = +layout.w, h = +layout.h;
    let best = null;
    for (const m of [margin, 0]) {
      for (const [cw, ch] of [[w, h], [h, w]]) {
        const [cols, rows] = fixedCount(cw, ch, pw, ph, m, gap);
        const n = cols * rows;
        if (n > 0 && (best === null || n > best[0])) best = [n, cw, ch, cols, rows];
      }
      if (best) break;
    }
    if (!best) return { rects: [[margin, margin, pw - 2 * margin, ph - 2 * margin]], cols: 1, rows: 1 };
    const [, cw, ch, cols, rows] = best;
    const totalW = cols * cw + (cols - 1) * gap, totalH = rows * ch + (rows - 1) * gap;
    const x0 = (pw - totalW) / 2, y0 = (ph - totalH) / 2;
    const rects = [];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) rects.push([x0 + c * (cw + gap), y0 + r * (ch + gap), cw, ch]);
    return { rects, cols, rows };
  }

  // Shelf-pack square pieces [{w,h}] on a sheet. Returns [{x,y}] or null when they do not fit.
  function packPieces(pieces, W, H, margin, gap) {
    const out = [];
    let x = margin, y = margin, rowH = 0;
    for (const p of pieces) {
      if (x + p.w > W - margin + EPS) {
        if (x === margin) return null;           // a single piece wider than the sheet
        x = margin; y += rowH + gap; rowH = 0;
        if (x + p.w > W - margin + EPS) return null;
      }
      if (y + p.h > H - margin + EPS) return null;
      out.push({ x, y });
      x += p.w + gap;
      rowH = Math.max(rowH, p.h);
    }
    return out;
  }

  root.JCMLayout = { layoutRects, packPieces };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.JCMLayout;
})(typeof window !== 'undefined' ? window : globalThis);
