"""JCM Print Portal - web app (Starlette).  Works fully offline on the shop LAN."""
import csv
import html
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import auth, db, docgen, pricing, settings, storage

STATIC = settings.BASE_DIR / "static"
log = logging.getLogger("jcm")
ID_RE = re.compile(r"^(?:[RS]-)?\d{8}-\d{3,4}$")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LAYOUT_PDFS = {"photo": "Generated_Photo_Layout.pdf", "rushid": "Generated_RushID_Layout.pdf"}


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def jerr(status: int, detail: str):
    return JSONResponse({"detail": detail}, status_code=status)


PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for")


def _is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback
    except ValueError:
        return False


def via_proxy(request: Request) -> bool:
    """True when the request came through Cloudflare Tunnel / a reverse proxy (i.e. from the internet).
    Cloudflare always sets these headers and overwrites any the visitor tried to send."""
    return any(request.headers.get(h) for h in PROXY_HEADERS)


def client_ip(request: Request) -> str:
    """The real visitor address. Behind cloudflared every request arrives from 127.0.0.1, so without this
    all visitors would share one rate-limit bucket (one person could lock everyone out)."""
    peer = request.client.host if request.client else "?"
    if via_proxy(request) and _is_private(peer):
        ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if ip:
            return ip[:64]
    return peer


def is_https(request: Request) -> bool:
    h = request.headers
    return request.url.scheme == "https" or h.get("x-forwarded-proto", "").lower() == "https" or '"https"' in h.get("cf-visitor", "")


def admin_allowed(request: Request) -> bool:
    return settings.get_config()["admin"].get("allow_remote", False) or not via_proxy(request)


REMOTE_MSG = "The staff area is only available on the shop network."


def require_admin(request: Request):
    if not admin_allowed(request):
        raise ApiError(403, REMOTE_MSG)
    if not auth.valid_token(request.cookies.get(auth.COOKIE, "")):
        raise ApiError(401, "Please log in.")


