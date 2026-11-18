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
import shlex
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import auth, backup, catalog, db, docgen, netsec, pricing, settings, sharing, storage

STATIC = settings.BASE_DIR / "static"
log = logging.getLogger("jcm")
ID_RE = re.compile(r"^(?:[RS]-)?\d{8}-\d{3,4}$")
CODE_ALPHABET = sharing.CODE_ALPHABET
LEGACY_OFFICE = {"doc", "xls", "ppt"}
LAYOUT_PDFS = {"photo": "Generated_Photo_Layout.pdf", "rushid": "Generated_RushID_Layout.pdf"}


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def jerr(status: int, detail: str):
    return JSONResponse({"detail": detail}, status_code=status)


PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for")


def _is_private(ip: str) -> bool:
    return netsec.is_private(ip)


def via_proxy(request: Request) -> bool:
    """True when the request came through Cloudflare Tunnel / a reverse proxy (i.e. from the internet).
    Cloudflare always sets these headers and overwrites any the visitor tried to send."""
    return any(request.headers.get(h) for h in PROXY_HEADERS)


def peer_of(request: Request) -> str:
    return request.client.host if request.client else "?"


def server_port(request: Request) -> int:
    """The port this request arrived on (so the tunnel's port can be walled off from the staff area)."""
    srv = request.scope.get("server")
    try:
        return int(srv[1]) if srv else 0
    except (TypeError, ValueError, IndexError):
        return 0


def client_ip(request: Request) -> str:
    """The real visitor address. Behind cloudflared every request arrives from 127.0.0.1, so without this
    all visitors would share one rate-limit bucket. Proxy headers are believed ONLY from a trusted proxy
    (network.trusted_proxies, default this PC), so a customer on the shop Wi-Fi cannot fake an address."""
    cfg = settings.get_config()
    return netsec.real_client_ip(peer_of(request), lambda n: request.headers.get(n, ""), via_proxy(request),
                                 cfg["network"].get("trusted_proxies"))


def is_https(request: Request) -> bool:
    h = request.headers
    return request.url.scheme == "https" or h.get("x-forwarded-proto", "").lower() == "https" or '"https"' in h.get("cf-visitor", "")


def lan_only_ok(request: Request, networks) -> bool:
    """The rule for the staff area and the POS connection. ALL of these must hold:
       1. no proxy / tunnel headers,   2. not on a public (tunnel) port,
       3. the connecting address is a private/loopback one (admin.require_private_peer),
       4. if allowed_networks is set, the address is inside one of those networks."""
    cfg = settings.get_config()
    if via_proxy(request):
        return False
    if server_port(request) in settings.public_ports(cfg):
        return False
    peer = peer_of(request)
    if cfg["admin"].get("require_private_peer", True) and not netsec.is_private(peer):
        return False
    return netsec.peer_in(peer, networks)


def admin_allowed(request: Request) -> bool:
    cfg = settings.get_config()
    if cfg["admin"].get("allow_remote", False):
        return True
    return lan_only_ok(request, settings.admin_networks(cfg))


REMOTE_MSG = "The staff area is only available on the shop network."


def require_admin(request: Request) -> dict:
    """-> {'name','role'} of the logged-in staff member."""
    if not admin_allowed(request):
        raise ApiError(403, REMOTE_MSG)
    sess = auth.valid_token(request.cookies.get(auth.COOKIE, ""))
    if not sess:
        raise ApiError(401, "Please log in.")
    return sess


def require_owner(request: Request) -> dict:
    sess = require_admin(request)
    if sess["role"] != "owner":
        raise ApiError(403, "Only an owner can do this.")
    return sess


# ------------------------------------------------------------------ middleware
class SecurityMiddleware:
    """Request-size guard (header AND real byte count) + CSRF check + security headers + no caching of API/admin."""

    CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
           "script-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
           "frame-ancestors 'self'")
    UPLOAD_PATHS = ("/api/submit_order", "/api/estimate", "/api/admin/catalog/image", "/share")

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _cross_site(h) -> bool:
        """Browser requests from another website (CSRF). The POS and scripts send no Origin and are unaffected."""
        if h.get(b"sec-fetch-site", b"").lower() == b"cross-site":
            return True
        origin = h.get(b"origin", b"").decode("latin-1")
        if not origin or origin == "null":
            return origin == "null"
        host = urlparse(origin).netloc.lower()
        allowed = {h.get(b"host", b"").decode("latin-1").lower()}
        pub = settings.get_config()["shop"].get("public_url", "")
        if pub:
            allowed.add(urlparse(pub).netloc.lower())
        return host not in allowed

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hdrs = dict(scope["headers"])
        cfg = settings.get_config()
        lim = cfg["limits"]
        tunnel = any(h in hdrs for h in (b"cf-connecting-ip", b"cf-ray", b"x-forwarded-for"))
        https = (scope.get("scheme") == "https" or hdrs.get(b"x-forwarded-proto", b"").lower() == b"https"
                 or b'"https"' in hdrs.get(b"cf-visitor", b""))
        path = scope["path"]
        st = {"seen": 0, "exceeded": False, "started": False, "swallow": False}

        async def base_send(message):
            if message["type"] == "http.response.start":
                st["started"] = True
                h = MutableHeaders(scope=message)
                h["X-Content-Type-Options"] = "nosniff"
                h["Referrer-Policy"] = "no-referrer"
                h["Content-Security-Policy"] = self.CSP
                h["X-Frame-Options"] = "SAMEORIGIN"
                h["Permissions-Policy"] = "geolocation=(), microphone=(), payment=(), usb=()"
                if https and cfg.get("security", {}).get("hsts", True):
                    h["Strict-Transport-Security"] = "max-age=31536000"
                if path.startswith("/api/") or path.startswith("/admin"):
                    h["Cache-Control"] = "no-store"
            await send(message)

        async def reply(status, detail):
            resp = JSONResponse({"detail": detail}, status_code=status, headers={"Connection": "close"} if status == 413 else None)
            await resp(scope, receive, base_send)

        async def guarded_send(message):
            if st["swallow"]:
                return
            if st["exceeded"] and message["type"] == "http.response.start":
                st["swallow"] = True                 # whatever the app wanted to say, the real answer is 413
                await reply(413, too_big_msg)
                return
            await base_send(message)

        if path in self.UPLOAD_PATHS:
            limit_mb = lim["max_request_mb_tunnel"] if tunnel else lim["max_request_mb"]
            too_big_msg = f"Your upload is too big (limit {limit_mb} MB in total)."
        else:
            limit_mb = min(float(lim.get("max_json_mb", 2)), lim["max_request_mb_tunnel"] if tunnel else lim["max_request_mb"])
            too_big_msg = "That request is too large."
        max_bytes = int(limit_mb * 1024 * 1024)
        cl = hdrs.get(b"content-length")
        if cl and cl.isdigit() and int(cl) > max_bytes:
            return await reply(413, too_big_msg)
        if scope["method"] in ("POST", "PUT", "PATCH", "DELETE") and path != "/share" and self._cross_site(hdrs):
            return await reply(403, "Cross-site request blocked.")

        try:
            await self.app(scope, netsec.limited_receive(receive, max_bytes, st), guarded_send)
        except netsec.BodyTooLarge:
            if not st["started"] and not st["swallow"]:
                await reply(413, too_big_msg)


