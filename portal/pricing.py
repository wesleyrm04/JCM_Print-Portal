"""Document analysis (page count + colour level) and server-side price calculation.

The colour classification and the price formula are the SAME as the POS (JCM PrintOS):
  * a page's colour share = pixels whose max-min RGB spread > 15, as a % of non-white ("inked") pixels
  * <= thresh_bw -> B&W | <= thresh_half -> light | <= thresh_three_quarter -> medium | else full colour
  * price = per-level page rates + paper/folio surcharge, minus the two-sided discount, x copies, min fee
tests/test_pos_parity.py checks this file against the POS's own code, so a quote here equals the POS price.
"""
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium

from . import docgen
from .layout import find_layout, per_page

_PDFIUM_LOCK = threading.Lock()          # pdfium is not thread-safe
_LO_SEM = threading.BoundedSemaphore(1)  # one LibreOffice conversion at a time (it is heavy and parses untrusted files)
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif"}
OFFICE_EXT = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf"}
ZERO = Decimal("0")


def D(x) -> Decimal:
    if x is None or x == "":
        return ZERO
    return x if isinstance(x, Decimal) else Decimal(str(x))


def q2(x) -> Decimal:
    return D(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


# ---------------------------------------------------------------- colour levels (same rule as the POS)
def classify_array(arr, th_bw, th_half, th_tq) -> str:
    """arr: HxWx3 uint8 -> 'bw' | 'half' | 'tq' | 'full'"""
    color_pixels = int(np.sum(np.ptp(arr, axis=2) > 15))
    inked = int(np.sum(np.sum(arr, axis=2) < 750))
    pct = 0.0 if inked == 0 else color_pixels / inked * 100.0
    if pct <= th_bw:
        return "bw"
    if pct <= th_half:
        return "half"
    if pct <= th_tq:
        return "tq"
    return "full"


def analyze_pdf(path, cfg) -> dict:
    lim, an = cfg["limits"], cfg["analysis"]
    cap, tlimit = lim["max_pdf_pages_analyze"], lim["analyze_time_limit_s"]
    max_px = max(1_000_000, int(lim.get("max_render_pixels", 12_000_000)))
    start = time.time()
    res = {"bw": 0, "half": 0, "tq": 0, "full": 0}
    done = 0
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        total = len(doc)
    try:
        for i in range(total):
            if i >= cap or time.time() - start > tlimit:
                break
            with _PDFIUM_LOCK:
                page = doc[i]
                try:
                    # a PDF can claim a page the size of a city block; never render more than max_render_pixels
                    pw, ph = page.get_size()
                    scale = 1.0
                    if pw * ph > max_px:
                        scale = math.sqrt(max_px / float(pw * ph))
                    arr = np.array(page.render(scale=scale).to_pil().convert("RGB"))
                finally:
                    page.close()
            res[classify_array(arr, an["thresh_bw"], an["thresh_half"], an["thresh_three_quarter"])] += 1
            done += 1
    finally:
        with _PDFIUM_LOCK:
            doc.close()
    res["bw"] += total - done                  # pages we did not get to are assumed B&W
    return {"pages": total, "bw": res["bw"], "half": res["half"], "tq": res["tq"], "full": res["full"],
            "complete": done == total}


def pdf_page_count(path) -> int:
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        n = len(doc)
        doc.close()
    return n


# ---------------------------------------------------------------- other file types
def _read_small(z, name, cap=1_000_000):
    """Read one zip member, refusing anything that unpacks to more than `cap` bytes (zip-bomb guard)."""
    info = z.getinfo(name)
    if info.file_size > cap:
        raise ValueError("document metadata is unreasonably large")
    with z.open(info) as fh:
        return fh.read(cap + 1).decode("utf-8", "ignore")


def _office_pages_from_xml(path, ext):
    try:
        with zipfile.ZipFile(path) as z:
            if ext in ("docx", "pptx"):
                xml = _read_small(z, "docProps/app.xml")
                tag = "Pages" if ext == "docx" else "Slides"
                m = re.search(rf"<{tag}>(\d+)</{tag}>", xml)
                return int(m.group(1)) if m else None
            if ext in ("odt", "odp"):
                xml = _read_small(z, "meta.xml")
                m = re.search(r'page-count="(\d+)"', xml)
                return int(m.group(1)) if m else None
    except Exception:
        return None
    return None


def _libreoffice_bin(cfg):
    if cfg["analysis"]["use_libreoffice"] is False:
        return None
    return shutil.which("soffice") or shutil.which("libreoffice")


def _run_killing_tree(cmd, timeout):
    """Run a program; on timeout kill it AND the helper processes it started (soffice.bin outlives soffice)."""
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if os.name != "nt":
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kw)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15, check=False)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        raise


