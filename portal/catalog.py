"""Editable shop catalog: the Souvenirs gallery and the Tech-Repair services list.

Staff edit it in the dashboard (Catalog tab); it is stored in  data/catalog.json  and the pictures staff upload in
data/catalog_images/.  Customers get a filtered copy from /api/catalog (hidden / out-of-stock / switched-off things removed).
If the file is missing or damaged the built-in defaults (the original page content) are used, so the pages never break.
Pure Python (+ Pillow for pictures) - no web framework needed."""
import copy
import io
import json
import os
import re
import time
import uuid
from pathlib import Path

REV_KEY = "rev"
MAX_CATEGORIES, MAX_ITEMS, MAX_IMAGES = 30, 100, 12
MAX_SECTIONS, MAX_SERVICES = 12, 80
MAX_UPLOAD = 12 * 1024 * 1024
IMG_URL_RE = re.compile(r"^/(static/images|media)/[A-Za-z0-9_.\-]{1,120}\.(jpg|jpeg|png|webp)$", re.I)
MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{32}(_t)?\.jpg$")
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")

_IMG = "/static/images/"
DEFAULT = {
    REV_KEY: 1,
    "souvenirs": {
        "show_out_of_stock": False,           # False = out-of-stock products are hidden from customers
        "order_options": ["Keychains", "Fridge magnets", "Invitations", "Name tags", "Tarpaulin", "Standee / Sintra board", "Other"],
        "categories": [
            {"id": "keychains", "title": "Keychains & Fridge Magnets", "enabled": True, "items": [
                {"id": "k1", "name": "Custom Keychain", "images": [_IMG + "473601503_623107957327889_4500035839716247704_n.jpg"]},
                {"id": "k2", "name": "Car Magnets", "images": [_IMG + "473265139_620589684246383_9117151090067098987_n.jpg"]},
                {"id": "k3", "name": "Assorted Magnets", "images": [_IMG + "472668113_620589384246413_3225702170662326905_n.jpg"]},
                {"id": "k4", "name": "Dino Souvenirs", "images": [_IMG + "473002130_620589780913040_6630652098382717823_n.jpg"]}]},
            {"id": "invitations", "title": "Invitations & Name Tags", "enabled": True, "items": [
                {"id": "i1", "name": "Dedication Invitation", "images": [_IMG + "475987361_637658889206129_797339117601465013_n.jpg"]},
                {"id": "i2", "name": "Rainbow Invitation", "images": [_IMG + "476332242_637658899206128_759204822908155878_n.jpg"]},
                {"id": "i3", "name": "Wrapped Invitations", "images": [_IMG + "475464368_637658892539462_4052887853046638795_n.jpg"]},
                {"id": "i4", "name": "Frozen Name Tags", "images": [_IMG + "480518536_648402938131724_5402518772263412281_n.jpg"]}]},
            {"id": "standees", "title": "Custom Standees", "enabled": True, "items": [
                {"id": "s1", "name": "Custom Standee", "images": [_IMG + "480706072_650641861241165_484634563023305956_n.jpg"]}]},
        ],
    },
    "repair": {
        "device_types": ["Laptop", "Desktop", "Printer", "Other"],
        "sections": [
            {"id": "desktop", "title": "Desktop Services", "icon": "\U0001F5A5\uFE0F", "enabled": True, "services": [
                {"id": "d1", "name": "Basic Diagnostic Fee", "price": "\u20b1150 - \u20b1200", "notes": "Waived if you proceed with the repair"},
                {"id": "d2", "name": "OS Format & Reinstall", "price": "\u20b1400 - \u20b1500", "notes": "Includes Windows 10/11, Basic Office Apps, Drivers"},
                {"id": "d3", "name": "Software Troubleshooting / Virus Removal", "price": "\u20b1250 - \u20b1400", "notes": "Depends on severity"},
                {"id": "d4", "name": "Hardware Cleaning & Thermal Paste", "price": "\u20b1300 - \u20b1400", "notes": "Full dust cleaning + high-quality thermal paste"},
                {"id": "d5", "name": "Component Replacement / Installation", "price": "\u20b1200 - \u20b1350", "notes": "RAM, SSD/HDD, Power Supply, Graphics Card (parts excluded)"},
                {"id": "d6", "name": "Custom PC Building / Assembly", "price": "\u20b1500 - \u20b1700", "notes": "Cable management and system testing included"}]},
            {"id": "laptop", "title": "Laptop Services", "icon": "\U0001F4BB", "enabled": True, "services": [
                {"id": "l1", "name": "Basic Diagnostic Fee", "price": "\u20b1200 - \u20b1300", "notes": "Waived if you proceed with the repair"},
                {"id": "l2", "name": "OS Format & Reinstall", "price": "\u20b1500 - \u20b1600", "notes": "Includes Windows 10/11, Drivers, Basic Office Apps"},
                {"id": "l3", "name": "General Cleaning & Thermal Paste", "price": "\u20b1500 - \u20b1700", "notes": "Requires delicate disassembly"},
                {"id": "l4", "name": "Keyboard / Screen Replacement (Labor)", "price": "\u20b1500 - \u20b1700", "notes": "Cost of replacement parts not included"},
                {"id": "l5", "name": "Battery / RAM / Storage Upgrade (Labor)", "price": "\u20b1250 - \u20b1400", "notes": "Quick internal installations"}]},
            # Ready-made but OFF until the shop switches it on in the dashboard. Prices are placeholders to edit.
            {"id": "mobile", "title": "Mobile Services", "icon": "\U0001F4F1", "enabled": False, "services": [
                {"id": "m1", "name": "Basic Diagnostic Fee", "price": "Ask us", "notes": "Waived if you proceed with the repair"},
                {"id": "m2", "name": "Screen Replacement (Labor)", "price": "Ask us", "notes": "Cost of the screen is not included"},
                {"id": "m3", "name": "Battery Replacement (Labor)", "price": "Ask us", "notes": "Cost of the battery is not included"},
                {"id": "m4", "name": "Charging Port Repair", "price": "Ask us", "notes": ""},
                {"id": "m5", "name": "Software Reset / Flashing / Unlock", "price": "Ask us", "notes": "Back up your files first"}]},
        ],
    },
}


