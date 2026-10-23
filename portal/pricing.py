"""Document analysis (page count + colour detection) and server-side price calculation.
The browser shows an estimate, but THIS module decides the real total."""
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageChops

from . import docgen
from .layout import find_layout, per_page

_PDFIUM_LOCK = threading.Lock()          # pdfium is not thread-safe
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif"}
OFFICE_EXT = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf"}


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


# ---------------------------------------------------------------- colour detection
def page_is_color(img, sat_thr=40, val_thr=60, min_ratio=0.0005):
    """True when a visible share of pixels is clearly coloured.  Tiny JPEG noise or a
    faint scanner tint stays black & white; a logo or coloured heading counts as colour."""
    rgb = img.convert("RGB")
    _, s, v = rgb.convert("HSV").split()
    sm = s.point(lambda x: 255 if x > sat_thr else 0)
    vm = v.point(lambda x: 255 if x > val_thr else 0)
    colored = ImageChops.multiply(sm, vm).histogram()[255]
    ratio = colored / float(rgb.width * rgb.height or 1)
    return ratio >= min_ratio, ratio


def analyze_pdf(path, cfg) -> dict:
    lim, an = cfg["limits"], cfg["analysis"]
    cap, tlimit = lim["max_pdf_pages_analyze"], lim["analyze_time_limit_s"]
    start = time.time()
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        total = len(doc)
        doc.close()
    bw = color = done = 0
    for i in range(total):
        if i >= cap or time.time() - start > tlimit:
            break
        with _PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(path))
            try:
                page = doc[i]
                pil = page.render(scale=0.5).to_pil()
                page.close()
            finally:
                doc.close()
        is_col, _ = page_is_color(pil, an["sat_threshold"], an["val_threshold"], an["min_ratio"])
        color += 1 if is_col else 0
        bw += 0 if is_col else 1
        done += 1
    complete = done == total
    bw += total - done                      # pages we did not get to are assumed B&W
    return {"pages": total, "bw_pages": bw, "color_pages": color, "complete": complete}


def pdf_page_count(path) -> int:
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        n = len(doc)
        doc.close()
    return n


# ---------------------------------------------------------------- other file types
def _office_pages_from_xml(path, ext):
    try:
        with zipfile.ZipFile(path) as z:
            if ext in ("docx", "pptx"):
                xml = z.read("docProps/app.xml").decode("utf-8", "ignore")
                tag = "Pages" if ext == "docx" else "Slides"
                m = re.search(rf"<{tag}>(\d+)</{tag}>", xml)
                return int(m.group(1)) if m else None
            if ext in ("odt", "odp"):
                xml = z.read("meta.xml").decode("utf-8", "ignore")
                m = re.search(r'page-count="(\d+)"', xml)
                return int(m.group(1)) if m else None
    except Exception:
        return None
    return None


def _libreoffice_bin(cfg):
    mode = cfg["analysis"]["use_libreoffice"]
    if mode is False:
        return None
    return shutil.which("soffice") or shutil.which("libreoffice")


def _convert_to_pdf(path, cfg, timeout=60):
    exe = _libreoffice_bin(cfg)
    if not exe:
        return None
    tmp = tempfile.mkdtemp(prefix="jcm_lo_")
    try:
        cmd = [exe, "--headless", "--norestore", f"-env:UserInstallation=file://{tmp}/profile",
               "--convert-to", "pdf", "--outdir", tmp, str(path)]
        subprocess.run(cmd, timeout=timeout, capture_output=True, check=False)
        out = Path(tmp) / (Path(path).stem + ".pdf")
        if out.exists():
            res = analyze_pdf(out, cfg)
            return res
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return None