def _convert_to_pdf(path, cfg, timeout=60):
    exe = _libreoffice_bin(cfg)
    if not exe:
        return None
    # one conversion at a time; if the converter is busy for too long, fall back to the quick page-count estimate
    if not _LO_SEM.acquire(timeout=float(cfg["limits"].get("libreoffice_wait_s", 20))):
        return None
    tmp = tempfile.mkdtemp(prefix="jcm_lo_")
    try:
        cmd = [exe, "--headless", "--norestore", "--nologo", f"-env:UserInstallation=file://{tmp}/profile",
               "--convert-to", "pdf", "--outdir", tmp, str(path)]
        _run_killing_tree(cmd, timeout)
        out = Path(tmp) / (Path(path).stem + ".pdf")
        if out.exists():
            return analyze_pdf(out, cfg)
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _LO_SEM.release()
    return None


def _fill(base, pages, bw=0, half=0, tq=0, full=0):
    base.update(pages=pages, bw_pages=bw, half_pages=half, tq_pages=tq, full_pages=full)


def analyze_file(path, filename, cfg) -> dict:
    """How many pages and at which colour level. status: ok = measured, estimated = best guess,
    unknown = staff must check."""
    ext = ext_of(filename)
    base = {"name": filename, "kind": "other", "status": "unknown", "note": ""}
    _fill(base, 1, bw=1)
    try:
        if ext == "pdf":
            r = analyze_pdf(path, cfg)
            _fill(base, r["pages"], r["bw"], r["half"], r["tq"], r["full"])
            base.update(kind="pdf", status="ok" if r["complete"] else "estimated",
                        note="" if r["complete"] else "Very long PDF - colour only partly checked")
        elif ext in IMAGE_EXT:
            _fill(base, 1, full=1)
            base.update(kind="image", status="ok", note="Photos are charged as 1 full-colour page each")
        elif ext in ("txt", "csv"):
            n = docgen.count_pages(Path(path).read_text(encoding="utf-8", errors="replace"))
            _fill(base, n, bw=n)
            base.update(kind="text", status="estimated")
        elif ext in OFFICE_EXT:
            r = _convert_to_pdf(path, cfg)
            if r:
                _fill(base, r["pages"], r["bw"], r["half"], r["tq"], r["full"])
                base.update(kind="office", status="ok" if r["complete"] else "estimated")
            else:
                n = _office_pages_from_xml(path, ext)
                if n:
                    _fill(base, n, bw=n)
                    base.update(kind="office", status="estimated",
                                note="Page count read from the file; colour will be checked at the counter")
                else:
                    base.update(kind="office", note="Page count unknown - the counter will confirm the price")
        else:
            base.update(note="File type not recognised - the counter will confirm the price")
    except Exception as e:                         # a broken file must not break the order
        _fill(base, 1, bw=1)
        base.update(status="unknown", note=f"Could not read this file ({type(e).__name__})")
    base["color_pages"] = base["half_pages"] + base["tq_pages"] + base["full_pages"]      # for display
    base["needs_review"] = base["status"] != "ok"
    return base


# ---------------------------------------------------------------- price calculation (POS formula)
def peso(n) -> str:
    return f"PHP {float(n):,.2f}"


def _counts(analyses):
    return {"bw": sum(int(a.get("bw_pages", 0)) for a in analyses),
            "half": sum(int(a.get("half_pages", 0)) for a in analyses),
            "tq": sum(int(a.get("tq_pages", 0)) for a in analyses),
            "full": sum(int(a.get("full_pages", 0)) for a in analyses)}


def _page_money(r, paper_type, paper_size):
    is_photo = "photo" in (paper_type or "").lower()
    extra = D(r["surcharge_photopaper"] if is_photo else r["surcharge_plain"])
    if "long" in (paper_size or "").lower() or "folio" in (paper_size or "").lower():
        extra += D(r["surcharge_folio"])
    return is_photo, extra


