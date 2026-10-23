"""Edge cases in a real browser: unreadable image, big phone photos, screenshots."""
import io, json, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from helpers import Server, make_jpeg, make_pdf
from playwright.sync_api import sync_playwright
from test_browser import until

fails = []
def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)

def big_photo(seed):
    im = Image.new("RGB", (4000, 3000))
    d = ImageDraw.Draw(im)
    for i in range(0, 3000, 50):
        d.rectangle([0, i, 4000, i + 50], fill=((seed * 40 + i // 20) % 255, (i // 12) % 255, (seed * 70) % 255))
    d.ellipse([1200, 700, 2800, 2300], fill=(240, 220, 200))
    b = io.BytesIO(); im.save(b, "JPEG", quality=88); return b.getvalue()

tmp = Path(tempfile.mkdtemp()); up, data = tmp / "o", tmp / "d"
cfg = json.load(open(Path(__file__).resolve().parent.parent / "config.json")); cfg["analysis"]["use_libreoffice"] = False
srv = Server(8126, up, data, config=cfg); base = srv.base
try:
    with sync_playwright() as p:
        br = p.chromium.launch(); ctx = br.new_context(viewport={"width": 390, "height": 844}); page = ctx.new_page()
        errs = []; page.on("pageerror", lambda e: errs.append(str(e)))
        print("unreadable image does not hang the layout")
        page.goto(base + "/print"); page.fill("#custName", "Ana")
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1" + b"\x00" * 200
        page.set_input_files("#fileInput", [{"name": "good.jpg", "mimeType": "image/jpeg", "buffer": make_jpeg()},
                                            {"name": "iphone.heic", "mimeType": "image/heic", "buffer": heic}])
        page.wait_for_selector("#photoDlg[open]", timeout=10000)
        until(page, "() => !document.getElementById('phBroken').classList.contains('hidden')", 30)
        check("iphone.heic" in page.inner_text("#phBroken"), "warning names the unreadable photo")
        page.screenshot(path="/tmp/shots/photo_studio.png")
        page.click("#phConfirm"); until(page, "() => !document.getElementById('photoDlg').open", 30)
        check("Layout ready" in page.inner_text("#studioSummary"), "layout still confirmed with the readable photo")
        check("can't be previewed" in page.inner_text("#fileList") or "previewed" in page.inner_text("#fileList"), "file row flags the unreadable photo")
        page.click("#submitBtn"); page.wait_for_selector("#result:not(.hidden)", timeout=30000)
        oj = json.load(open(next(up.glob("*/001_*/order.json"))))
        check(oj["needs_review"], f"order flagged for staff because a photo could not be placed: {oj['review_notes']}")

        print("12 big phone photos (4000x3000) as a contact sheet x3 copies")
        page.click("#againBtn"); page.fill("#custName", "Big")
        photos = [{"name": f"IMG_{i}.jpg", "mimeType": "image/jpeg", "buffer": big_photo(i)} for i in range(12)]
        page.set_input_files("#fileInput", photos)
        page.wait_for_selector("#photoDlg[open]", timeout=20000)
        until(page, "() => document.getElementById('phPage').textContent.includes('of')", 60)
        page.click('#phLayouts [data-lay="contact"]'); page.fill("#phCopies", "3")
        check("1 of 2" in page.inner_text("#phPage"), f"36 photos on 35-up sheets = 2 sheets ({page.inner_text('#phPage')})")
        t0 = time.time(); page.click("#phConfirm"); until(page, "() => !document.getElementById('photoDlg').open", 90)
        dt = time.time() - t0
        check(dt < 45, f"PDF built in {dt:.1f}s")
        page.screenshot(path="/tmp/shots/print_after.png", full_page=True)
        page.click("#submitBtn"); page.wait_for_selector("#result:not(.hidden)", timeout=60000)
        d = next(up.glob("*/002_*")); pdfp = next(d.glob("*Generated_Photo_Layout.pdf"))
        size_mb = pdfp.stat().st_size / 1048576
        check(len(pdfium.PdfDocument(str(pdfp))) == 2 and size_mb < 50, f"2-page layout PDF, {size_mb:.1f} MB (limit 50)")
        check(not errs, f"no JS errors {errs}")

        print("screenshots")
        for name, path in [("hub", "/"), ("print", "/print"), ("repair", "/repair"), ("status", "/status")]:
            page.goto(base + path); page.wait_for_load_state("networkidle"); page.screenshot(path=f"/tmp/shots/{name}.png", full_page=(name != "repair"))
        page.goto(base + "/print"); page.set_input_files("#fileInput", [{"name": "report.pdf", "mimeType": "application/pdf", "buffer": make_pdf(["bw", "color", "bw"])}])
        until(page, "() => document.getElementById('estTotal').textContent.includes('16')", 20); page.fill("#custName", "Juan"); page.screenshot(path="/tmp/shots/print_doc.png", full_page=True)
        br.close()
finally:
    out = srv.stop()
    if "Traceback" in out: print(out[-1500:]); fails.append("server traceback")
print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")); sys.exit(1 if fails else 0)
