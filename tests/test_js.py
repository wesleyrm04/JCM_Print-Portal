"""Checks the browser code that can run without a browser:
 1. static/js/layout.js agrees with portal/layout.py for every layout x paper size
 2. static/js/pdf.js writes PDFs that a real PDF engine (pdfium) opens, with the right page count/size
Run: python tests/test_js.py   (needs node)"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pypdfium2 as pdfium
from helpers import make_jpeg
from portal import settings
from portal.layout import layout_rects

failures = []


def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c:
        failures.append(m)


cfg = settings.get_config()
pm, pg = cfg["page"]["margin_mm"], cfg["page"]["gap_mm"]
cases = []
for lay in cfg["layouts"]:
    for pk, ps in cfg["paper_sizes"].items():
        for (m, g) in ((pm, pg), (0.0, 1.0), (10.0, 3.0)):
            r, c, rows = layout_rects(lay, ps["w"], ps["h"], m, g)
            cases.append({"layout": lay, "pw": ps["w"], "ph": ps["h"], "m": m, "g": g, "rects": r})
tmp = Path(tempfile.mkdtemp())
(tmp / "cases.json").write_text(json.dumps(cases))
js = """
const L = require('%s/static/js/layout.js');
const cases = JSON.parse(require('fs').readFileSync('%s/cases.json','utf8'));
let bad = 0;
for (const c of cases) {
  const got = L.layoutRects(c.layout, c.pw, c.ph, c.m, c.g).rects;
  if (got.length !== c.rects.length) { bad++; continue; }
  for (let i = 0; i < got.length; i++) for (let k = 0; k < 4; k++) if (Math.abs(got[i][k] - c.rects[i][k]) > 1e-6) bad++;
}
// shelf packing sanity: Package C (2x 50.8mm + 4x 25.4mm) on a 5R sheet, two sets
const pcs = [];
for (let s = 0; s < 2; s++) { for (let i = 0; i < 2; i++) pcs.push({w:50.8,h:50.8}); for (let i = 0; i < 4; i++) pcs.push({w:25.4,h:25.4}); }
const p2 = L.packPieces(pcs, 127, 177.8, 4, 2);
const p3 = L.packPieces(pcs.concat(pcs.slice(0,6)), 127, 177.8, 4, 2);
console.log(JSON.stringify({cases: cases.length, bad, twoSetsFit: !!p2, threeSetsFit: !!p3,
  tooWide: L.packPieces([{w:200,h:10}], 127, 177.8, 4, 2) === null}));
""" % (ROOT, tmp)
out = subprocess.run(["node", "-e", js], capture_output=True, text=True)
print("layout.js vs layout.py")
if out.returncode:
    print(out.stderr)
    failures.append("node error")
else:
    r = json.loads(out.stdout)
    check(r["bad"] == 0, f"JS and Python agree on all {r['cases']} layout/paper/margin cases")
    check(r["twoSetsFit"], "two Package-C sets fit on one 5R sheet")
    check(not r["threeSetsFit"], "a third Package-C set correctly goes to a new sheet")
    check(r["tooWide"], "piece wider than the sheet is reported, not silently clipped")

print("pdf.js")
jpgs = [make_jpeg((200, 50, 50), (800, 1130)), make_jpeg((50, 200, 50), (800, 1130)), make_jpeg((50, 50, 200), (800, 1130))]
for i, j in enumerate(jpgs):
    (tmp / f"p{i}.jpg").write_bytes(j)
js2 = """
const P = require('%s/static/js/pdf.js'); const fs = require('fs');
const pages = [0,1,2].map(i => ({jpeg: new Uint8Array(fs.readFileSync('%s/p'+i+'.jpg')), w: 800, h: 1130}));
fs.writeFileSync('%s/out.pdf', P.buildJpegPdf(pages, 210, 297));
""" % (ROOT, tmp, tmp)
r = subprocess.run(["node", "-e", js2], capture_output=True, text=True)
check(r.returncode == 0, "node built a PDF" + (r.stderr[:200] if r.returncode else ""))
doc = pdfium.PdfDocument(str(tmp / "out.pdf"))
check(len(doc) == 3, f"pdfium opens it: {len(doc)} pages")
w, h = doc[0].get_size()
check(abs(w - 595.28) < 0.1 and abs(h - 841.89) < 0.1, "page size is A4")
pix = [doc[i].render(scale=0.2).to_pil().convert("RGB").getpixel((5, 5)) for i in range(3)]
check(pix[0][0] > 150 and pix[1][1] > 150 and pix[2][2] > 150, f"each page shows its own image {pix}")
print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED"))
sys.exit(1 if failures else 0)
