/* Minimal PDF writer: one full-page JPEG per page. Replaces jsPDF so nothing is downloaded from the internet. */
(function (root) {
  'use strict';
  const MM = 72 / 25.4;

  // pages: [{jpeg: Uint8Array, w: px, h: px}]   pageW/pageH in mm   -> Uint8Array
  function buildJpegPdf(pages, pageWmm, pageHmm) {
    const enc = new TextEncoder();
    const chunks = [];
    let offset = 0;
    const offsets = [];
    const push = b => { chunks.push(b); offset += b.length; };
    const text = s => push(enc.encode(s));
    const W = (pageWmm * MM).toFixed(2), H = (pageHmm * MM).toFixed(2);
    const n = pages.length;
    // object numbers: 1 catalog, 2 pages, then per page i: page=3+3i, content=4+3i, image=5+3i
    const kids = pages.map((_, i) => (3 + 3 * i) + ' 0 R').join(' ');
    text('%PDF-1.4\n%\u00e2\u00e3\u00cf\u00d3\n');
    const obj = (num, body) => { offsets[num] = offset; text(num + ' 0 obj\n' + body + '\nendobj\n'); };
    obj(1, '<< /Type /Catalog /Pages 2 0 R >>');
    obj(2, '<< /Type /Pages /Kids [' + kids + '] /Count ' + n + ' >>');
    pages.forEach((p, i) => {
      const pg = 3 + 3 * i, ct = 4 + 3 * i, im = 5 + 3 * i;
      obj(pg, '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ' + W + ' ' + H + '] /Resources << /XObject << /Im0 ' + im + ' 0 R >> >> /Contents ' + ct + ' 0 R >>');
      const content = 'q ' + W + ' 0 0 ' + H + ' 0 0 cm /Im0 Do Q';
      obj(ct, '<< /Length ' + content.length + ' >>\nstream\n' + content + '\nendstream');
      offsets[im] = offset;
      text(im + ' 0 obj\n<< /Type /XObject /Subtype /Image /Width ' + p.w + ' /Height ' + p.h +
        ' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ' + p.jpeg.length + ' >>\nstream\n');
      push(p.jpeg);
      text('\nendstream\nendobj\n');
    });
    const total = 3 + 3 * n;
    const xref = offset;
    let x = 'xref\n0 ' + total + '\n0000000000 65535 f \n';
    for (let i = 1; i < total; i++) x += String(offsets[i]).padStart(10, '0') + ' 00000 n \n';
    text(x + 'trailer\n<< /Size ' + total + ' /Root 1 0 R >>\nstartxref\n' + xref + '\n%%EOF\n');
    const out = new Uint8Array(offset);
    let pos = 0;
    for (const c of chunks) { out.set(c, pos); pos += c.length; }
    return out;
  }

  root.JCMPdf = { buildJpegPdf };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.JCMPdf;
})(typeof window !== 'undefined' ? window : globalThis);
