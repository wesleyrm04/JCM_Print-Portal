"""Configuration. Everything the shop owner may want to change lives in config.json
(prices, layouts, shop info, limits).  Missing keys fall back to DEFAULTS below."""
import copy
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("JCM_CONFIG", BASE_DIR / "config.json"))

# Philippines has no daylight saving, so a fixed UTC+8 offset is always correct and
# needs no tzdata package (works on minimal Linux and on Windows).
PH_TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    return datetime.now(PH_TZ)


STATUSES = {
    "print": ["Pending", "Printing", "Ready for Pickup", "Completed", "Cancelled"],
    "repair": ["Received", "Diagnosing", "Waiting for Approval", "Repairing",
               "Ready for Pickup", "Completed", "Cancelled"],
    "souvenir": ["Inquiry Received", "Quoted", "In Production", "Ready for Pickup",
                 "Completed", "Cancelled"],
}
CLOSED_STATUSES = ("Completed", "Cancelled")
CATEGORY_PREFIX = {"print": "", "repair": "R", "souvenir": "S"}
KIND_CATEGORY = {"document": "print", "photo": "print", "rushid": "print",
                 "typed": "print", "repair": "repair", "souvenir": "souvenir",
                 "share": "print"}        # share = links / files shared from Facebook or another app

DEFAULTS = {
    "shop": {
        "name": "JCM Prints and Computer Services",
        "short_name": "JCM Prints",
        "tagline": "Send your documents & photos straight to the counter - no internet needed.",
        "phone": "", "email": "", "address": "", "hours": "",
        "facebook": "https://facebook.com/jcmgcs",
        "public_url": "",          # e.g. https://portal.linkora.top  (shown on the counter poster)
    },
    "storage": {
        "upload_dir": "/mnt/nas_spool/customer_orders",
        # Orders are refused (instead of silently landing on the server's own disk)
        # when this path is not a mounted drive.  Set to "" to disable the check.
        "mount_point": "/mnt/nas_spool",
        "data_dir": "data",
    },
    "limits": {
        "max_file_mb": 50, "max_files_per_order": 50, "max_request_mb": 600,
        # Cloudflare's free plan rejects any single request over 100 MB, so uploads that arrive through
        # the tunnel are limited a little below that (shop Wi-Fi uploads keep the larger limit).
        "max_request_mb_tunnel": 95,
        "max_pdf_pages_analyze": 300, "analyze_time_limit_s": 25,
        "max_text_chars": 20000, "max_copies": 100,
        "min_free_gb": 1.0,                 # refuse new orders when the orders disk has less free space
        "blocked_extensions": [],           # e.g. ["doc","xls","ppt"] to refuse old macro-capable Office files
        # Optional virus scan of every upload, e.g. "clamscan --no-summary {file}" (exit code 0 = clean).
        "scan_command": "", "scan_timeout_s": 60,
        # v6 security limits
        "scan_infected_codes": [1],          # exit codes that mean "virus found" (Windows Defender's MpCmdRun uses 2)
        "scan_fail_closed": False,           # true = refuse the order when the scanner itself fails (stricter)
        "deep_file_checks": True,            # look inside .docx/.xlsx/.pptx/.odt... (zip bombs, macros, fake files)
        "max_json_mb": 2,                    # biggest allowed body for every non-upload request
        "max_render_pixels": 12000000,       # never render a PDF page bigger than this when counting colour
        "libreoffice_wait_s": 20,            # how long an estimate waits for the (single) LibreOffice converter
    },
    "analysis": {
        # Same rule as the POS: a page's colour share = coloured pixels / inked pixels (%).
        # <= thresh_bw: B&W   <= thresh_half: light colour   <= thresh_three_quarter: medium   above: full colour
        "thresh_bw": 2.0, "thresh_half": 25.0, "thresh_three_quarter": 60.0,
        "use_libreoffice": "auto",   # true / false / "auto" (use it when installed)
    },
    # These mirror the POS price list. When the POS is connected it pushes its own rates and they replace these.
    "rates": {
        "doc_bw": 3.0, "doc_half": 5.0, "doc_three_quarter": 7.5, "doc_full": 10.0,
        "surcharge_plain": 0.0, "surcharge_photopaper": 10.0, "surcharge_folio": 1.0,
        "duplex_discount_page": 1.0, "duplex_discount_photo": 15.0,
        "min_print_fee": 0.0, "typing_fee": 5.0,
    },
    "paper_sizes": {
        "A4": {"label": "A4 (210 x 297 mm)", "w": 210.0, "h": 297.0},
        "Letter": {"label": "Letter (8.5 x 11 in)", "w": 215.9, "h": 279.4},
        "Long": {"label": "Long / Folio (8.5 x 13 in)", "w": 215.9, "h": 330.2},
    },
    "photo_paper": {
        # base "plain"/"photopaper": priced like the POS (full-colour page rate + paper surcharge + folio surcharge).
        # base "custom": uses the fixed per-size rates below (for papers the POS does not price).
        "plain": {"label": "Plain Paper (Full Color)", "base": "plain"},
        "110gsm": {"label": "Photopaper (110gsm)", "base": "photopaper"},
        "180gsm": {"label": "Photopaper (180gsm)", "base": "custom", "rates": {"A4": 30, "Letter": 30, "Long": 30}},
        "sticker": {"label": "Photo Sticker Paper", "base": "custom", "rates": {"A4": 35, "Letter": 35, "Long": 35}},
    },
    "page": {"margin_mm": 5.0, "gap_mm": 2.0},
    # w/h are the portrait size in mm; the best orientation is chosen automatically.
    "layouts": [
        {"key": "full", "name": "Full page photo", "type": "full"},
        {"key": "20x25", "name": "20 x 25 cm", "type": "fixed", "w": 200, "h": 250},
        {"key": "13x18", "name": "13 x 18 cm", "type": "fixed", "w": 130, "h": 180},
        {"key": "10x15", "name": "10 x 15 cm", "type": "fixed", "w": 100, "h": 150},
        {"key": "9x13", "name": "9 x 13 cm", "type": "fixed", "w": 90, "h": 130},
        {"key": "wallet", "name": "Wallet (2.5 x 3.5 in)", "type": "fixed", "w": 63.5, "h": 88.9},
        {"key": "contact", "name": "Contact sheet", "type": "grid", "cols": 5, "rows": 7},
        {"key": "2R", "name": "2R (2.5 x 3.5 in)", "type": "fixed", "w": 64, "h": 89},
        {"key": "3R", "name": "3R (3.5 x 5 in)", "type": "fixed", "w": 89, "h": 127},
        {"key": "4R", "name": "4R (4 x 6 in)", "type": "fixed", "w": 102, "h": 152},
        {"key": "5R", "name": "5R (5 x 7 in)", "type": "fixed", "w": 127, "h": 178},
        {"key": "8R", "name": "8R (8 x 10 in)", "type": "fixed", "w": 203, "h": 254},
    ],
    "rush_id": {
        # ID photos are square: 1x1 in = 25.4 mm, 2x2 in = 50.8 mm.
        "sheet": {"label": "5R (5 x 7 in)", "w": 127.0, "h": 177.8, "margin": 4.0, "gap": 2.0},
        "packages": [
            {"key": "A", "label": "Rush ID - Package A (6pcs 1x1)", "price": 20,
             "pieces": [{"size_in": 1, "count": 6}]},
            {"key": "B", "label": "Rush ID - Package B (3pcs 2x2)", "price": 25,
             "pieces": [{"size_in": 2, "count": 3}]},
            {"key": "C", "label": "Rush ID - Package C (2pcs 2x2 + 4pcs 1x1)", "price": 30,
             "pieces": [{"size_in": 2, "count": 2}, {"size_in": 1, "count": 4}]},
        ],
    },
    # allowed_networks: only these addresses may use the POS connection, e.g. ["10.0.4.20/32"]. Empty = any shop-network address.
    "integration": {"pos_enabled": True, "allowed_networks": []},      # lets the POS connect (LAN only, needs the POS key)
    "payment": {
        "note": "Please pay at the counter when you pick up your order.",
        "gcash_number": "", "gcash_name": "",
    },
    "retention": {"days": 14},          # delete customer files N days after Completed/Cancelled (0 = never)
    # allow_remote=false: the staff area only works from the shop network, never through the tunnel/internet.
    "admin": {"session_hours": 12, "allow_remote": False,
              # allowed_networks: only these addresses may open the staff area, e.g. ["127.0.0.1/32", "10.0.4.37/32"]
              # (this PC and the staff phone). Empty = any shop-network address. Customers on the shop Wi-Fi are on the
              # same network, so setting this is the strongest protection for /admin.
              "allowed_networks": [], "require_private_peer": True,
              "lockout_attempts": 5, "lockout_minutes": 10,
              # true: the old shared PIN keeps working next to named staff accounts. Set false once everyone has an account.
              "allow_shared_pin": True},
    "backup": {"keep": 14, "extra_dir": ""},       # nightly zip of the portal's own data; extra_dir = USB / NAS copy
    # Customer order-status search. A code shows everything; a name shows only a masked name + the status.
    "status": {"name_search": True, "name_search_days": 14, "name_search_max_results": 8, "name_search_min_chars": 3},
    # public_ports: ports that the tunnel / internet uses. The staff area and POS API are ALWAYS refused on these ports
    # (also settable with the JCM_PUBLIC_PORTS environment variable, e.g. JCM_PUBLIC_PORTS=1013).
    # trusted_proxies: addresses whose X-Forwarded-For / CF-Connecting-IP we believe (the tunnel program on this PC).
    "network": {"public_ports": [], "trusted_proxies": ["127.0.0.1", "::1"]},
    "security": {"confine_folders": True, "hsts": True},
    # Orders sent with the phone's Share button (Facebook / Messenger links, photos, PDFs).
    "share": {"enabled": True, "max_links": 10},
}

