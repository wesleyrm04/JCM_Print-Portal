"""Unit tests for the framework-free logic (run: python tests/test_logic.py)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("JCM_DATA_DIR", tempfile.mkdtemp())

import pypdfium2 as pdfium
from helpers import make_jpeg, make_pdf
from portal import docgen, pricing, settings, storage
from portal.layout import layout_rects, per_page

CFG = settings.get_config()
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def test_layout():
    print("layout")
    pm, pg = CFG["page"]["margin_mm"], CFG["page"]["gap_mm"]
    A4, LET, LONG = (CFG["paper_sizes"][k] for k in ("A4", "Letter", "Long"))
    got = {l["key"]: per_page(l, A4["w"], A4["h"], pm, pg) for l in CFG["layouts"]}
    print("   per page on A4:", got)
    expect = {"full": 1, "20x25": 1, "13x18": 2, "10x15": 2, "9x13": 4, "wallet": 9, "contact": 35}
    for k, v in expect.items():
        check(got[k] == v, f"A4 {k} fits {v} (got {got[k]})")
    check(got["8R"] >= 1, "8R (203x254) still fits A4 using the zero-margin fallback")
    for l in CFG["layouts"]:                       # nothing may leave the paper
        for ps in (A4, LET, LONG):
            rects, _, _ = layout_rects(l, ps["w"], ps["h"], pm, pg)
            for (x, y, w, h) in rects:
                if x < -1e-6 or y < -1e-6 or x + w > ps["w"] + 1e-6 or y + h > ps["h"] + 1e-6:
                    check(False, f"{l['key']} leaves the page")
                    return
    check(True, "all layouts stay inside A4/Letter/Long")


def test_docgen():
    print("docgen")
    text = "Dear Sir/Madam,\n\nThis is a test of the typing service, with n-tilde \u00f1 and peso \u20b1500.\n" * 30
    pdf = docgen.make_pdf(text)
    doc = pdfium.PdfDocument(pdf)
    n = len(doc)
    check(n == docgen.count_pages(text) and n >= 2, f"PDF opens in pdfium with {n} pages == count_pages")
    got = doc[0].get_textpage().get_text_range()
    check("typing service" in got and "PHP" in got, "text is real, extractable text (peso sign -> PHP)")
    check("\u00f1" in got, "n-tilde survives WinAnsi encoding")
    w, h = doc[0].get_size()
    check(abs(w - 595.28) < 0.1 and abs(h - 841.89) < 0.1, "page is A4")
    check(docgen.count_pages("") == 1, "empty text is 1 page")
    check(docgen.count_pages("x\n" * 200) == 4, "200 short lines -> 4 pages (53 lines/page)")
    long_word = "A" * 400
    check(all(docgen._text_w(l) <= docgen.TEXT_W + 0.01 for l in docgen.wrap_lines(long_word)), "very long words are broken to fit")
    z = zipfile.ZipFile(io.BytesIO(docgen.make_docx("Hello <b> & world")))
    check("Hello &lt;b&gt; &amp; world" in z.read("word/document.xml").decode(), "DOCX escapes XML characters")


def test_color():
    print("colour detection")
    d = Path(tempfile.mkdtemp())
    p = d / "t.pdf"
    p.write_bytes(make_pdf(["bw", "color", "logo", "tint", "bw"]))
    r = pricing.analyze_pdf(p, CFG)
    check(r["pages"] == 5 and r["complete"], "5 pages counted")
    check(r["color_pages"] == 2 and r["bw_pages"] == 3, f"colour page + logo page = colour, tint/bw = B&W (got {r['color_pages']} colour)")


def test_pricing():
    print("pricing")
    a = [{"bw_pages": 3, "color_pages": 1}, {"bw_pages": 0, "color_pages": 2}]
    r = pricing.doc_total(a, "auto", 1, "A4", CFG)
    check(r["total"] == 3 * 3 + 3 * 10, f"auto: 3 bw + 3 colour = 39 (got {r['total']})")
    check(pricing.doc_total(a, "force_bw", 1, "A4", CFG)["total"] == 6 * 3, "force B&W")
    check(pricing.doc_total(a, "force_color", 2, "A4", CFG)["total"] == 6 * 10 * 2, "force colour x2 copies")
    check(pricing.photo_total(CFG, "180gsm", "A4", 3)["total"] == 90, "180gsm x3 = 90")
    check(pricing.photo_rate(CFG, "plain", "Long") == 15, "plain Long = 15")
    check(pricing.typed_total(CFG, 2, 1)["total"] == 5 + 2 * 3, "typed doc: 5 fee + 2 pages")
    check(pricing.photo_pages_expected(CFG, "10x15", "A4", 3, 1) == 2, "3 photos @ 2 per sheet = 2 sheets")


def test_files():
    print("file checks")
    check(storage.sniff_ok("pdf", b"%PDF-1.4 ..."), "real pdf accepted")
    check(not storage.sniff_ok("pdf", b"MZ\x90\x00 this is an exe"), "exe renamed .pdf rejected")
    check(storage.sniff_ok("jpg", make_jpeg()[:20]), "real jpeg accepted")
    check(not storage.sniff_ok("png", b"<html>"), "html renamed .png rejected")
    check(storage.safe_filename("..\\..\\evil/../my file?.PDF") == "my file.pdf", "path tricks removed from file names")
    check(storage.sanitize("Juan/Dela:Cruz*?") == "JuanDelaCruz", "folder-name characters removed")
    check(storage.folder_name("R", 3, "Ana", "Repair Request") == "R003_[Ana] - Repair Request", "folder naming")
    d = Path(tempfile.mkdtemp())
    n1 = storage.acquire_number(d, "")
    n2 = storage.acquire_number(d, "")
    storage.release_number(d, "", n2)
    n3 = storage.acquire_number(d, "")
    check((n1, n2, n3) == (1, 2, 2), "numbers are unique and a released number is reused (no gaps)")
    (d / "003_[x] - y").mkdir()
    check(storage.acquire_number(d, "") == 4, "number is skipped when its folder already exists")
    check(storage.resolve_file(d, "../x") is None and storage.resolve_file(d, ".locks") is None, "no path traversal on downloads")


def test_office():
    print("office files")
    d = Path(tempfile.mkdtemp())
    p = d / "t.docx"
    p.write_bytes(docgen.make_docx("hello\n" * 10))
    cfg = json.loads(json.dumps(CFG))
    cfg["analysis"]["use_libreoffice"] = False
    r = pricing.analyze_file(p, "t.docx", cfg)
    check(r["needs_review"] and r["status"] in ("unknown", "estimated"), "docx without LibreOffice is flagged for staff review, never silently 1 page")
    img = pricing.analyze_file(d / "x.jpg", "x.jpg", cfg)
    check(img["kind"] == "image" and img["color_pages"] == 1, "photos count as colour pages")


def test_retention():
    print("retention (auto-delete of old customer files)")
    try:
        from portal import app as appmod, db
    except ImportError:
        print("  skip (starlette not installed)")
        return
    base = Path(tempfile.mkdtemp())
    os.environ["JCM_UPLOAD_DIR"] = str(base)
    db.init()

    def mk(oid, status, updated):
        f = base / "2026-01-01" / f"{oid}_[x] - y"
        f.mkdir(parents=True)
        (f / "a.pdf").write_bytes(b"%PDF-1.4")
        (f / "instructions.txt").write_text("x")
        o = {"order_id": oid, "date": "2026-01-01", "created": updated, "updated": updated, "customer_name": "X",
             "preset_request": "y", "status": status, "total": 10, "total_files": 1, "folder": str(f), "category": "print"}
        (f / "order.json").write_text(json.dumps(o))
        db.upsert(o)
        return f
    old = mk("20260101-901", "Completed", "2020-01-01T10:00:00+08:00")
    recent = mk("20260101-902", "Completed", settings.now().isoformat(timespec="seconds"))
    pending = mk("20260101-903", "Pending", "2020-01-01T10:00:00+08:00")
    n = appmod.purge_expired()
    check(n == 1, f"exactly one order purged ({n})")
    check(sorted(p.name for p in old.iterdir()) == ["order.json"], "old completed order: files deleted, order.json (sales record) kept")
    check((recent / "a.pdf").exists(), "recently completed order is untouched")
    check((pending / "a.pdf").exists(), "old but still-Pending order is never deleted")
    check(json.loads((old / "order.json").read_text())["files_deleted"] is True, "order.json records that files were purged")


if __name__ == "__main__":
    for t in (test_layout, test_docgen, test_color, test_pricing, test_files, test_office, test_retention):
        t()
    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    sys.exit(1 if failures else 0)