# ------------------------------------------------------------------ pages
def page(filename: str):
    async def handler(request: Request):
        return FileResponse(STATIC / filename)
    return handler


async def admin_page(request: Request):
    if not admin_allowed(request):
        return HTMLResponse(f"<h1>{REMOTE_MSG}</h1>", status_code=403)
    return FileResponse(STATIC / "admin.html")


async def share_entry(request: Request):
    """The phone's Share button opens /share.  Normally the service worker handles the POST (it keeps shared files
    in the browser); this is the fallback when it is not active yet: keep the text, drop big file uploads."""
    if request.method == "POST":
        text = {}
        try:
            cl = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            cl = 0
        if 0 < cl <= 200_000:
            try:
                async with request.form(max_files=5, max_fields=10) as form:
                    text = {k: str(v)[:1500] for k, v in form.multi_items() if k in ("title", "text", "url") and isinstance(v, str)}
            except Exception:
                text = {}
        return RedirectResponse("/share" + ("?" + urlencode(text) if text else ""), status_code=303)
    return FileResponse(STATIC / "share.html")


async def web_manifest(request: Request):
    shop = settings.get_config()["shop"]
    data = {
        "id": "/", "name": shop["name"], "short_name": shop["short_name"][:12] or "JCM",
        "description": shop["tagline"], "lang": "en", "dir": "ltr",
        "start_url": "/?source=app", "scope": "/", "display": "standalone", "orientation": "portrait",
        "background_color": "#ffffff", "theme_color": "#2f5bea", "categories": ["business", "utilities"],
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Send files to print", "short_name": "Print", "url": "/print", "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]},
            {"name": "Send a Facebook link", "short_name": "Facebook", "url": "/share", "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]},
            {"name": "Check my order", "short_name": "My order", "url": "/status", "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]},
        ],
        # Makes the app appear in the Android Share menu (Facebook, Messenger, Gallery, Files ...)
        "share_target": {
            "action": "/share", "method": "POST", "enctype": "multipart/form-data",
            "params": {"title": "title", "text": "text", "url": "url",
                       "files": [{"name": "files", "accept": [
                           "image/*", "application/pdf", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                           ".odt", ".ods", ".odp", ".rtf", ".txt", ".csv"]}]},
        },
    }
    return JSONResponse(data, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


async def service_worker(request: Request):
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


# ---------------------------------------------------------------- catalog (souvenirs gallery + repair services)
async def api_catalog(request: Request):
    """What customers see: hidden / out-of-stock / switched-off things are already removed."""
    cat = await run_in_threadpool(catalog.load, settings.data_dir())
    return JSONResponse(catalog.public_view(cat), headers={"Cache-Control": "no-cache"})


async def media_file(request: Request):
    name = request.path_params["name"]
    if not catalog.MEDIA_NAME_RE.match(name):
        return jerr(404, "Not found.")
    f = catalog.images_dir(settings.data_dir()) / name
    if not f.is_file():
        return jerr(404, "Not found.")
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def admin_catalog_get(request: Request):
    require_admin(request)
    cat = await run_in_threadpool(catalog.load, settings.data_dir())
    return JSONResponse(cat)


async def admin_catalog_save(request: Request):
    me = require_admin(request)
    try:
        body = await request.json()
        new = await run_in_threadpool(catalog.save, settings.data_dir(), body)
    except catalog.Conflict as e:
        return jerr(409, str(e))
    except (ValueError, TypeError):
        return jerr(400, "Bad request.")
    log.info("catalog saved by %s (revision %s)", me["name"], new[catalog.REV_KEY])
    return JSONResponse(new)


async def admin_catalog_image(request: Request):
    me = require_admin(request)
    try:
        async with request.form(max_files=1, max_fields=2) as form:
            up = form.get("file")
            if up is None or not hasattr(up, "read"):
                return jerr(400, "Choose a picture.")
            raw = await up.read(catalog.MAX_UPLOAD + 1)
        url = await run_in_threadpool(catalog.save_image, settings.data_dir(), raw)
    except ValueError as e:
        return jerr(400, str(e))
    except Exception:
        return jerr(400, "Could not read that picture.")
    log.info("catalog picture added by %s: %s", me["name"], url)
    return JSONResponse({"url": url, "thumb": url.replace(".jpg", "_t.jpg")})


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
    if ext not in allowed or ext in {e.lower().lstrip(".") for e in cfg["limits"].get("blocked_extensions", [])}:
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
    if cfg["limits"].get("deep_file_checks", True):
        deep_ok, why = storage.deep_check(ext, up.file)
        if not deep_ok:
            return False, f"\"{name}\" {why}.", ext
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
    if not ok:
        log.warning("health check: %s", msg)                   # the detail (it names server paths) stays in the log
    return JSONResponse({"ok": ok, "storage": "ok" if ok else "problem", "time": settings.now().isoformat()})


_EST_SEM = threading.BoundedSemaphore(4)       # at most 4 customers' files are analysed at the same time


def _estimate_uploads(ups, cfg):
    if not _EST_SEM.acquire(timeout=30):
        raise ApiError(503, "The shop computer is busy checking other files. Please try again in a moment.")
    try:
        return _estimate_uploads_inner(ups, cfg)
    finally:
        _EST_SEM.release()


def _estimate_uploads_inner(ups, cfg):
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
    if not auth.limiter.allow("est:" + client_ip(request), 90, 60) or not auth.limiter.allow("est:all", 240, 60):
        return jerr(429, "Too many requests - please wait a moment.")
    cfg = settings.get_config()
    async with request.form(max_files=10, max_fields=10) as form:
        ups = [f for f in form.getlist("file") + form.getlist("files") if hasattr(f, "filename") and f.filename]
        if not ups:
            return jerr(400, "No file received.")
        try:
            res = await run_in_threadpool(_estimate_uploads, ups, cfg)
        except ApiError as e:
            return jerr(e.status, e.detail)
        except Exception:
            log.exception("estimate failed")
            return jerr(500, "Could not check this file.")
    return JSONResponse({"files": res})


async def api_typed_estimate(request: Request):
    if not auth.limiter.allow("typed:" + client_ip(request), 120, 60):
        return jerr(429, "Too many requests - please wait a moment.")
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


async def api_quote(request: Request):
    """Price of a document order from the per-file page counts the browser already has (one source of truth)."""
    if not auth.limiter.allow("quote:" + client_ip(request), 240, 60):
        return jerr(429, "Too many requests - please wait a moment.")
    cfg = settings.get_config()
    try:
        b = await request.json()
        files = b.get("files", [])
        if not isinstance(files, list) or len(files) > 100:
            raise ValueError
    except Exception:
        return jerr(400, "Bad request.")
    an = [{k: _int(f.get(k), 0, 0, 100000) for k in ("bw_pages", "half_pages", "tq_pages", "full_pages")}
          for f in files if isinstance(f, dict)]
    mode = b.get("color_mode") if b.get("color_mode") in ("auto", "force_bw", "force_color") else "auto"
    paper = b.get("paper") if b.get("paper") in cfg["paper_sizes"] else "A4"
    res = pricing.doc_total(an, mode, _int(b.get("copies"), 1, 1, cfg["limits"]["max_copies"]), paper, cfg, duplex=_bool(b.get("duplex")))
    res.pop("lines", None)
    return JSONResponse(res)


# ------------------------------------------------------------------ order creation
def _price_document(meta, saved, cfg, folder):
    color_mode = meta.get("color_mode", "auto")
    if color_mode not in ("auto", "force_bw", "force_color"):
        color_mode = "auto"
    copies = _int(meta.get("copies"), 1, 1, cfg["limits"]["max_copies"])
    paper = meta.get("paper") if meta.get("paper") in cfg["paper_sizes"] else "A4"
    duplex = _bool(meta.get("duplex"))
    analyses = [pricing.analyze_file(folder / s["saved"], s["original"], cfg) for s in saved]
    r = pricing.doc_total(analyses, color_mode, copies, paper, cfg, duplex=duplex)
    notes = [f"{a['name']}: {a['note']}" for a in analyses if a.get("needs_review")]
    keys = ("name", "kind", "pages", "bw_pages", "half_pages", "tq_pages", "full_pages", "status")
    opts = {"color_mode": color_mode, "copies": copies, "paper": paper, "duplex": duplex,
            "page_count": r["pages"], "bw_pages": r["bw"], "half_pages": r["half"], "tq_pages": r["tq"],
            "full_pages": r["full"], "per_file": [{k: a[k] for k in keys} for a in analyses]}
    return "Standard Document Print", r["total"], r["lines"], opts, bool(notes), notes


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


def _price_share(links, saved):
    """Links / files shared from Facebook or another app. The portal cannot know what the customer wants
    printed, so the order is always flagged for staff to open the link(s) and quote the price."""
    preset = sharing.preset_for(links, len(saved))
    notes = ["Shared from the phone's Share button: open the link(s) / files, then confirm what to print and the price."]
    notes += sharing.access_hints(links)
    labels = sorted({sharing.service_label(u) for u in links})
    opts = {"shared_from": ", ".join(labels) if labels else "Share button"}
    extra = ""
    if links:
        extra = "LINKS SHARED BY THE CUSTOMER (open them on the shop computer):\n" + "\n".join(f"- [{sharing.service_label(u)}] {u}" for u in links)
    return preset, 0.0, [], opts, True, notes, extra


def _scan_args(cmd: str, path: str) -> list:
    """Split the configured scan command. On Windows shlex keeps the quote marks, which would break subprocess."""
    parts = shlex.split(cmd, posix=(os.name != "nt"))
    if os.name == "nt":
        parts = [x[1:-1] if len(x) >= 2 and x[0] == x[-1] == '"' else x for x in parts]
    return [a.replace("{file}", path) for a in parts]


def _scan_files(folder: Path, saved: list, cfg) -> list:
    """Optional antivirus hook. An 'infected' exit code (limits.scan_infected_codes, default [1]; Windows Defender's
    MpCmdRun.exe uses 2) rejects the order. Any other failure is logged and flagged for staff, and rejects the
    order only when limits.scan_fail_closed is true."""
    lim = cfg["limits"]
    cmd = (lim.get("scan_command") or "").strip()
    if not cmd:
        return []
    bad = {int(x) for x in (lim.get("scan_infected_codes") or [1])}
    strict = bool(lim.get("scan_fail_closed", False))
    notes = []
    for sv in saved:
        args = _scan_args(cmd, str(folder / sv["saved"]))
        try:
            rc = subprocess.run(args, capture_output=True, timeout=lim["scan_timeout_s"]).returncode
        except Exception as e:
            log.error("virus scan could not run for %s: %s", sv["original"], e)
            if strict:
                raise ApiError(503, "The shop's virus scanner is not working right now. Please tell the counter staff.")
            notes.append(f"Not virus-scanned (scanner error): {sv['original']}")
            continue
        if rc in bad:
            log.warning("virus scan flagged %s", sv["original"])
            raise ApiError(400, f"\"{sv['original']}\" was blocked by the virus scan.")
        if rc != 0:
            if strict:
                raise ApiError(503, "The shop's virus scanner is not working right now. Please tell the counter staff.")
            notes.append(f"Not virus-scanned (scanner exit code {rc}): {sv['original']}")
    return notes


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
    links = []
    if kind == "share":
        if not cfg["share"]["enabled"]:
            raise ApiError(400, "Sharing is switched off. Please use the Print page.")
        links = sharing.collect_links(fields.get("shared_text"), meta.get("links"), int(cfg["share"]["max_links"]))
        if not links and not uploads:
            raise ApiError(400, "Please share a link or add at least one file.")
    elif not ticket and kind != "typed" and not uploads:
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

    free_gb = shutil.disk_usage(settings.upload_dir(cfg)).free / 1e9
    if free_gb < cfg["limits"]["min_free_gb"]:
        log.error("order refused: only %.2f GB free on the orders disk", free_gb)
        raise ApiError(503, "The shop computer is almost out of storage right now. Please tell the counter staff.")

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
                       "souvenir": "Souvenir Inquiry", "share": "Shared Items"}[kind]
        folder = date_folder / storage.folder_name(prefix, number, name, provisional)
        folder.mkdir(parents=True, exist_ok=False)

        saved = []
        for i, up in enumerate(uploads, 1):
            sname = f"{i:02d}_{storage.safe_filename(up.filename)}"
            with open(folder / sname, "wb") as out:
                shutil.copyfileobj(up.file, out)
            saved.append({"saved": sname, "original": up.filename})

        scan_notes = _scan_files(folder, saved, cfg)
        extra_instr = ""
        if kind == "document":
            preset, total, lines, opts, review, notes = _price_document(meta, saved, cfg, folder)
        elif kind == "photo":
            preset, total, lines, opts, review, notes = _price_photo(meta, saved, cfg, folder)
        elif kind == "rushid":
            preset, total, lines, opts, review, notes = _price_rush(meta, saved, cfg, folder)
        elif kind == "typed":
            preset, total, lines, opts, review, notes = _price_typed(fields, meta, cfg, folder, saved)
        elif kind == "share":
            preset, total, lines, opts, review, notes, extra_instr = _price_share(links, saved)
        else:
            preset, total, lines, opts, review, notes, extra_instr = _price_ticket(kind, meta, fields)

        notes.extend(scan_notes)
        review = review or bool(scan_notes)
        warnings = [f"{sv['original']}: old Office format (.{pricing.ext_of(sv['original'])}) can carry macros - open it in "
                    "Protected View or print from a PDF copy" for sv in saved if pricing.ext_of(sv["original"]) in LEGACY_OFFICE]
        for sv in saved:
            if pricing.ext_of(sv["original"]) in LEGACY_OFFICE and storage.has_ole_macros(folder / sv["saved"]):
                warnings.append(f"{sv['original']}: CONTAINS MACROS - do not open it in Office; ask the customer for a PDF copy")
                review = True
        pay_ref = re.sub(r"[^A-Za-z0-9 \-]", "", fields.get("pay_ref", "") or "").strip()[:40]
        if extra_instr:
            instructions = (extra_instr + ("\n\nNotes: " + instructions if instructions else "")).strip()
        if kind == "document" and opts.get("duplex"):
            instructions = ("[Two-sided printing requested]\n" + instructions).strip()
        if links:
            warnings.extend(sharing.link_warnings(links))

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
            "public_note": "", "staff_note": "", "history": [{"at": iso, "by": "Customer", "status": status0}],
            "folder": str(folder), "files_deleted": False,
            "warnings": warnings, "customer_pay_ref": pay_ref,
        }
        if links:
            order["links"] = links
            order["links_info"] = sharing.link_info(links)
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