_lock = threading.Lock()
_cache = {"mtime": None, "cfg": None}

# POS rates_config.json key -> (section, portal key)
POS_RATE_MAP = {
    "rate_bw": ("rates", "doc_bw"), "rate_doc_half": ("rates", "doc_half"),
    "rate_doc_three_quarter": ("rates", "doc_three_quarter"), "rate_doc_full": ("rates", "doc_full"),
    "surcharge_plain": ("rates", "surcharge_plain"), "surcharge_photopaper": ("rates", "surcharge_photopaper"),
    "surcharge_folio": ("rates", "surcharge_folio"),
    "discount_duplex_per_page": ("rates", "duplex_discount_page"),
    "discount_duplex_photopaper": ("rates", "duplex_discount_photo"),
    "min_print_fee": ("rates", "min_print_fee"), "typing_service_fee": ("rates", "typing_fee"),
    "thresh_bw": ("analysis", "thresh_bw"), "thresh_half": ("analysis", "thresh_half"),
    "thresh_three_quarter": ("analysis", "thresh_three_quarter"),
}
POS_PACKAGE_KEYS = {"rate_package_a": "A", "rate_package_b": "B", "rate_package_c": "C"}


def clean_pos_rates(raw: dict) -> dict:
    """Keep only known numeric POS settings (never trust the sender)."""
    out = {}
    for k, v in (raw or {}).items():
        if k in POS_RATE_MAP or k in POS_PACKAGE_KEYS:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            hi = 100.0 if k.startswith("thresh_") else 100000.0
            if 0 <= f <= hi:
                out[k] = f
    return out