# ---------------------------------------------------------------- paths
def path(data_dir: Path) -> Path:
    return Path(data_dir) / "catalog.json"


def images_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "catalog_images"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- cleaning
def _s(v, n) -> str:
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(v if v is not None else "")).strip()[:n]


def _id(v, fallback) -> str:
    v = _s(v, 40)
    return v if ID_RE.match(v) else fallback


def _unique_id(v, taken: set, prefix: str) -> str:
    i = _id(v, "")
    while not i or i in taken:
        i = f"{prefix}{uuid.uuid4().hex[:6]}"
    taken.add(i)
    return i


def sanitize(raw) -> dict:
    """Turns whatever the dashboard sent into a safe catalog (unknown fields dropped, sizes limited, only our own picture URLs)."""
    if not isinstance(raw, dict):
        raise ValueError("Bad catalog.")
    sv_in = raw.get("souvenirs") if isinstance(raw.get("souvenirs"), dict) else {}
    rp_in = raw.get("repair") if isinstance(raw.get("repair"), dict) else {}

    cats, taken = [], set()
    for c in (sv_in.get("categories") or [])[:MAX_CATEGORIES]:
        if not isinstance(c, dict):
            continue
        items, itaken = [], set()
        for it in (c.get("items") or [])[:MAX_ITEMS]:
            if not isinstance(it, dict):
                continue
            imgs = []
            for u in (it.get("images") or [])[:MAX_IMAGES]:
                u = _s(u, 200)
                if IMG_URL_RE.match(u) and u not in imgs:
                    imgs.append(u)
            items.append({"id": _unique_id(it.get("id"), itaken, "p"), "name": _s(it.get("name"), 80) or "Product",
                          "price": _s(it.get("price"), 60), "images": imgs, "in_stock": bool(it.get("in_stock", True))})
        cats.append({"id": _unique_id(c.get("id"), taken, "c"), "title": _s(c.get("title"), 80) or "Products",
                     "enabled": bool(c.get("enabled", True)), "items": items})
    opts = []
    for o in (sv_in.get("order_options") or [])[:30]:
        o = _s(o, 60)
        if o and o not in opts:
            opts.append(o)

    secs, staken = [], set()
    for sec in (rp_in.get("sections") or [])[:MAX_SECTIONS]:
        if not isinstance(sec, dict):
            continue
        svcs, vtaken = [], set()
        for sv in (sec.get("services") or [])[:MAX_SERVICES]:
            if not isinstance(sv, dict):
                continue
            name = _s(sv.get("name"), 120)
            if not name:
                continue
            svcs.append({"id": _unique_id(sv.get("id"), vtaken, "s"), "name": name, "price": _s(sv.get("price"), 60),
                         "notes": _s(sv.get("notes"), 200), "available": bool(sv.get("available", True))})
        secs.append({"id": _unique_id(sec.get("id"), staken, "sec"), "title": _s(sec.get("title"), 60) or "Services",
                     "icon": _s(sec.get("icon"), 8), "enabled": bool(sec.get("enabled", True)), "services": svcs})
    devs = []
    for d in (rp_in.get("device_types") or [])[:20]:
        d = _s(d, 40)
        if d and d not in devs:
            devs.append(d)

    return {REV_KEY: int(raw.get(REV_KEY) or 0),
            "souvenirs": {"show_out_of_stock": bool(sv_in.get("show_out_of_stock", False)),
                          "order_options": opts or list(DEFAULT["souvenirs"]["order_options"]), "categories": cats},
            "repair": {"device_types": devs or list(DEFAULT["repair"]["device_types"]), "sections": secs}}