def _status_payload(row, full=True):
    """What the customer may see. full=False (name search) leaves out the price, the staff message and the order number."""
    cat = row["category"]
    steps = [x for x in settings.STATUSES[cat] if x != "Cancelled"]
    d = row["data"] or {}
    out = {
        "category": cat, "kind": row["kind"], "preset": row["preset"],
        "status": row["status"], "steps": steps,
        "step": steps.index(row["status"]) if row["status"] in steps else -1,
        "created": row["created"], "updated": row["updated"],
        "ahead": db.queue_ahead(row["id"]) if row["status"] in ("Pending", "Printing") else 0,
    }
    if full:
        out.update({"id": row["id"], "paid": row["paid"], "total": row["total"],
                    "public_note": d.get("public_note", ""), "first_name": (row["name"] or "").split(" ")[0],
                    "files_deleted": row["files_deleted"], "limited": False})
    else:
        out.update({"first_name": sharing.mask_name(row["name"]), "limited": True})
    return out


def _find_status(q: str):
    cfg = settings.get_config()
    st = cfg["status"]
    code = sharing.normalize_code(q)
    if code:
        rows = db.find_by_code(code)
        if rows:
            return {"mode": "code", "results": [_status_payload(r, True) for r in rows]}
    if not st["name_search"]:
        return {"mode": "code", "results": []}
    tokens = sharing.name_tokens(q)
    if not tokens or len(" ".join(tokens)) < int(st["name_search_min_chars"]):
        return {"mode": "name", "results": [], "too_short": True}
    since = (settings.now() - timedelta(days=int(st["name_search_days"]))).strftime("%Y-%m-%d")
    rows = db.find_by_name(tokens, since, int(st["name_search_max_results"]))
    return {"mode": "name", "results": [_status_payload(r, False) for r in rows]}


