"""Real-browser test (Playwright + Chromium) against the real server.
Run: python tests/test_browser.py     (needs: pip install playwright && playwright install chromium)"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pypdfium2 as pdfium
from helpers import Server, make_jpeg, make_pdf
from playwright.sync_api import sync_playwright

failures = []


def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c:
        failures.append(m)


def until(page, js, timeout=15):
    """Poll a JS predicate via CDP evaluate (Playwright's wait_for_function uses eval, which our strict CSP blocks)."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate(js):
            return True
        time.sleep(0.15)
    raise AssertionError("timed out waiting for: " + js)


def main():
    tmp = Path(tempfile.mkdtemp())
    up, data = tmp / "orders", tmp / "data"
    cfg = json.load(open(Path(__file__).resolve().parent.parent / "config.json"))
    cfg["analysis"]["use_libreoffice"] = False
    srv = Server(8125, up, data, config=cfg)
    base = srv.base
    problems = []
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            ctx = br.new_context(viewport={"width": 390, "height": 800}, accept_downloads=True)   # phone-sized
            page = ctx.new_page()
            page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            page.on("requestfailed", lambda r: problems.append(f"requestfailed: {r.url}"))
            page.on("response", lambda r: problems.append(f"HTTP {r.status}: {r.url}") if r.status >= 400 and "/api/admin/me" not in r.url else None)
            page.on("request", lambda r: problems.append(f"EXTERNAL request: {r.url}") if not r.url.startswith(base) and not r.url.startswith(("data:", "blob:")) else None)

            print("pages load offline, no errors")
            for path in ["/", "/print", "/repair", "/souvenirs", "/status", "/admin"]:
                page.goto(base + path)
                page.wait_for_load_state("networkidle")
                check(page.title() != "", f"{path} loads ({page.title()})")
            page.goto(base + "/")
            check("JCM" in page.inner_text("h1"), "hub shows the shop name from config")
            check(page.locator("#printFacts li").count() == 4, "hub shows price facts from config")

            print("document order in the browser")
            page.goto(base + "/print")
            page.fill("#custName", "Juan Dela Cruz")
            page.set_input_files("#fileInput", [{"name": "mixed.pdf", "mimeType": "application/pdf", "buffer": make_pdf(["bw", "color", "bw"])}])
            until(page, "() => document.getElementById('estTotal').textContent.includes('16.00')", 15.0)
            tot = page.inner_text("#estTotal")
            check(tot == "\u20b116.00", f"total shows a real peso sign: {tot!r}")
            check("1 color" in page.inner_text("#fileList"), "file row shows 1 color page")
            page.select_option("#colorMode", "force_bw")
            check(page.inner_text("#estTotal") == "\u20b19.00", "force B&W = 9.00")
            page.fill("#copies", "2")
            check(page.inner_text("#estTotal") == "\u20b118.00", "2 copies = 18.00")
            page.fill("#notes", "please staple")
            page.click("#submitBtn")
            page.wait_for_selector("#result:not(.hidden)", timeout=20000)
            oid = page.inner_text("#resId"); code = page.inner_text("#resCode")
            check(oid.endswith("-001") and len(code) == 6, f"order sent: {oid} / {code}")
            check(any(up.glob("*/001_*/order.json")), "order is on the NAS folder")
            oj = json.load(open(next(up.glob("*/001_*/order.json"))))
            check(oj["total"] == 18.0 and oj["options"]["color_mode"] == "force_bw", "server total 18.00 recorded")

            print("status page")
            page.click("#resStatus")
            page.wait_for_selector("#out:not(.hidden)")
            check(oid in page.inner_text("#oId") and "Pending" in page.inner_text("#oBadge"), "status page shows the order as Pending")
            page.click("#langBtn")
            check(page.inner_text("#oBadge") == "Naghihintay", "Filipino toggle translates the status")
            page.click("#langBtn")

            print("photo layout flow")
            page.goto(base + "/print")
            page.fill("#custName", "Maria")
            imgs = [{"name": f"p{i}.jpg", "mimeType": "image/jpeg", "buffer": make_jpeg((60 * i + 40, 90, 150), (900, 600))} for i in range(3)]
            page.set_input_files("#fileInput", imgs)
            page.wait_for_selector("#photoDlg[open]", timeout=10000)
            check(page.input_value("#preset") == "photo", "images auto-switch to Photo prints and open the studio")
            until(page, "() => document.getElementById('phPage').textContent.includes('of')", 15)
            page.click('#phLayouts [data-lay="10x15"]')
            check("1 of 2" in page.inner_text("#phPage"), f"10x15: 3 photos -> 2 sheets ({page.inner_text('#phPage')})")
            page.select_option("#phPaperType", "180gsm")
            check(page.inner_text("#phTotal") == "\u20b160.00", "2 sheets x 30 = 60.00")
            page.click("#phConfirm")
            until(page, "() => !document.getElementById('photoDlg').open", 30.0)
            check("Layout ready" in page.inner_text("#studioSummary"), "layout confirmed")
            page.click("#submitBtn")
            page.wait_for_selector("#result:not(.hidden)", timeout=30000)
            d = next(up.glob("*/002_*"))
            pdf = pdfium.PdfDocument(str(d / "03_Generated_Photo_Layout.pdf")) if (d / "03_Generated_Photo_Layout.pdf").exists() else pdfium.PdfDocument(str(next(d.glob("*Generated_Photo_Layout.pdf"))))
            check(len(pdf) == 2, f"layout PDF has 2 sheets ({len(pdf)})")
            im = pdf[0].render(scale=0.3).to_pil().convert("RGB")
            nonwhite = sum(1 for px in list(im.tobytes()[i:i+3] for i in range(0, len(im.tobytes()), 3)) if px != b"\xff\xff\xff")
            check(nonwhite > im.width * im.height * 0.1, "sheet 1 actually contains the photos")
            oj = json.load(open(d / "order.json"))
            check(oj["total"] == 60.0 and not oj["needs_review"], f"server agrees: total {oj['total']}, no review flag")

            print("rush id flow")
            page.click("#againBtn")
            page.select_option("#preset", "rush:C")
            page.fill("#custName", "Pedro")
            page.set_input_files("#fileInput", imgs[:2])
            page.wait_for_selector("#idDlg[open]", timeout=10000)
            until(page, "() => document.getElementById('idInfo').textContent.includes('1')", 15)
            page.fill("#idZoom", "2")
            page.dispatch_event("#idZoom", "input")
            box = page.locator("#idCrop").bounding_box()
            page.mouse.move(box["x"] + 100, box["y"] + 100); page.mouse.down(); page.mouse.move(box["x"] + 130, box["y"] + 110, steps=4); page.mouse.up()
            page.click("#idNextStep")
            page.wait_for_selector("#idSheetPane:not(.hidden)")
            check("2 photo" in page.inner_text("#idSummary") and page.inner_text("#idTotal") == "\u20b160.00", f"ID summary: {page.inner_text('#idSummary')}")
            page.click("#idConfirm")
            until(page, "() => !document.getElementById('idDlg').open", 30.0)
            page.click("#submitBtn")
            page.wait_for_selector("#result:not(.hidden)", timeout=30000)
            d = next(up.glob("*/003_*"))
            pdf = pdfium.PdfDocument(str(next(d.glob("*Generated_RushID_Layout.pdf"))))
            w, h = pdf[0].get_size()
            check(len(pdf) == 1 and abs(w - 127 * 72 / 25.4) < 1 and abs(h - 177.8 * 72 / 25.4) < 1, f"2 Package-C sets fit one 5R sheet ({len(pdf)} page, {w:.0f}x{h:.0f}pt)")
            check(json.load(open(d / "order.json"))["total"] == 60.0, "Rush ID total 60.00")

            print("typed document")
            page.click("#againBtn")
            page.fill("#custName", "Lola")
            page.click("#typedBtn")
            page.fill("#tdText", "Dear Sir,\n\nThis is a typed letter.\n" * 10)
            until(page, "() => document.getElementById('tdInfo').textContent.includes('page')", 15)
            check(page.inner_text("#tdTotal") == "\u20b18.00", f"typed price {page.inner_text('#tdTotal')}")
            page.click("#tdSend")
            page.wait_for_selector("#result:not(.hidden)", timeout=20000)
            d = next(up.glob("*/004_*"))
            check((d / "Typed_Document.pdf").exists() and (d / "Typed_Document.docx").exists(), "typed document produced PDF + DOCX")

            print("repair request form")
            page.goto(base + "/repair")
            page.fill("#custName", "Ben"); page.fill("#custPhone", "09171234567"); page.fill("#d_issue", "Laptop will not turn on")
            page.click("#submitBtn")
            page.wait_for_selector("#result:not(.hidden)", timeout=15000)
            check(page.inner_text("#resId").startswith("R-"), f"repair ticket {page.inner_text('#resId')}")

            print("staff dashboard")
            page.goto(base + "/admin")
            page.fill("#pin", "000000"); page.click("#loginForm button")
            page.wait_for_selector("#loginErr:not(.hidden)")
            check(True, "wrong PIN shows an error")
            page.fill("#pin", "123456"); page.click("#loginForm button")
            page.wait_for_selector("#app:not(.hidden)")
            until(page, "() => document.querySelectorAll('#olist li').length >= 5", 10.0)
            check(page.locator("#olist li").count() == 5, f"5 orders listed ({page.locator('#olist li').count()})")
            page.locator(f'#olist li[data-id="{oid}"]').click()
            page.wait_for_selector("#stChips")
            page.click('#stChips [data-st="Printing"]')
            until(page, "() => document.querySelector('#detail .badge').textContent === 'Printing'", 15)
            page.fill("#ePub", "Almost done!"); page.check("#ePaid"); page.select_option("#ePay", "GCash"); page.click("#eSave")
            until(page, "() => document.querySelector('.toast')", 15)
            r = json.load(open(next(up.glob("*/001_*/order.json"))))
            check(r["status"] == "Printing" and r["paid"] and r["payment_method"] == "GCash" and r["public_note"] == "Almost done!", "staff changes saved to order.json")
            page.click('[data-tab="summary"]'); page.wait_for_selector("#sOut table")
            check("Sales" in page.inner_text("#tab-summary"), "sales summary renders")
            page.click('[data-tab="tools"]'); page.wait_for_selector("#toolStatus .alert")
            check("connected" in page.inner_text("#toolStatus"), "tools tab shows storage connected")

            print("customer sees the staff update")
            page.goto(f"{base}/status?o={oid}&c={code}")
            page.wait_for_selector("#out:not(.hidden)")
            check("Almost done!" in page.inner_text("#oNote") and "Paid" in page.inner_text("#oPay"), "status page shows staff message and paid state")
            br.close()
    finally:
        out = srv.stop()
        if "Traceback" in out:
            print("SERVER LOG:\n", out[-1500:]); failures.append("server traceback")
    problems[:] = [x for x in problems if not ("/api/admin/login" in x or "qr.svg" in x or x.startswith("console error: Failed to load resource"))]   # expected: wrong-PIN test, optional segno
    print("\nbrowser problems seen:", len(problems))
    for pr in problems[:25]:
        print("   ", pr)
    if problems:
        failures.append("browser console/network problems")
    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