# ---------------------------------------------------------------- load / save
def load(data_dir: Path) -> dict:
    p = path(data_dir)
    if p.exists():
        try:
            cat = sanitize(json.loads(p.read_text(encoding="utf-8")))
            cat[REV_KEY] = max(1, cat[REV_KEY])
            return cat
        except (ValueError, OSError):
            print("[catalog] catalog.json is damaged - using the built-in defaults (the old file is left in place)")
    return default_catalog()


def default_catalog() -> dict:
    """The built-in catalog (the original page content), cleaned like any saved catalog."""
    cat = sanitize(copy.deepcopy(DEFAULT))
    cat[REV_KEY] = 1
    return cat


class Conflict(Exception):
    pass


def save(data_dir: Path, raw) -> dict:
    """Saves a catalog sent by the dashboard. `raw['rev']` must be the revision the editor started from."""
    new = sanitize(raw)
    cur = load(data_dir)
    if new[REV_KEY] != cur[REV_KEY]:
        raise Conflict("The catalog was changed somewhere else. Reload the page and try again.")
    new[REV_KEY] = cur[REV_KEY] + 1
    p = path(data_dir)
    if p.exists():
        try:
            os.replace(p, p.with_name("catalog.prev.json"))            # one step of undo, kept on disk
        except OSError:
            pass
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    remove_orphans(data_dir, new)
    return new


def referenced_media(cat: dict) -> set:
    out = set()
    for c in cat["souvenirs"]["categories"]:
        for it in c["items"]:
            for u in it["images"]:
                if u.startswith("/media/"):
                    n = u[len("/media/"):]
                    out.add(n)
                    out.add(n.replace(".jpg", "_t.jpg"))
    return out


def remove_orphans(data_dir: Path, cat: dict, min_age_s: int = 3600):
    """Deletes uploaded pictures nobody uses any more (only ones older than an hour, so a picture that was just
    uploaded but not saved yet is never removed)."""
    keep = referenced_media(cat)
    prev = path(data_dir).with_name("catalog.prev.json")
    if prev.exists():                                                # keep what the one-step undo still needs
        try:
            keep |= referenced_media(sanitize(json.loads(prev.read_text(encoding="utf-8"))))
        except (ValueError, OSError):
            pass
    now = time.time()
    for f in images_dir(data_dir).iterdir():
        if f.is_file() and f.name not in keep and now - f.stat().st_mtime > min_age_s:
            try:
                f.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- customer view
def public_view(cat: dict) -> dict:
    sv = cat["souvenirs"]
    show_out = sv["show_out_of_stock"]
    cats = []
    for c in sv["categories"]:
        if not c["enabled"]:
            continue
        items = [{"id": it["id"], "name": it["name"], "price": it["price"], "images": it["images"], "in_stock": it["in_stock"]}
                 for it in c["items"] if it["in_stock"] or show_out]
        if items:
            cats.append({"id": c["id"], "title": c["title"], "items": items})
    secs = []
    for s in cat["repair"]["sections"]:
        if not s["enabled"]:
            continue
        svcs = [{"name": v["name"], "price": v["price"], "notes": v["notes"]} for v in s["services"] if v["available"]]
        if svcs:
            secs.append({"id": s["id"], "title": s["title"], "icon": s["icon"], "services": svcs})
    devs = list(cat["repair"]["device_types"])
    if any(s["id"] == "mobile" for s in secs) and not any("mobile" in d.lower() or "phone" in d.lower() for d in devs):
        i = devs.index("Other") if "Other" in devs else len(devs)
        devs.insert(i, "Mobile phone / Tablet")
    return {REV_KEY: cat[REV_KEY], "souvenirs": {"categories": cats, "order_options": sv["order_options"]},
            "repair": {"sections": secs, "device_types": devs}}


# ---------------------------------------------------------------- pictures
def save_image(data_dir: Path, raw: bytes) -> str:
    """Checks an uploaded picture, strips its metadata, shrinks it and stores a full size + thumbnail JPEG.
    Returns the public URL (/media/<name>.jpg)."""
    from PIL import Image, ImageOps
    if not raw or len(raw) > MAX_UPLOAD:
        raise ValueError("The picture is too big (max 12 MB).")
    try:
        im = Image.open(io.BytesIO(raw))
        if im.width * im.height > 60_000_000:
            raise ValueError("The picture has too many pixels.")
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    except ValueError:
        raise
    except Exception:
        raise ValueError("That file is not a picture we can read (use JPG, PNG or WebP).")
    name = uuid.uuid4().hex
    d = images_dir(data_dir)
    big = im.copy()
    big.thumbnail((1600, 1600))
    big.save(d / f"{name}.jpg", "JPEG", quality=85, optimize=True)
    th = im.copy()
    th.thumbnail((480, 480))
    th.save(d / f"{name}_t.jpg", "JPEG", quality=80, optimize=True)
    return f"/media/{name}.jpg"