async def api_status_find(request: Request):
    """Customer search: ?q=<order code or sender name>."""
    ip = client_ip(request)
    if not auth.limiter.allow("find:" + ip, 40, 60) or not auth.limiter.allow("find:all", 300, 60):
        return jerr(429, "Too many requests - please wait a moment.")
    q = request.query_params.get("q", "").strip()[:60]
    if not q:
        return jerr(400, "Please type your order code or your name.")
    res = await run_in_threadpool(_find_status, q)
    if not res["results"] and not res.get("too_short"):
        # guessing codes / names: only misses count towards a stricter limit
        if not auth.limiter.allow("findmiss:" + ip, 25, 600):
            return jerr(429, "Too many searches that found nothing. Please wait a few minutes.")
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
        "ahead": await run_in_threadpool(db.queue_ahead, row["id"]) if row["status"] in ("Pending", "Printing") else 0,
    })


# ------------------------------------------------------------------ staff API
async def api_login(request: Request):
    if not admin_allowed(request):
        return jerr(403, REMOTE_MSG)
    ip = client_ip(request)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        return jerr(400, "Bad request.")
    name = str(body.get("name", "")).strip()[:30]
    if not auth.limiter.allow("login:" + ip, 12, 300) or not auth.limiter.allow("login:" + ip + ":" + name.casefold(), 6, 600):
        return jerr(429, "Too many attempts. Wait a few minutes and try again.")
    adm = settings.get_config()["admin"]
    lk_key = "acct:" + (name.casefold() or "(shared pin)")
    lk_n, lk_s = int(adm.get("lockout_attempts", 5)), float(adm.get("lockout_minutes", 10)) * 60
    if auth.lockout.locked(lk_key, lk_n, lk_s):         # per ACCOUNT: switching IP address does not help
        log.warning("login refused (locked): %r from %s", name or "shared PIN", ip)
        return jerr(429, "Too many wrong PINs. This login is locked for a few minutes - please wait and try again.")
    allow_shared = adm.get("allow_shared_pin", True)
    sess = await run_in_threadpool(auth.login, name, str(body.get("pin", "")), allow_shared)
    if not sess:
        auth.lockout.fail(lk_key)
        log.warning("failed staff login from %s (%r)", ip, name or "shared PIN")
        return jerr(401, "Wrong name or PIN." if name else "Wrong PIN.")
    auth.lockout.clear(lk_key)
    hours = adm["session_hours"]
    log.info("staff login: %s (%s) from %s", sess["name"], sess["role"], ip)
    resp = JSONResponse({"ok": True, **sess})
    resp.set_cookie(auth.COOKIE, auth.make_token(hours, sess["name"], sess["role"]), max_age=int(hours * 3600),
                    httponly=True, samesite="strict", path="/", secure=is_https(request))
    return resp