def doc_price(counts: dict, color_mode, copies, paper, duplex, cfg, paper_type="Plain Paper", typing_fee=False):
    """counts: {'bw','half','tq','full'} pages for ONE copy. Returns a dict with Decimal-exact money."""
    r = cfg["rates"]
    bw, half, tq, full = (int(counts.get(k, 0)) for k in ("bw", "half", "tq", "full"))
    pages = bw + half + tq + full
    if color_mode == "force_bw":
        bw, half, tq, full = pages, 0, 0, 0
    elif color_mode == "force_color":
        bw, half, tq, full = 0, 0, 0, pages
    copies = max(1, int(copies or 1))
    is_photo, extra = _page_money(r, paper_type, paper)
    color_base = (half * (D(r["doc_half"]) + extra) + tq * (D(r["doc_three_quarter"]) + extra)
                  + full * (D(r["doc_full"]) + extra))
    page_total = bw * (D(r["doc_bw"]) + extra) + color_base
    duplex_off = ZERO
    if duplex:
        sheets = pages // 2
        per_sheet = D(r["duplex_discount_photo"] if is_photo else r["duplex_discount_page"])
        new_total = max(ZERO, page_total - sheets * per_sheet)
        duplex_off = (page_total - new_total) * copies
        page_total = new_total
    base = page_total * copies
    fee = ZERO
    if typing_fee:
        fee = D(r["typing_fee"])
        base += fee
    final = max(ZERO, base)
    min_fee = D(r["min_print_fee"])
    if ZERO < final < min_fee:
        final = min_fee
    return {"total": float(q2(final)), "pages": pages, "bw": bw, "half": half, "tq": tq, "full": full,
            "copies": copies, "extra": float(extra), "duplex_off": float(q2(duplex_off)), "fee": float(fee),
            "rates": {k: float(r[k]) for k in ("doc_bw", "doc_half", "doc_three_quarter", "doc_full")}}


def describe(res: dict) -> list:
    """Plain-English breakdown lines for staff (customers get the same numbers localised by the browser)."""
    r, ex = res["rates"], res["extra"]
    out = []
    for key, rk, label in (("bw", "doc_bw", "B&W pages"), ("half", "doc_half", "Light color pages"),
                           ("tq", "doc_three_quarter", "Medium color pages"), ("full", "doc_full", "Full color pages")):
        if res[key]:
            out.append(f"{label}: {res[key]} x {peso(r[rk] + ex)}")
    if res["duplex_off"]:
        out.append(f"Two-sided discount: -{peso(res['duplex_off'])}")
    if res["fee"]:
        out.append(f"Typing fee: {peso(res['fee'])}")
    if res["copies"] > 1:
        out.append(f"Copies: x{res['copies']}")
    return out


def doc_total(analyses, color_mode, copies, paper, cfg, duplex=False):
    res = doc_price(_counts(analyses), color_mode, copies, paper, duplex, cfg)
    res["lines"] = describe(res)
    return res


def photo_rate(cfg, paper_type, paper_size) -> float:
    """Price of one printed photo sheet. plain/photopaper are priced like the POS prices a full-colour page."""
    spec = cfg["photo_paper"][paper_type]
    base = spec.get("base", "custom")
    if base in ("plain", "photopaper"):
        res = doc_price({"full": 1}, "auto", 1, paper_size, False, cfg,
                        paper_type="Photopaper" if base == "photopaper" else "Plain Paper")
        return res["total"]
    return float(spec["rates"][paper_size])


def photo_pages_expected(cfg, layout_key, paper_size, n_images, copies) -> int:
    lay = find_layout(cfg, layout_key)
    ps = cfg["paper_sizes"][paper_size]
    pp = per_page(lay, ps["w"], ps["h"], cfg["page"]["margin_mm"], cfg["page"]["gap_mm"])
    return max(1, math.ceil(n_images * copies / pp))


def photo_total(cfg, paper_type, paper_size, pages):
    rate = photo_rate(cfg, paper_type, paper_size)
    return {"total": float(q2(D(rate) * pages)), "pages": pages,
            "lines": [f"{pages} sheet(s) x {peso(rate)} ({cfg['photo_paper'][paper_type]['label']}, {paper_size})"]}


def rush_package(cfg, key):
    for p in cfg["rush_id"]["packages"]:
        if p["key"] == key:
            return p
    return None


def typed_total(cfg, pages, copies):
    res = doc_price({"bw": pages}, "auto", copies, "A4", False, cfg, typing_fee=True)
    lines = describe(res)
    return {"total": res["total"], "pages": pages, "lines": lines}
