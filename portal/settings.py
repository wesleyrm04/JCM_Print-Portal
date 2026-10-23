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
                 "typed": "print", "repair": "repair", "souvenir": "souvenir"}

DEFAULTS = {
    "shop": {
        "name": "JCM Prints and Computer Services",
        "short_name": "JCM Prints",
        "tagline": "Send your documents & photos straight to the counter - no internet needed.",
        "phone": "", "email": "", "address": "", "hours": "",
        "facebook": "https://facebook.com/jcmgcs",
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
        "max_pdf_pages_analyze": 300, "analyze_time_limit_s": 25,
        "max_text_chars": 20000, "max_copies": 100,
    },
    "analysis": {
        # a page counts as colour when >= min_ratio of its pixels are clearly coloured
        "sat_threshold": 40, "val_threshold": 60, "min_ratio": 0.0005,
        "use_libreoffice": "auto",   # true / false / "auto" (use it when installed)
    },
    "rates": {
        "doc_bw": 3.0, "doc_color": 10.0, "typing_fee": 5.0,
        "doc_paper_surcharge": {"A4": 0, "Letter": 0, "Long": 0},   # extra per page
    },
    "paper_sizes": {
        "A4": {"label": "A4 (210 x 297 mm)", "w": 210.0, "h": 297.0},
        "Letter": {"label": "Letter (8.5 x 11 in)", "w": 215.9, "h": 279.4},
        "Long": {"label": "Long / Folio (8.5 x 13 in)", "w": 215.9, "h": 330.2},
    },
    "photo_paper": {
        "plain": {"label": "Plain Paper (Full Color)", "rates": {"A4": 10, "Letter": 10, "Long": 15}},
        "110gsm": {"label": "Photopaper (110gsm)", "rates": {"A4": 20, "Letter": 20, "Long": 20}},
        "180gsm": {"label": "Photopaper (180gsm)", "rates": {"A4": 30, "Letter": 30, "Long": 30}},
        "sticker": {"label": "Photo Sticker Paper", "rates": {"A4": 35, "Letter": 35, "Long": 35}},
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
    "payment": {
        "note": "Please pay at the counter when you pick up your order.",
        "gcash_number": "", "gcash_name": "",
    },
    "retention": {"days": 14},          # delete customer files N days after Completed/Cancelled (0 = never)
    "admin": {"session_hours": 12},
}

_lock = threading.Lock()
_cache = {"mtime": None, "cfg": None}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get_config() -> dict:
    """Return the merged config, re-reading config.json when it changes on disk."""
    with _lock:
        try:
            mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            mtime = None
        if _cache["cfg"] is None or mtime != _cache["mtime"]:
            user = {}
            if mtime is not None:
                try:
                    user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except (OSError, ValueError) as e:      # a typo must not take the shop offline
                    print(f"[config] could not read {CONFIG_PATH}: {e} - using defaults")
            _cache["cfg"] = _merge(DEFAULTS, user)
            _cache["mtime"] = mtime
        return _cache["cfg"]


def upload_dir(cfg=None) -> Path:
    cfg = cfg or get_config()
    return Path(os.environ.get("JCM_UPLOAD_DIR") or cfg["storage"]["upload_dir"])


def mount_point(cfg=None) -> str:
    cfg = cfg or get_config()
    if "JCM_MOUNT_POINT" in os.environ:
        return os.environ["JCM_MOUNT_POINT"]
    return cfg["storage"].get("mount_point", "")


def data_dir(cfg=None) -> Path:
    cfg = cfg or get_config()
    p = Path(os.environ.get("JCM_DATA_DIR") or cfg["storage"]["data_dir"])
    if not p.is_absolute():
        p = BASE_DIR / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def public_config(cfg=None) -> dict:
    """The subset of the config the customer pages need (no paths, no secrets)."""
    cfg = cfg or get_config()
    lim = cfg["limits"]
    return {
        "shop": cfg["shop"], "rates": cfg["rates"], "paper_sizes": cfg["paper_sizes"],
        "photo_paper": cfg["photo_paper"], "page": cfg["page"], "layouts": cfg["layouts"],
        "rush_id": cfg["rush_id"], "payment": cfg["payment"], "statuses": STATUSES,
        "limits": {"max_file_mb": lim["max_file_mb"], "max_files": lim["max_files_per_order"],
                   "max_copies": lim["max_copies"], "max_text_chars": lim["max_text_chars"]},
        "retention_days": cfg["retention"]["days"],
    }