def apply_pos_rates(cfg: dict, pos: dict) -> dict:
    for k, v in pos.items():
        if k in POS_RATE_MAP:
            sec, key = POS_RATE_MAP[k]
            cfg[sec][key] = v
        elif k in POS_PACKAGE_KEYS:
            for pk in cfg["rush_id"]["packages"]:
                if pk["key"] == POS_PACKAGE_KEYS[k]:
                    pk["price"] = v
    return cfg


def pos_rates_path():
    return data_dir_for_rates() / "pos_rates.json"


def data_dir_for_rates() -> Path:
    base = os.environ.get("JCM_DATA_DIR") or DEFAULTS["storage"]["data_dir"]
    p = Path(base)
    if not p.is_absolute():
        p = BASE_DIR / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get_config() -> dict:
    """Return the merged config, re-reading config.json / the POS price file when they change on disk."""
    with _lock:
        def mt(p):
            try:
                return p.stat().st_mtime
            except OSError:
                return None
        key = (mt(CONFIG_PATH), mt(pos_rates_path()))
        if _cache["cfg"] is None or key != _cache["mtime"]:
            user = {}
            if key[0] is not None:
                try:
                    user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except (OSError, ValueError) as e:      # a typo must not take the shop offline
                    print(f"[config] could not read {CONFIG_PATH}: {e} - using defaults")
            cfg = _merge(DEFAULTS, user)
            cfg["prices_source"], cfg["prices_synced_at"] = "portal", ""
            if key[1] is not None:
                try:
                    blob = json.loads(pos_rates_path().read_text(encoding="utf-8"))
                    apply_pos_rates(cfg, clean_pos_rates(blob.get("rates", {})))
                    cfg["prices_source"], cfg["prices_synced_at"] = "pos", blob.get("synced_at", "")
                except (OSError, ValueError) as e:
                    print(f"[config] could not read POS prices: {e}")
            _cache["cfg"], _cache["mtime"] = cfg, key
        return _cache["cfg"]