# ------------------------------------------------------------------ middleware
class SecurityMiddleware:
    """Request-size guard + security headers + no caching of API/admin responses."""

    CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
           "script-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
           "frame-ancestors 'self'")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hdrs = dict(scope["headers"])
        cl = hdrs.get(b"content-length")
        lim = settings.get_config()["limits"]
        tunnel = any(h in hdrs for h in (b"cf-connecting-ip", b"cf-ray", b"x-forwarded-for"))
        limit_mb = lim["max_request_mb_tunnel"] if tunnel else lim["max_request_mb"]
        if cl and cl.isdigit() and int(cl) > limit_mb * 1024 * 1024:
            resp = JSONResponse({"detail": f"Your upload is too big (limit {limit_mb} MB in total)."}, status_code=413)
            return await resp(scope, receive, send)
        path = scope["path"]

        async def send_wrap(message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h["X-Content-Type-Options"] = "nosniff"
                h["Referrer-Policy"] = "no-referrer"
                h["Content-Security-Policy"] = self.CSP
                if path.startswith("/api/") or path.startswith("/admin"):
                    h["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_wrap)


# ------------------------------------------------------------------ pages
def page(filename: str):
    async def handler(request: Request):
        return FileResponse(STATIC / filename)
    return handler


async def admin_page(request: Request):
    if not admin_allowed(request):
        return HTMLResponse(f"<h1>{REMOTE_MSG}</h1>", status_code=403)
    return FileResponse(STATIC / "admin.html")


async def favicon(request: Request):
    f = STATIC / "favicon.ico"
    return FileResponse(f) if f.exists() else Response(status_code=204)


async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return jerr(404, "Not found")
    return FileResponse(STATIC / "404.html", status_code=404)


# ------------------------------------------------------------------ helpers
def _size_of(f) -> int:
    pos = f.tell()
    f.seek(0, os.SEEK_END)
    n = f.tell()
    f.seek(pos)
    return n


def check_upload(up, cfg, allowed=None):
    """-> (ok, reason, ext).  Size, type and 'does the content match the name' checks."""
    allowed = allowed or storage.ALLOWED_EXT
    name = up.filename or ""
    ext = pricing.ext_of(name)
    if ext not in allowed:
        return False, f"\"{name}\" is not a supported file type.", ext
    size = up.size if getattr(up, "size", None) is not None else _size_of(up.file)
    if size == 0:
        return False, f"\"{name}\" is empty.", ext
    max_mb = cfg["limits"]["max_file_mb"]
    if size > max_mb * 1024 * 1024:
        return False, f"\"{name}\" is bigger than {max_mb} MB.", ext
    up.file.seek(0)
    head = up.file.read(2048)
    up.file.seek(0)
    if not storage.sniff_ok(ext, head):
        return False, f"\"{name}\" does not look like a real .{ext} file.", ext
    return True, "", ext


def _int(v, default, lo, hi):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _bool(v) -> bool:
    return v is True or str(v).lower() in ("1", "true", "yes", "on")


def _clean_phone(p: str) -> str:
    return re.sub(r"[^0-9+\-() ]", "", p or "").strip()[:20]


def _trim(s, n):
    return (s or "").strip()[:n]


# ------------------------------------------------------------------ public API
async def api_config(request: Request):
    return JSONResponse(settings.public_config())


async def api_health(request: Request):
    ok, msg = await run_in_threadpool(storage.storage_status)
    return JSONResponse({"ok": ok, "storage": "ok" if ok else msg, "time": settings.now().isoformat()})


def _estimate_uploads(ups, cfg):
    out = []
    for up in ups:
        ok, reason, ext = check_upload(up, cfg)
        if not ok:
            out.append({"name": up.filename, "accepted": False, "reason": reason, "status": "unknown",
                        "needs_review": True, "pages": 1, "bw_pages": 1, "color_pages": 0, "kind": "other"})
            continue
        fd, tmp = tempfile.mkstemp(suffix="." + ext, prefix="jcm_est_")
        try:
            with os.fdopen(fd, "wb") as f:
                shutil.copyfileobj(up.file, f)
            r = pricing.analyze_file(tmp, up.filename, cfg)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        r["accepted"], r["reason"] = True, ""
        out.append(r)
    return out


async def api_estimate(request: Request):
    if not auth.limiter.allow("est:" + client_ip(request), 90, 60):
        return jerr(429, "Too many requests - please wait a moment.")
    cfg = settings.get_config()
    async with request.form(max_files=10, max_fields=10) as form:
        ups = [f for f in form.getlist("file") + form.getlist("files") if hasattr(f, "filename") and f.filename]
        if not ups:
            return jerr(400, "No file received.")
        try:
            res = await run_in_threadpool(_estimate_uploads, ups, cfg)
        except Exception:
            log.exception("estimate failed")
            return jerr(500, "Could not check this file.")
    return JSONResponse({"files": res})


async def api_typed_estimate(request: Request):
    cfg = settings.get_config()
    try:
        body = await request.json()
    except Exception:
        return jerr(400, "Bad request.")
    text = str(body.get("text", ""))[: cfg["limits"]["max_text_chars"]]
    copies = _int(body.get("copies"), 1, 1, cfg["limits"]["max_copies"])
    pages = docgen.count_pages(text)
    res = pricing.typed_total(cfg, pages, copies)
    return JSONResponse({"pages": pages, "chars": len(text), **res})


# ------------------------------------------------------------------ order creation
def _price_document(meta, saved, cfg, folder):
    color_mode = meta.get("color_mode", "auto")
    if color_mode not in ("auto", "force_bw", "force_color"):
        color_mode = "auto"
    copies = _int(meta.get("copies"), 1, 1, cfg["limits"]["max_copies"])
    paper = meta.get("paper") if meta.get("paper") in cfg["paper_sizes"] else "A4"
    analyses = [pricing.analyze_file(folder / s["saved"], s["original"], cfg) for s in saved]
    r = pricing.doc_total(analyses, color_mode, copies, paper, cfg)
    notes = [f"{a['name']}: {a['note']}" for a in analyses if a.get("needs_review")]
    opts = {"color_mode": color_mode, "copies": copies, "paper": paper, "duplex": _bool(meta.get("duplex")),
            "page_count": r["pages"], "bw_pages": r["bw"], "color_pages": r["color"],
            "per_file": [{k: a[k] for k in ("name", "kind", "pages", "bw_pages", "color_pages", "status")} for a in analyses]}
    preset = "Standard Document Print"
    return preset, r["total"], r["lines"], opts, bool(notes), notes


def _price_photo(meta, saved, cfg, folder):
    lay = next((l for l in cfg["layouts"] if l["key"] == meta.get("layout")), None)
    if not lay:
        raise ApiError(400, "Unknown photo layout.")
    paper_size = meta.get("paper_size")
    paper_type = meta.get("paper_type")
    if paper_size not in cfg["paper_sizes"] or paper_type not in cfg["photo_paper"]:
        raise ApiError(400, "Unknown paper size or type.")
    copies = _int(meta.get("copies"), 1, 1, cfg["limits"]["max_copies"])
    pdfs = [s for s in saved if s["original"] == LAYOUT_PDFS["photo"]]
    images = [s for s in saved if pricing.ext_of(s["original"]) in pricing.IMAGE_EXT]
    notes = []
    expected = pricing.photo_pages_expected(cfg, lay["key"], paper_size, len(images), copies) if images else None
    if pdfs:
        pages = pricing.pdf_page_count(folder / pdfs[0]["saved"])
        if expected and pages != expected:
            notes.append(f"Layout PDF has {pages} page(s) but {expected} were expected - please check.")
    elif expected:
        pages = expected
        notes.append("No layout PDF was attached - staff must arrange the photos.")
    else:
        raise ApiError(400, "Please add at least one photo.")
    skipped = meta.get("skipped")
    skipped = [_trim(str(x), 80) for x in skipped[:20]] if isinstance(skipped, list) else []
    if skipped:
        notes.append("NOT in the layout (could not be read on the customer's phone) - place by hand: " + ", ".join(skipped))
    r = pricing.photo_total(cfg, paper_type, paper_size, pages)
    opts = {"layout": lay["key"], "layout_name": lay["name"], "paper_size": paper_size, "paper_type": paper_type,
            "copies": copies, "fit": _bool(meta.get("fit")), "rotate": _bool(meta.get("rotate")),
            "sheets": pages, "photos": len(images), "skipped_photos": skipped}
    return f"Photo Print Layout - {lay['name']}", r["total"], r["lines"], opts, bool(notes), notes


def _price_rush(meta, saved, cfg, folder):
    pkg = pricing.rush_package(cfg, meta.get("package"))
    if not pkg:
        raise ApiError(400, "Unknown Rush ID package.")
    images = [s for s in saved if pricing.ext_of(s["original"]) in pricing.IMAGE_EXT]
    if not images:
        raise ApiError(400, "Please add at least one photo.")
    sets = len(images)
    total = sets * float(pkg["price"])
    notes = [] if any(s["original"] == LAYOUT_PDFS["rushid"] for s in saved) else \
        ["No ID layout PDF was attached - staff must crop and arrange the photos."]
    lines = [f"{pkg['label']}: {sets} set(s) x {pricing.peso(float(pkg['price']))}"]
    return pkg["label"], round(total, 2), lines, {"package": pkg["key"], "sets": sets}, bool(notes), notes


def _price_typed(fields, meta, cfg, folder, saved):
    text = (fields.get("typed_text") or "").replace("\x00", "")
    if not text.strip():
        raise ApiError(400, "Please type the document text.")
    if len(text) > cfg["limits"]["max_text_chars"]:
        raise ApiError(400, f"The text is too long (max {cfg['limits']['max_text_chars']} characters).")
    copies = _int(meta.get("copies"), 1, 1, cfg["limits"]["max_copies"])
    pages = docgen.count_pages(text)
    for name, data in (("Typed_Document.pdf", docgen.make_pdf(text)), ("Typed_Document.docx", docgen.make_docx(text))):
        (folder / name).write_bytes(data)
        saved.append({"saved": name, "original": name})
    (folder / "Typed_Document.txt").write_text(text, encoding="utf-8")
    saved.append({"saved": "Typed_Document.txt", "original": "Typed_Document.txt"})
    r = pricing.typed_total(cfg, pages, copies)
    return "Typed Document (Arial 11 A4)", r["total"], r["lines"], {"pages": pages, "copies": copies, "chars": len(text)}, False, []


TICKET_FIELDS = {
    "repair": [("device_type", "Device"), ("brand_model", "Brand / Model"), ("issue", "Problem"),
               ("password_hint", "Login / password needed"), ("accessories", "Items left with the device")],
    "souvenir": [("item", "Item"), ("quantity", "Quantity"), ("theme", "Theme / Design"),
                 ("event_date", "Needed by"), ("size", "Size / Details")],
}


def _price_ticket(kind, meta, fields):
    details = meta.get("details") if isinstance(meta.get("details"), dict) else {}
    lines = []
    clean = {}
    for key, label in TICKET_FIELDS[kind]:
        v = _trim(str(details.get(key, "")), 500)
        if v:
            clean[key] = v
            lines.append(f"{label}: {v}")
    if kind == "repair" and not clean.get("issue"):
        raise ApiError(400, "Please describe the problem.")
    if kind == "souvenir" and not (clean.get("item") or clean.get("theme")):
        raise ApiError(400, "Please tell us what you would like.")
    label = "Repair Request" if kind == "repair" else "Souvenir Inquiry"
    return label, 0.0, [], clean, False, [], "\n".join(lines)


def process_order(fields: dict, uploads: list, ip: str) -> dict:
    cfg = settings.get_config()
    lim = cfg["limits"]
    meta = {}
    try:
        meta = json.loads(fields.get("order_meta") or "{}")
    except ValueError:
        pass
    if not isinstance(meta, dict):
        meta = {}
    kind = meta.get("kind") or "document"
    if kind not in settings.KIND_CATEGORY:
        raise ApiError(400, "Unknown request type.")
    category = settings.KIND_CATEGORY[kind]
    ticket = kind in ("repair", "souvenir")

    name = _trim(fields.get("customer_name"), 60)
    if not name:
        raise ApiError(400, "Please enter your name.")
    phone = _clean_phone(fields.get("phone"))
    if ticket and len(re.sub(r"\D", "", phone)) < 7:
        raise ApiError(400, "Please enter a mobile number so we can reach you.")
    instructions = _trim(fields.get("instructions"), 1000)

    # ---- validate uploads BEFORE using an order number
    if len(uploads) > lim["max_files_per_order"]:
        raise ApiError(400, f"Maximum {lim['max_files_per_order']} files allowed per order.")
    if ticket and len(uploads) > 5:
        raise ApiError(400, "Please attach at most 5 reference photos.")
    if not ticket and kind != "typed" and not uploads:
        raise ApiError(400, "Please select at least one file to upload.")
    allowed = pricing.IMAGE_EXT if ticket else storage.ALLOWED_EXT
    for up in uploads:
        ok, reason, _ = check_upload(up, cfg, allowed)
        if not ok:
            raise ApiError(400, reason)

    ok, msg = storage.storage_status(cfg)
    if not ok:
        log.error("order refused: %s", msg)
        raise ApiError(503, "The shop's storage is offline right now. Please tell the counter staff.")

    now = settings.now()
    date_str, date_compact = now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d")
    date_folder = settings.upload_dir(cfg) / date_str
    date_folder.mkdir(parents=True, exist_ok=True)
    prefix = settings.CATEGORY_PREFIX[category]
    try:
        number = storage.acquire_number(date_folder, prefix)
    except RuntimeError:
        raise ApiError(500, "Too many orders today - please see the counter.")
    order_id = storage.make_order_id(prefix, date_compact, number)

    folder = None
    try:
        # provisional preset for the folder name; real one is set after pricing
        provisional = {"document": "Standard Document Print", "photo": "Photo Print Layout",
                       "rushid": "Rush ID", "typed": "Typed Document", "repair": "Repair Request",
                       "souvenir": "Souvenir Inquiry"}[kind]
        folder = date_folder / storage.folder_name(prefix, number, name, provisional)
        folder.mkdir(parents=True, exist_ok=False)

        saved = []
        for i, up in enumerate(uploads, 1):
            sname = f"{i:02d}_{storage.safe_filename(up.filename)}"
            with open(folder / sname, "wb") as out:
                shutil.copyfileobj(up.file, out)
            saved.append({"saved": sname, "original": up.filename})

        extra_instr = ""
        if kind == "document":
            preset, total, lines, opts, review, notes = _price_document(meta, saved, cfg, folder)
        elif kind == "photo":
            preset, total, lines, opts, review, notes = _price_photo(meta, saved, cfg, folder)
        elif kind == "rushid":
            preset, total, lines, opts, review, notes = _price_rush(meta, saved, cfg, folder)
        elif kind == "typed":
            preset, total, lines, opts, review, notes = _price_typed(fields, meta, cfg, folder, saved)
        else:
            preset, total, lines, opts, review, notes, extra_instr = _price_ticket(kind, meta, fields)

        if extra_instr:
            instructions = (extra_instr + ("\n\nNotes: " + instructions if instructions else "")).strip()
        if kind == "document" and opts.get("duplex"):
            instructions = ("[Two-sided printing requested]\n" + instructions).strip()

        # rename folder to include the real preset
        final_folder = date_folder / storage.folder_name(prefix, number, name, preset)
        if final_folder != folder:
            folder.rename(final_folder)
            folder = final_folder

        try:
            client_total = float(meta.get("client_total"))
        except (TypeError, ValueError):
            client_total = None
        if client_total is not None and abs(client_total - total) > 0.01 and not ticket:
            notes.append(f"Customer was shown PHP {client_total:,.2f} but the server calculated PHP {total:,.2f}.")

        iso = now.isoformat(timespec="seconds")
        status0 = settings.STATUSES[category][0]
        files = [s["saved"] for s in saved]
        order = {
            "order_id": order_id, "order_number": number, "date": date_str, "time": now.strftime("%I:%M:%S %p"),
            "created": iso, "updated": iso, "kind": kind, "category": category,
            "customer_name": name, "phone": phone, "preset_request": preset, "instructions": instructions,
            "total_files": len(files), "files": files, "status": status0,
            "paid": False, "payment_method": "", "total": round(total, 2), "computed_total": round(total, 2),
            "client_estimate": client_total, "breakdown": lines, "options": opts,
            "needs_review": review, "review_notes": notes,
            "access_code": "".join(secrets.choice(CODE_ALPHABET) for _ in range(6)),
            "public_note": "", "staff_note": "", "history": [{"at": iso, "status": status0}],
            "folder": str(folder), "files_deleted": False,
        }
        storage.write_instructions(folder, order)
        storage.write_json_atomic(folder / "order.json", order)
    except Exception:
        if folder and folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        storage.release_number(date_folder, prefix, number)       # no gaps in the numbering
        raise

    try:
        db.upsert(order)
    except Exception:
        log.exception("could not index %s (order is safe on disk; use Re-scan in the admin page)", order_id)
    log.info("order %s by %s (%s) total=%.2f files=%d from %s", order_id, name, kind, total, len(files), ip)
    return {"status": "success", "message": "Order uploaded successfully!", "order_id": order_id,
            "order_number": number, "date": date_str, "total_files": len(files),
            "access_code": order["access_code"], "total": order["total"], "needs_review": review,
            "category": category, "kind": kind}


async def api_submit(request: Request):
    if not auth.limiter.allow("sub:" + client_ip(request), 20, 60):
        return jerr(429, "Too many orders in a short time - please wait a minute.")
    cfg = settings.get_config()
    max_files = cfg["limits"]["max_files_per_order"] + 10
    try:
        async with request.form(max_files=max_files, max_fields=60, max_part_size=2 * 1024 * 1024) as form:
            fields = {k: v for k, v in form.multi_items() if isinstance(v, str)}
            uploads = [v for v in form.getlist("files") if hasattr(v, "filename") and v.filename]
            try:
                res = await run_in_threadpool(process_order, fields, uploads, client_ip(request))
            except ApiError as e:
                return jerr(e.status, e.detail)
            except Exception:
                log.exception("order failed")
                return jerr(500, "Something went wrong saving your order. Please try again or see the counter.")
    except ApiError as e:
        return jerr(e.status, e.detail)
    except Exception:
        log.exception("could not read upload")
        return jerr(400, "The upload could not be read. Please try again.")
    return JSONResponse(res)


async def api_status(request: Request):
    if not auth.limiter.allow("stat:" + client_ip(request), 40, 60):
        return jerr(429, "Too many requests - please wait a moment.")
    oid = request.query_params.get("id", "").strip().upper()
    code = request.query_params.get("code", "").strip().upper()
    row = await run_in_threadpool(db.get, oid) if ID_RE.match(oid) else None
    if not row or not secrets.compare_digest(code, (row["code"] or "").upper()):
        return jerr(404, "Order not found. Please check the reference and the code.")
    cat = row["category"]
    steps = [s for s in settings.STATUSES[cat] if s != "Cancelled"]
    d = row["data"] or {}
    return JSONResponse({
        "id": row["id"], "category": cat, "kind": row["kind"], "preset": row["preset"],
        "status": row["status"], "steps": steps,
        "step": steps.index(row["status"]) if row["status"] in steps else -1,
        "paid": row["paid"], "total": row["total"], "created": row["created"], "updated": row["updated"],
        "public_note": d.get("public_note", ""), "first_name": (row["name"] or "").split(" ")[0],
        "files_deleted": row["files_deleted"],
    })


# ------------------------------------------------------------------ staff API
async def api_login(request: Request):
    if not admin_allowed(request):
        return jerr(403, REMOTE_MSG)
    ip = client_ip(request)
    if not auth.limiter.allow("login:" + ip, 8, 300):
        return jerr(429, "Too many attempts. Wait a few minutes and try again.")
    try:
        body = await request.json()
    except Exception:
        return jerr(400, "Bad request.")
    if not auth.check_pin(str(body.get("pin", ""))):
        log.warning("failed staff login from %s", ip)
        return jerr(401, "Wrong PIN.")
    hours = settings.get_config()["admin"]["session_hours"]
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, auth.make_token(hours), max_age=int(hours * 3600), httponly=True,
                    samesite="lax", path="/", secure=is_https(request))
    return resp


async def api_logout(request: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


async def api_me(request: Request):
    ok = admin_allowed(request) and auth.valid_token(request.cookies.get(auth.COOKIE, ""))
    return JSONResponse({"ok": ok})


def _folder_of(row) -> Path:
    return Path(row["folder"]) if row and row.get("folder") else None


async def admin_orders(request: Request):
    require_admin(request)
    q = request.query_params
    rows, counts = await run_in_threadpool(
        db.search, q.get("date") or settings.now().strftime("%Y-%m-%d"), q.get("status"), q.get("category"),
        q.get("q"), _int(q.get("limit"), 200, 1, 500), _int(q.get("offset"), 0, 0, 100000))
    ok, msg = await run_in_threadpool(storage.storage_status)
    return JSONResponse({"orders": rows, "counts": counts, "poll": db.poll(),
                         "storage": {"ok": ok, "message": msg}, "statuses": settings.STATUSES})


async def admin_order(request: Request):
    require_admin(request)
    oid = request.path_params["order_id"]
    row = await run_in_threadpool(db.get, oid) if ID_RE.match(oid) else None
    if not row:
        return jerr(404, "Order not found.")
    files = []
    folder = _folder_of(row)
    if folder and folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.is_file() and not p.name.endswith(".tmp"):
                files.append({"name": p.name, "size": p.stat().st_size})
    row["file_list"] = files
    row["statuses"] = settings.STATUSES[row["category"]]
    return JSONResponse(row)


def _update_order(oid: str, patch: dict):
    row = db.get(oid)
    if not row:
        raise ApiError(404, "Order not found.")
    o = row["data"] or {}
    cat = row["category"]
    if "status" in patch:
        if patch["status"] not in settings.STATUSES[cat]:
            raise ApiError(400, "Invalid status.")
        if patch["status"] != o.get("status"):
            o["status"] = patch["status"]
            o.setdefault("history", []).append({"at": settings.now().isoformat(timespec="seconds"), "status": patch["status"]})
    if "paid" in patch:
        o["paid"] = bool(patch["paid"])
    if "payment_method" in patch:
        if patch["payment_method"] not in ("", "Cash", "GCash", "Other"):
            raise ApiError(400, "Invalid payment method.")
        o["payment_method"] = patch["payment_method"]
    for k in ("staff_note", "public_note"):
        if k in patch:
            o[k] = _trim(str(patch[k]), 1000)
    if "total" in patch:
        try:
            t = round(float(patch["total"]), 2)
        except (TypeError, ValueError):
            raise ApiError(400, "Invalid price.")
        if t < 0 or t > 10_000_000:
            raise ApiError(400, "Invalid price.")
        o["total"] = t
    o["updated"] = settings.now().isoformat(timespec="seconds")
    synced = True
    folder = _folder_of(row)
    if folder and folder.is_dir():
        try:
            storage.write_json_atomic(folder / "order.json", o)
        except OSError:
            synced = False
    else:
        synced = False
    db.upsert(o)
    return {"ok": True, "json_synced": synced}


async def admin_patch(request: Request):
    require_admin(request)
    oid = request.path_params["order_id"]
    if not ID_RE.match(oid):
        return jerr(404, "Order not found.")
    try:
        patch = await request.json()
        if not isinstance(patch, dict):
            raise ValueError
    except Exception:
        return jerr(400, "Bad request.")
    try:
        res = await run_in_threadpool(_update_order, oid, patch)
    except ApiError as e:
        return jerr(e.status, e.detail)
    return JSONResponse(res)


async def admin_file(request: Request):
    require_admin(request)
    oid, fname = request.path_params["order_id"], request.path_params["filename"]
    row = await run_in_threadpool(db.get, oid) if ID_RE.match(oid) else None
    folder = _folder_of(row)
    p = storage.resolve_file(folder, fname) if folder and folder.is_dir() else None
    if not p:
        return jerr(404, "File not found (it may have been deleted after the retention period).")
    inline = request.query_params.get("inline") == "1" and pricing.ext_of(p.name) in (pricing.IMAGE_EXT - {"heic", "heif", "tif", "tiff"}) | {"pdf"}
    return FileResponse(p, filename=p.name, content_disposition_type="inline" if inline else "attachment")


async def admin_zip(request: Request):
    require_admin(request)
    oid = request.path_params["order_id"]
    row = await run_in_threadpool(db.get, oid) if ID_RE.match(oid) else None
    folder = _folder_of(row)
    if not folder or not folder.is_dir():
        return jerr(404, "Order folder not found.")
    tmp = await run_in_threadpool(storage.make_zip, folder)
    return FileResponse(tmp, filename=f"{oid}.zip", media_type="application/zip",
                        background=BackgroundTask(os.remove, tmp))


def _slip_html(row, base_url: str) -> str:
    e = html.escape
    d = row["data"] or {}
    cfg = settings.get_config()
    shop = cfg["shop"]
    lines = "".join(f"<li>{e(x)}</li>" for x in d.get("breakdown", []))
    note = f"<p><b>Notes:</b> {e(d.get('instructions', ''))}</p>" if d.get("instructions") else ""
    status_url = f"{base_url}/status?o={row['id']}&c={row['code']}"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Slip {e(row['id'])}</title>
<link rel="stylesheet" href="/static/css/app.css"></head><body class="slip">
<h2>{e(shop['name'])}</h2>
<h1>{e(row['id'])}</h1>
<p>{e(d.get('date', ''))} {e(d.get('time', ''))}</p>
<p><b>{e(row['name'])}</b>{(' - ' + e(row['phone'])) if row['phone'] else ''}</p>
<p>{e(row['preset'])}</p><ul>{lines}</ul>
<p class="slip-total">Total: PHP {row['total']:,.2f}{' (to be confirmed)' if row['needs_review'] else ''}</p>
<p>{'PAID' if row['paid'] else 'UNPAID'} {e(row['pay_method'])}</p>{note}
<hr><p>Check your order status:<br><b>{e(base_url)}/status</b><br>Order: <b>{e(row['id'])}</b> &nbsp; Code: <b>{e(row['code'])}</b></p>
<p class="no-print"><button id="printBtn">Print</button></p>
<script src="/static/js/slip.js"></script></body></html>"""


async def admin_slip(request: Request):
    if not admin_allowed(request):
        return HTMLResponse(REMOTE_MSG, status_code=403)
    if not auth.valid_token(request.cookies.get(auth.COOKIE, "")):
        return RedirectResponse("/admin")
    oid = request.path_params["order_id"]
    row = await run_in_threadpool(db.get, oid) if ID_RE.match(oid) else None
    if not row:
        return HTMLResponse("Order not found", status_code=404)
    base = f"{request.url.scheme}://{request.headers.get('host', '')}"
    return HTMLResponse(_slip_html(row, base))


def _valid_date(s, default):
    return s if s and re.match(r"^\d{4}-\d{2}-\d{2}$", s) else default


async def admin_summary(request: Request):
    require_admin(request)
    today = settings.now().strftime("%Y-%m-%d")
    q = request.query_params
    res = await run_in_threadpool(db.summary, _valid_date(q.get("from"), today), _valid_date(q.get("to"), today))
    return JSONResponse(res)


def _csv_safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


async def admin_export(request: Request):
    require_admin(request)
    today = settings.now().strftime("%Y-%m-%d")
    q = request.query_params
    rows = await run_in_threadpool(db.export_rows, _valid_date(q.get("from"), today), _valid_date(q.get("to"), today))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Order", "Date", "Created", "Type", "Customer", "Phone", "Request", "Status", "Paid", "Payment", "Total", "Files"])
    for r in rows:
        w.writerow([_csv_safe(r["id"]), r["date"], r["created"], r["kind"], _csv_safe(r["name"]), _csv_safe(r["phone"]),
                    _csv_safe(r["preset"]), r["status"], "yes" if r["paid"] else "no", r["pay_method"], r["total"], r["files"]])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="jcm_orders.csv"'})


async def admin_poll(request: Request):
    require_admin(request)
    return JSONResponse(await run_in_threadpool(db.poll))


async def admin_reindex(request: Request):
    require_admin(request)
    n = await run_in_threadpool(lambda: db.reindex(settings.upload_dir()))
    return JSONResponse({"added": n})


async def admin_qr(request: Request):
    require_admin(request)
    try:
        import segno
    except ImportError:
        return jerr(404, "QR codes need the optional 'segno' package (pip install segno).")
    text = request.query_params.get("text") or f"{request.url.scheme}://{request.headers.get('host', '')}/"
    buf = io.BytesIO()
    segno.make(text[:300], error="m").save(buf, kind="svg", scale=8, border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml")


# ------------------------------------------------------------------ retention + startup
def purge_expired():
    cfg = settings.get_config()
    days = cfg["retention"]["days"]
    if not days or days <= 0:
        return 0
    cutoff = (settings.now() - timedelta(days=days)).isoformat(timespec="seconds")
    base = settings.upload_dir(cfg).resolve()
    done = 0
    for r in db.expired(cutoff):
        folder = Path(r["folder"]).resolve() if r["folder"] else None
        if not folder or base not in folder.parents or not folder.is_dir():
            continue
        storage.purge_files(folder)
        row = db.get(r["id"])
        if row and row["data"]:
            o = row["data"]
            o["files_deleted"] = True
            o["purged_at"] = settings.now().isoformat(timespec="seconds")
            try:
                storage.write_json_atomic(folder / "order.json", o)
            except OSError:
                pass
            db.upsert(o)
        done += 1
    if done:
        log.info("retention: removed files of %d old order(s)", done)
    return done


def _retention_loop():
    while True:
        try:
            purge_expired()
        except Exception:
            log.exception("retention job failed")
        time.sleep(6 * 3600)


def _setup_logging():
    root = logging.getLogger("jcm")
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    try:
        fh = RotatingFileHandler(settings.data_dir() / "portal.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass


@asynccontextmanager
async def lifespan(app):
    _setup_logging()
    db.init()
    auth._load()
    ok, msg = storage.storage_status()
    if ok:
        threading.Thread(target=lambda: log.info("indexed %d existing order(s)", db.reindex(settings.upload_dir())),
                         daemon=True).start()
    else:
        log.error("STORAGE PROBLEM: %s - customers cannot place orders until this is fixed", msg)
    threading.Thread(target=_retention_loop, daemon=True).start()
    log.info("JCM Print Portal ready. Orders go to %s", settings.upload_dir())
    yield


routes = [
    Route("/", page("index.html")), Route("/print", page("print.html")),
    Route("/souvenirs", page("souvenirs.html")), Route("/repair", page("repair.html")),
    Route("/status", page("status.html")), Route("/admin", admin_page),
    Route("/favicon.ico", favicon), Route("/health", api_health),
    Route("/api/config", api_config), Route("/api/estimate", api_estimate, methods=["POST"]),
    Route("/api/typed/estimate", api_typed_estimate, methods=["POST"]),
    Route("/api/submit_order", api_submit, methods=["POST"]),
    Route("/api/status", api_status),
    Route("/api/admin/login", api_login, methods=["POST"]), Route("/api/admin/logout", api_logout, methods=["POST"]),
    Route("/api/admin/me", api_me),
    Route("/api/admin/orders", admin_orders),
    Route("/api/admin/orders/{order_id}", admin_order, methods=["GET"]),
    Route("/api/admin/orders/{order_id}", admin_patch, methods=["PATCH"]),
    Route("/api/admin/orders/{order_id}/file/{filename}", admin_file),
    Route("/api/admin/orders/{order_id}/zip", admin_zip),
    Route("/admin/slip/{order_id}", admin_slip),
    Route("/api/admin/summary", admin_summary), Route("/api/admin/export.csv", admin_export),
    Route("/api/admin/poll", admin_poll), Route("/api/admin/reindex", admin_reindex, methods=["POST"]),
    Route("/api/admin/qr.svg", admin_qr),
    Mount("/static", StaticFiles(directory=str(STATIC)), name="static"),
]


async def api_error_handler(request: Request, exc: ApiError):
    return jerr(exc.status, exc.detail)


app = Starlette(routes=routes, lifespan=lifespan,
                exception_handlers={404: not_found, ApiError: api_error_handler})
app.add_middleware(SecurityMiddleware)