async def api_logout(request: Request):
    auth.revoke_session(request.cookies.get(auth.COOKIE, ""))        # the session really ends on the server
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


async def api_me(request: Request):
    sess = auth.valid_token(request.cookies.get(auth.COOKIE, "")) if admin_allowed(request) else None
    return JSONResponse({"ok": bool(sess), **(sess or {})})


async def api_staff_names(request: Request):
    """Names for the login drop-down (shop network only - the staff area is refused from the internet)."""
    if not admin_allowed(request):
        return jerr(403, REMOTE_MSG)
    return JSONResponse({"names": auth.staff_names(),
                         "shared_pin": settings.get_config()["admin"].get("allow_shared_pin", True) or not auth.staff_names()})


async def admin_staff_list(request: Request):
    require_owner(request)
    return JSONResponse({"staff": auth.staff_list(), "shared_pin": settings.get_config()["admin"].get("allow_shared_pin", True)})


async def admin_staff_save(request: Request):
    me = require_owner(request)
    try:
        b = await request.json()
        await run_in_threadpool(auth.set_staff, str(b.get("name", "")), str(b.get("pin", "")), b.get("role", "staff"))
        # a reset PIN ends that person's other sessions (the owner who is doing the reset stays logged in)
        auth.revoke_account(" ".join(str(b.get("name", "")).split())[:30], keep_sid=auth.session_id(request.cookies.get(auth.COOKIE, "")))
    except ValueError as e:
        return jerr(400, str(e))
    except Exception:
        return jerr(400, "Bad request.")
    log.info("staff account saved by %s: %s", me["name"], b.get("name"))
    return JSONResponse({"ok": True, "staff": auth.staff_list()})


