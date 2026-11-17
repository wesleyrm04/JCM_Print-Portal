"""The portal must quote EXACTLY what the POS will charge. This imports the POS's own pricing and PDF-analysis code
and compares it with the portal's on thousands of random jobs and many random PDF pages.
Needs the POS source folder:  JCM_POS_SRC=/path/to/JCM_PrintPOS-versionN  python tests/test_pos_parity.py
(skips itself when the POS source is not available)"""
import copy, io, os, random, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
POS = Path(os.environ.get("JCM_POS_SRC", ""))
if not (POS / "services" / "pricing.py").exists():
    print("skip: POS source not found (set JCM_POS_SRC)"); sys.exit(0)

os.environ["JCM_DATA_DIR"] = tempfile.mkdtemp()
os.environ["HOME"] = tempfile.mkdtemp()              # the POS creates its data folders under HOME/LOCALAPPDATA on import
sys.path.insert(0, str(POS))
from PIL import Image, ImageDraw
from services.pricing import compute_price, JobOptions            # the POS's own code
from services.pdf_service import analyze_pdf_numpy_strict         # the POS's own analysis
from portal import pricing, settings

fails = []
def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)

random.seed(7)
print("price formula vs POS compute_price")
bad = 0; n = 3000
for i in range(n):
    rates = {"rate_bw": random.choice([1, 2, 3, 3.5]), "rate_doc_half": random.choice([4, 5, 5.5]),
             "rate_doc_three_quarter": random.choice([6, 7.5, 8]), "rate_doc_full": random.choice([8, 10, 12.25]),
             "surcharge_plain": random.choice([0, 0.5]), "surcharge_photopaper": random.choice([5, 10]),
             "surcharge_folio": random.choice([0, 1, 2]), "discount_duplex_per_page": random.choice([0, 0.5, 1]),
             "discount_duplex_photopaper": random.choice([10, 15]), "min_print_fee": random.choice([0, 5, 15]),
             "typing_service_fee": random.choice([0, 5, 7.5])}
    cfg = copy.deepcopy(settings.DEFAULTS)
    settings.apply_pos_rates(cfg, rates)
    cnt = {k: random.randint(0, 9) for k in ("bw", "half", "tq", "full")}
    if sum(cnt.values()) == 0: cnt["bw"] = 1
    copies = random.randint(1, 6); duplex = random.random() < .4; typing = random.random() < .3
    paper = random.choice(["A4", "Letter", "Long"]); ptype = random.choice(["Plain Paper", "Photopaper"])
    summary = {"bw_pages": cnt["bw"], "half_color_pages": cnt["half"], "three_quarter_color_pages": cnt["tq"], "full_color_pages": cnt["full"]}
    pos = float(compute_price(summary, JobOptions(paper_type=ptype, paper_size=paper, duplex=duplex, copies=copies, typing_fee=typing),
                              lambda k, d: rates.get(k, d)).total)
    mine = pricing.doc_price(cnt, "auto", copies, paper, duplex, cfg, paper_type=ptype, typing_fee=typing)["total"]
    if abs(pos - mine) > 1e-9:
        bad += 1
        if bad < 4: print("   mismatch", cnt, copies, duplex, typing, paper, ptype, "POS", pos, "portal", mine)
check(bad == 0, f"{n} random jobs: portal total == POS total ({bad} differences)")

print("rush package price vs POS")
for key, label in (("A", "Package A (1x1 - 6pcs)"), ("B", "Package B (2x2 - 3pcs)"), ("C", "Package C (1x1 4pcs + 2x2 2pcs)")):
    rates = {"rate_package_a": 21, "rate_package_b": 26.5, "rate_package_c": 31}
    cfg = copy.deepcopy(settings.DEFAULTS); settings.apply_pos_rates(cfg, rates)
    pos = float(compute_price(None, JobOptions(rush_package=label, rush_count=3), lambda k, d: rates.get(k, d)).total)
    mine = float(pricing.q2(pricing.D(pricing.rush_package(cfg, key)["price"]) * 3))
    check(pos == mine, f"package {key} x3 sets: POS {pos} == portal {mine}")

print("colour levels vs POS analyze_pdf_numpy_strict")
def page(ink_lines, block_pct, tint=False):
    w, h = 612, 792
    im = Image.new("RGB", (w, h), "white"); d = ImageDraw.Draw(im)
    for y in range(60, 60 + 14 * ink_lines, 14): d.rectangle([50, y, 560, y + 6], fill=(15, 15, 15))
    if block_pct:
        bh = int(h * block_pct); d.rectangle([40, h - bh - 20, 570, h - 20], fill=(210, 40, random.randint(0, 255)))
    return im
pages = [page(random.randint(0, 40), random.choice([0, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9])) for _ in range(40)]
pages += [Image.new("RGB", (612, 792), "white"), Image.new("RGB", (612, 792), (240, 20, 20))]
buf = io.BytesIO(); pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=72)
path = Path(tempfile.mkdtemp()) / "t.pdf"; path.write_bytes(buf.getvalue())
pos = analyze_pdf_numpy_strict(str(path), 2.0, 25.0, 60.0)
mine = pricing.analyze_pdf(path, settings.get_config())
print("   POS   :", pos); print("   portal:", {k: mine[k] for k in ("pages", "bw", "half", "tq", "full")})
check((pos["total"], pos["bw"], pos["half"], pos["three_quarter"], pos["color"]) == (mine["pages"], mine["bw"], mine["half"], mine["tq"], mine["full"]),
      "all 42 pages land in the same colour level as the POS")
check(pos["half"] > 0 and pos["three_quarter"] > 0 and pos["color"] > 0 and pos["bw"] > 0, "the test pages cover all four levels")

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED")); sys.exit(1 if fails else 0)