def analyze_file(path, filename, cfg) -> dict:
    """Describe one uploaded file: how many pages, how many colour / B&W.
    status: ok = measured, estimated = best guess, unknown = staff must check."""
    ext = ext_of(filename)
    base = {"name": filename, "kind": "other", "pages": 1, "bw_pages": 1, "color_pages": 0,
            "status": "unknown", "note": ""}
    try:
        if ext == "pdf":
            r = analyze_pdf(path, cfg)
            base.update(kind="pdf", pages=r["pages"], bw_pages=r["bw_pages"],
                        color_pages=r["color_pages"],
                        status="ok" if r["complete"] else "estimated",
                        note="" if r["complete"] else "Very long PDF - colour only partly checked")
        elif ext in IMAGE_EXT:
            base.update(kind="image", pages=1, bw_pages=0, color_pages=1, status="ok",
                        note="Photos are charged as 1 colour page each")
        elif ext in ("txt", "csv"):
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            n = docgen.count_pages(text)
            base.update(kind="text", pages=n, bw_pages=n, color_pages=0, status="estimated")
        elif ext in OFFICE_EXT:
            r = _convert_to_pdf(path, cfg)
            if r:
                base.update(kind="office", pages=r["pages"], bw_pages=r["bw_pages"],
                            color_pages=r["color_pages"], status="ok" if r["complete"] else "estimated")
            else:
                n = _office_pages_from_xml(path, ext)
                if n:
                    base.update(kind="office", pages=n, bw_pages=n, color_pages=0, status="estimated",
                                note="Page count read from the file; colour will be checked at the counter")
                else:
                    base.update(kind="office", status="unknown",
                                note="Page count unknown - the counter will confirm the price")
        else:
            base.update(note="File type not recognised - the counter will confirm the price")
    except Exception as e:                         # a broken file must not break the order
        base.update(status="unknown", note=f"Could not read this file ({type(e).__name__})")
    base["needs_review"] = base["status"] != "ok"
    return base


# ---------------------------------------------------------------- price calculation
def peso(n) -> str:
    return f"PHP {n:,.2f}"


def doc_total(analyses, color_mode, copies, paper, cfg):
    rates = cfg["rates"]
    bw = sum(a["bw_pages"] for a in analyses)
    col = sum(a["color_pages"] for a in analyses)
    pages = bw + col
    if color_mode == "force_bw":
        bw, col = pages, 0
    elif color_mode == "force_color":
        bw, col = 0, pages
    sur = float(rates.get("doc_paper_surcharge", {}).get(paper, 0))
    per_copy = bw * rates["doc_bw"] + col * rates["doc_color"] + pages * sur
    total = per_copy * copies
    lines = []
    if bw:
        lines.append(f"B&W pages: {bw} x {peso(rates['doc_bw'])}")
    if col:
        lines.append(f"Color pages: {col} x {peso(rates['doc_color'])}")
    if sur:
        lines.append(f"{paper} paper: {pages} x {peso(sur)}")
    if copies > 1:
        lines.append(f"Copies: x{copies}")
    return {"total": round(total, 2), "pages": pages, "bw": bw, "color": col, "lines": lines}


def photo_rate(cfg, paper_type, paper_size) -> float:
    return float(cfg["photo_paper"][paper_type]["rates"][paper_size])


def photo_pages_expected(cfg, layout_key, paper_size, n_images, copies) -> int:
    lay = find_layout(cfg, layout_key)
    ps = cfg["paper_sizes"][paper_size]
    pp = per_page(lay, ps["w"], ps["h"], cfg["page"]["margin_mm"], cfg["page"]["gap_mm"])
    return max(1, math.ceil(n_images * copies / pp))


def photo_total(cfg, paper_type, paper_size, pages):
    rate = photo_rate(cfg, paper_type, paper_size)
    return {"total": round(rate * pages, 2), "pages": pages,
            "lines": [f"{pages} sheet(s) x {peso(rate)} ({cfg['photo_paper'][paper_type]['label']}, {paper_size})"]}


def rush_package(cfg, key):
    for p in cfg["rush_id"]["packages"]:
        if p["key"] == key:
            return p
    return None


def typed_total(cfg, pages, copies):
    fee, rate = cfg["rates"]["typing_fee"], cfg["rates"]["doc_bw"]
    total = fee + pages * rate * copies
    return {"total": round(total, 2), "pages": pages,
            "lines": [f"Typing fee: {peso(fee)}", f"Printing: {pages} page(s) x {copies} x {peso(rate)}"]}