async def admin_sessions_revoke_all(request: Request):
    """'Log out everyone else' - for a lost phone or a PIN that may have leaked."""
    me = require_owner(request)
    n = await run_in_threadpool(auth.revoke_all, auth.session_id(request.cookies.get(auth.COOKIE, "")))
    log.warning("all other staff sessions ended by %s (%d session(s))", me["name"], n)
    return JSONResponse({"ok": True, "ended": n})


async def admin_staff_delete(request: Request):
    me = require_owner(request)
    try:
        b = await request.json()
        if str(b.get("name", "")).casefold() == me["name"].casefold():
            return jerr(400, "You cannot remove your own account while logged in.")
        await run_in_threadpool(auth.delete_staff, str(b.get("name", "")))
    except ValueError as e:
        return jerr(400, str(e))
    except Exception:
        return jerr(400, "Bad request.")
    log.info("staff account removed by %s: %s", me["name"], b.get("name"))
    return JSONResponse({"ok": True, "staff": auth.staff_list()})


def _folder_of(row) -> Path:
    """The order's folder, but only if it really lives inside the orders directory (a tampered order.json or
    database row can never point the staff area at some other part of the computer)."""
    if not row or not row.get("folder"):
        return None
    p = Path(row["folder"])
    cfg = settings.get_config()
    if cfg.get("security", {}).get("confine_folders", True):
        base = os.path.normcase(os.path.abspath(settings.upload_dir(cfg))).rstrip("\\/")
        mine = os.path.normcase(os.path.abspath(p))
        if not mine.startswith(base + os.sep):
            log.warning("order folder outside the orders directory ignored: %s", p)
            return None
    return p


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


def _update_order(oid: str, patch: dict, actor: str = "Staff"):
    row = db.get(oid)
    if not row:
        raise ApiError(404, "Order not found.")
    o = row["data"] or {}
    cat = row["category"]
    at = settings.now().isoformat(timespec="seconds")
    hist = o.setdefault("history", [])
    if "status" in patch:
        if patch["status"] not in settings.STATUSES[cat]:
            raise ApiError(400, "Invalid status.")
        if patch["status"] != o.get("status"):
            o["status"] = patch["status"]
            hist.append({"at": at, "by": actor, "status": patch["status"]})
    if "paid" in patch and bool(patch["paid"]) != bool(o.get("paid")):
        o["paid"] = bool(patch["paid"])
        hist.append({"at": at, "by": actor, "change": "Marked PAID" if o["paid"] else "Marked UNPAID"})
    if "payment_method" in patch:
        if patch["payment_method"] not in ("", "Cash", "GCash", "Other"):
            raise ApiError(400, "Invalid payment method.")
        if patch["payment_method"] != o.get("payment_method", ""):
            o["payment_method"] = patch["payment_method"]
            hist.append({"at": at, "by": actor, "change": f"Payment method: {patch['payment_method'] or '-'}"})
    for k in ("staff_note", "public_note"):
        if k in patch and _trim(str(patch[k]), 1000) != o.get(k, ""):
            o[k] = _trim(str(patch[k]), 1000)
            hist.append({"at": at, "by": actor, "change": f"{'Message to customer' if k == 'public_note' else 'Staff note'} updated"})
    for k in ("claimed_by", "pos_ref"):
        if k in patch:
            o[k] = _trim(str(patch[k]), 80)
    if "total" in patch:
        try:
            t = round(float(patch["total"]), 2)
        except (TypeError, ValueError):
            raise ApiError(400, "Invalid price.")
        if t < 0 or t > 10_000_000:
            raise ApiError(400, "Invalid price.")
        if t != o.get("total"):
            hist.append({"at": at, "by": actor, "change": f"Final price {o.get('total', 0):.2f} -> {t:.2f}"})
            o["total"] = t
    del hist[:-60]
    o["updated"] = at
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
    sess = require_admin(request)
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
        res = await run_in_threadpool(_update_order, oid, patch, sess["name"])
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
<hr><p>Check your order status:<br><b>{e(base_url)}/status</b><br>Code: <b>{e(row['code'])}</b> &nbsp; (or search your name)</p>
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


async def admin_backup_info(request: Request):
    require_owner(request)
    p, m = backup.last_backup()
    return JSONResponse({"last": p.name if p else None, "last_at": settings.datetime.fromtimestamp(m, settings.PH_TZ).isoformat(timespec="seconds") if m else None,
                         "count": len(backup.list_backups()), "extra_dir": settings.get_config()["backup"].get("extra_dir", "")})


async def admin_backup_run(request: Request):
    me = require_owner(request)
    p = await run_in_threadpool(backup.run_backup)
    log.info("backup made by %s: %s", me["name"], p.name)
    return JSONResponse({"ok": True, "name": p.name})


async def admin_backup_download(request: Request):
    require_owner(request)
    p, _ = backup.last_backup()
    if not p:
        return jerr(404, "No backup yet.")
    return FileResponse(p, filename=p.name, media_type="application/zip")


# ------------------------------------------------------------------ POS (cash register) API - LAN only
_pos_seen = {"at": None, "ip": ""}