def save_pos_rates(raw: dict) -> dict:
    clean = clean_pos_rates(raw)
    if "rate_bw" not in clean:
        raise ValueError("The price list must include rate_bw.")
    path = pos_rates_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"rates": clean, "synced_at": now().isoformat(timespec="seconds")}, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    with _lock:
        _cache["cfg"] = None
    return clean


def upload_dir(cfg=None) -> Path:
    cfg = cfg or get_config()
    return Path(os.environ.get("JCM_UPLOAD_DIR") or cfg["storage"]["upload_dir"])


def mount_point(cfg=None) -> str:
    cfg = cfg or get_config()
    if "JCM_MOUNT_POINT" in os.environ:
        return os.environ["JCM_MOUNT_POINT"]
    return cfg["storage"].get("mount_point", "")


def parse_ports(text) -> list:
    """'80,1013' / '80 1013' / 8080 -> [80, 1013].  Empty -> [80].  Raises ValueError for anything that is not a port."""
    import re
    parts = [p for p in re.split(r"[,;\s]+", str(text or "").strip()) if p]
    if not parts:
        return [80]
    out = []
    for p in parts:
        if not p.isdigit() or not (1 <= int(p) <= 65535):
            raise ValueError(f"'{p}' is not a valid port (use numbers 1-65535, e.g. JCM_PORT=80,1013)")
        if int(p) not in out:
            out.append(int(p))
    return out


def public_ports(cfg=None) -> list:
    """Ports the internet / tunnel arrives on (env JCM_PUBLIC_PORTS + config network.public_ports)."""
    import re
    cfg = cfg or get_config()
    out = []
    raw = list(cfg.get("network", {}).get("public_ports") or [])
    raw += [p for p in re.split(r"[,;\s]+", os.environ.get("JCM_PUBLIC_PORTS", "")) if p]
    for p in raw:
        try:
            n = int(str(p).strip())
        except ValueError:
            continue
        if 1 <= n <= 65535 and n not in out:
            out.append(n)
    return out


def admin_networks(cfg=None) -> list:
    env = os.environ.get("JCM_ADMIN_NETWORKS", "").strip()
    if env:
        return [x for x in env.replace(";", ",").split(",") if x.strip()]
    return list((cfg or get_config())["admin"].get("allowed_networks") or [])


def pos_networks(cfg=None) -> list:
    env = os.environ.get("JCM_POS_NETWORKS", "").strip()
    if env:
        return [x for x in env.replace(";", ",").split(",") if x.strip()]
    return list((cfg or get_config())["integration"].get("allowed_networks") or [])


def data_dir(cfg=None) -> Path:
    cfg = cfg or get_config()
    p = Path(os.environ.get("JCM_DATA_DIR") or cfg["storage"]["data_dir"])
    if not p.is_absolute():
        p = BASE_DIR / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def public_config(cfg=None) -> dict:
    """The subset of the config the customer pages need (no paths, no secrets)."""
    from . import pricing
    cfg = cfg or get_config()
    lim = cfg["limits"]
    photo_rates = {t: {s: pricing.photo_rate(cfg, t, s) for s in cfg["paper_sizes"]} for t in cfg["photo_paper"]}
    return {
        "photo_rates": photo_rates, "prices_source": cfg["prices_source"],
        "shop": cfg["shop"], "rates": cfg["rates"], "paper_sizes": cfg["paper_sizes"],
        "photo_paper": {k: {"label": v["label"]} for k, v in cfg["photo_paper"].items()}, "page": cfg["page"], "layouts": cfg["layouts"],
        "rush_id": cfg["rush_id"], "payment": cfg["payment"], "statuses": STATUSES,
        "limits": {"max_file_mb": lim["max_file_mb"], "max_files": lim["max_files_per_order"],
                   "max_copies": lim["max_copies"], "max_text_chars": lim["max_text_chars"],
                   "max_request_mb_tunnel": lim["max_request_mb_tunnel"]},
        "retention_days": cfg["retention"]["days"],
        "status_search": {"name_search": bool(cfg["status"]["name_search"]),
                          "min_chars": int(cfg["status"]["name_search_min_chars"])},
        "share": {"enabled": bool(cfg["share"]["enabled"]), "max_links": int(cfg["share"]["max_links"]),
                  "services": __import__("portal.sharing", fromlist=["x"]).services_for_pages()},
    }