def require_pos(request: Request):
    if not settings.get_config()["integration"]["pos_enabled"]:
        raise ApiError(404, "Not found")
    if not lan_only_ok(request, settings.pos_networks()):   # the POS lives on the shop network, never on the internet
        raise ApiError(403, "The POS connection is only available on the shop network.")
    ip = client_ip(request)
    if not auth.limiter.allow("posfail:ok:" + ip, 600, 60):
        raise ApiError(429, "Too many requests.")
    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if not auth.check_pos_key(token):
        if not auth.limiter.allow("posfail:" + ip, 10, 300):
            raise ApiError(429, "Too many wrong keys. Wait a few minutes.")
        raise ApiError(401, "Wrong or missing POS key.")
    _pos_seen.update(at=settings.now().isoformat(timespec="seconds"), ip=ip)


def _pos_view(row, full=False):
    d = row.get("data") or {}
    out = {"id": row["id"], "category": row["category"], "kind": row["kind"], "customer": row["name"],
           "phone": row["phone"], "request": row["preset"], "status": row["status"], "paid": row["paid"],
           "payment_method": row["pay_method"], "total": row["total"], "computed_total": row["computed_total"],
           "needs_review": row["needs_review"], "files": row["files"], "files_deleted": row["files_deleted"],
           "created": row["created"], "updated": row["updated"], "claimed_by": d.get("claimed_by", ""),
           "pos_ref": d.get("pos_ref", ""), "instructions": d.get("instructions", ""),
           "options": d.get("options", {}), "review_notes": d.get("review_notes", []), "breakdown": d.get("breakdown", []),
           "warnings": d.get("warnings", []), "customer_pay_ref": d.get("customer_pay_ref", "")}
    if full:
        folder = _folder_of(row)
        out["file_list"] = ([{"name": p.name, "size": p.stat().st_size} for p in sorted(folder.iterdir())
                             if p.is_file() and not p.name.endswith(".tmp")] if folder and folder.is_dir() else [])
        out["statuses"] = settings.STATUSES[row["category"]]
    return out


async def pos_ping(request: Request):
    require_pos(request)
    ok, msg = await run_in_threadpool(storage.storage_status)
    cfg = settings.get_config()
    return JSONResponse({"ok": True, "shop": cfg["shop"]["name"], "storage_ok": ok, "storage": msg,
                         "prices_source": cfg["prices_source"], "prices_synced_at": cfg["prices_synced_at"],
                         "time": settings.now().isoformat(timespec="seconds"), "api": 1})


async def pos_orders(request: Request):
    require_pos(request)
    q = request.query_params
    since = q.get("updated_since", "")
    rows, _ = await run_in_threadpool(db.search, "all", q.get("status") or "active", q.get("category") or "print", None,
                                      _int(q.get("limit"), 100, 1, 500), 0)
    if since:
        rows = [r for r in rows if (r["updated"] or "") > since]
    return JSONResponse({"orders": [_pos_view(r) for r in rows], "now": settings.now().isoformat(timespec="seconds")})


async def pos_order(request: Request):
    require_pos(request)
    oid = request.path_params["order_id"]
    row = await run_in_threadpool(db.get, oid) if ID_RE.match(oid) else None
    if not row:
        return jerr(404, "Order not found.")
    return JSONResponse(_pos_view(row, full=True))


async def pos_file(request: Request):
    require_pos(request)
    oid, fname = request.path_params["order_id"], request.path_params["filename"]
    row = await run_in_threadpool(db.get, oid) if ID_RE.match(oid) else None
    folder = _folder_of(row)
    p = storage.resolve_file(folder, fname) if folder and folder.is_dir() else None
    if not p:
        return jerr(404, "File not found (it may have been deleted after the retention period).")
    return FileResponse(p, filename=p.name, content_disposition_type="attachment")


async def pos_patch(request: Request):
    require_pos(request)
    oid = request.path_params["order_id"]
    if not ID_RE.match(oid):
        return jerr(404, "Order not found.")
    try:
        patch = await request.json()
        if not isinstance(patch, dict):
            raise ValueError
    except Exception:
        return jerr(400, "Bad request.")
    actor = "POS" + (": " + _trim(str(patch.get("actor", "")), 40) if patch.get("actor") else "")
    patch = {k: v for k, v in patch.items() if k in ("status", "paid", "payment_method", "total", "public_note", "claimed_by", "pos_ref")}
    try:
        res = await run_in_threadpool(_update_order, oid, patch, actor)
    except ApiError as e:
        return jerr(e.status, e.detail)
    return JSONResponse(res)


async def pos_rates_put(request: Request):
    require_pos(request)
    try:
        body = await request.json()
        saved = await run_in_threadpool(settings.save_pos_rates, body.get("rates", body))
    except ValueError as e:
        return jerr(400, str(e))
    except Exception:
        return jerr(400, "Bad request.")
    log.info("prices updated from the POS (%d values)", len(saved))
    return JSONResponse({"ok": True, "applied": sorted(saved)})


async def pos_rates_get(request: Request):
    require_pos(request)
    cfg = settings.get_config()
    return JSONResponse({"source": cfg["prices_source"], "synced_at": cfg["prices_synced_at"], "rates": cfg["rates"],
                         "analysis": {k: cfg["analysis"][k] for k in ("thresh_bw", "thresh_half", "thresh_three_quarter")},
                         "packages": {p["key"]: p["price"] for p in cfg["rush_id"]["packages"]}})


async def admin_pos(request: Request):
    require_owner(request)
    cfg = settings.get_config()
    return JSONResponse({"enabled": cfg["integration"]["pos_enabled"], "key": auth.pos_key(),
                         "prices_source": cfg["prices_source"], "prices_synced_at": cfg["prices_synced_at"],
                         "last_seen": _pos_seen["at"], "last_ip": _pos_seen["ip"]})


async def admin_pos_rotate(request: Request):
    me = require_owner(request)
    log.warning("POS key rotated by %s", me["name"])
    return JSONResponse({"key": await run_in_threadpool(auth.rotate_pos_key)})


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


def _security_warnings():
    """Plain-English reminders in the log when a protection is not switched on yet."""
    cfg = settings.get_config()
    adm = cfg["admin"]
    if not adm.get("allow_remote") and not settings.public_ports(cfg):
        log.warning("SECURITY: no tunnel port is set. The staff area is protected by tunnel headers only. "
                    "Set JCM_PUBLIC_PORTS=<the port your tunnel points at> (e.g. 1013) so /admin and the POS API are refused there for good.")
    if not adm.get("allow_remote") and not settings.admin_networks(cfg):
        log.warning("SECURITY: admin.allowed_networks is empty - every device on the shop network, customers on the Wi-Fi "
                    "included, can open the staff login. List the staff PC / phone, e.g. [\"127.0.0.1/32\", \"10.0.4.37/32\"].")
    if adm.get("allow_shared_pin", True) and auth.staff_names():
        log.warning("SECURITY: the shared PIN still works next to named accounts. Set admin.allow_shared_pin to false.")
    env_pin = os.environ.get("JCM_ADMIN_PIN", "")
    if env_pin and len(env_pin) < 6:
        log.warning("SECURITY: JCM_ADMIN_PIN is shorter than 6 characters.")


@asynccontextmanager
async def lifespan(app):
    _setup_logging()
    db.init()
    auth._load()
    auth.pos_key()
    _security_warnings()
    ok, msg = storage.storage_status()
    if ok:
        threading.Thread(target=lambda: log.info("indexed %d existing order(s)", db.reindex(settings.upload_dir())),
                         daemon=True).start()
    else:
        log.error("STORAGE PROBLEM: %s - customers cannot place orders until this is fixed", msg)
    threading.Thread(target=_retention_loop, daemon=True).start()
    threading.Thread(target=lambda: backup.backup_loop(log), daemon=True).start()
    log.info("JCM Print Portal ready. Orders go to %s", settings.upload_dir())
    yield


routes = [
    Route("/", page("index.html")), Route("/print", page("print.html")),
    Route("/souvenirs", page("souvenirs.html")), Route("/repair", page("repair.html")),
    Route("/status", page("status.html")), Route("/admin", admin_page),
    Route("/share", share_entry, methods=["GET", "POST"]),
    Route("/manifest.webmanifest", web_manifest), Route("/sw.js", service_worker),
    Route("/api/catalog", api_catalog), Route("/media/{name}", media_file),
    Route("/api/admin/catalog", admin_catalog_get), Route("/api/admin/catalog", admin_catalog_save, methods=["PUT"]),
    Route("/api/admin/catalog/image", admin_catalog_image, methods=["POST"]),
    Route("/favicon.ico", favicon), Route("/health", api_health),
    Route("/api/config", api_config), Route("/api/estimate", api_estimate, methods=["POST"]),
    Route("/api/typed/estimate", api_typed_estimate, methods=["POST"]),
    Route("/api/submit_order", api_submit, methods=["POST"]),
    Route("/api/status", api_status), Route("/api/status/find", api_status_find),
    Route("/api/admin/login", api_login, methods=["POST"]), Route("/api/admin/logout", api_logout, methods=["POST"]),
    Route("/api/admin/me", api_me), Route("/api/admin/staff-names", api_staff_names),
    Route("/api/admin/staff", admin_staff_list, methods=["GET"]), Route("/api/admin/staff", admin_staff_save, methods=["POST"]),
    Route("/api/admin/staff/delete", admin_staff_delete, methods=["POST"]),
    Route("/api/admin/sessions/revoke-all", admin_sessions_revoke_all, methods=["POST"]),
    Route("/api/admin/backup", admin_backup_info, methods=["GET"]), Route("/api/admin/backup", admin_backup_run, methods=["POST"]),
    Route("/api/admin/backup/download", admin_backup_download),
    Route("/api/admin/orders", admin_orders),
    Route("/api/admin/orders/{order_id}", admin_order, methods=["GET"]),
    Route("/api/admin/orders/{order_id}", admin_patch, methods=["PATCH"]),
    Route("/api/admin/orders/{order_id}/file/{filename}", admin_file),
    Route("/api/admin/orders/{order_id}/zip", admin_zip),
    Route("/admin/slip/{order_id}", admin_slip),
    Route("/api/admin/summary", admin_summary), Route("/api/admin/export.csv", admin_export),
    Route("/api/admin/poll", admin_poll), Route("/api/admin/reindex", admin_reindex, methods=["POST"]),
    Route("/api/admin/qr.svg", admin_qr),
    Route("/api/admin/pos", admin_pos), Route("/api/admin/pos/rotate", admin_pos_rotate, methods=["POST"]),
    Route("/api/quote", api_quote, methods=["POST"]),
    Route("/api/pos/ping", pos_ping), Route("/api/pos/orders", pos_orders),
    Route("/api/pos/orders/{order_id}", pos_order, methods=["GET"]),
    Route("/api/pos/orders/{order_id}", pos_patch, methods=["PATCH"]),
    Route("/api/pos/orders/{order_id}/file/{filename}", pos_file),
    Route("/api/pos/rates", pos_rates_get, methods=["GET"]), Route("/api/pos/rates", pos_rates_put, methods=["PUT"]),
    Mount("/static", StaticFiles(directory=str(STATIC)), name="static"),
]


async def api_error_handler(request: Request, exc: ApiError):
    return jerr(exc.status, exc.detail)


app = Starlette(routes=routes, lifespan=lifespan,
                exception_handlers={404: not_found, ApiError: api_error_handler})
app.add_middleware(SecurityMiddleware)
