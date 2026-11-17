"""Helpers for orders shared from Facebook / Messenger (links + files) and for the customer's
status search (order code or sender name).  Pure Python - no web framework needed - so it is easy to test.

The portal cannot open a Facebook link for the customer: Facebook needs a login and the portal may be
offline.  So a shared link is simply saved with the order and the staff open it on the shop computer."""
import re
from urllib.parse import urlparse

# Letters/digits the order codes are made of (kept in sync with CODE_ALPHABET in app.py)
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(r"^[" + CODE_ALPHABET + r"]{6}$")

FACEBOOK_HOSTS = ("facebook.com", "fb.com", "fb.me", "fb.watch", "m.me", "messenger.com", "instagram.com")

_URL_RE = re.compile(r"https?://[^\s<>\"'`\u200b]+", re.IGNORECASE)
_TRAIL = ".,;:!?)]}'\"\u2019\u201d"
MAX_LINK_LEN = 500


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_facebook(url: str) -> bool:
    h = _host(url)
    return any(h == d or h.endswith("." + d) for d in FACEBOOK_HOSTS)


# Well-known sharing services: (key, label shown to staff/customer, hosts, needs-permission hint).
# Any other http(s) link is still accepted, but staff get an "Other site" warning for it.
SERVICES = [
    ("facebook", "Facebook", ("facebook.com", "fb.com", "fb.me", "fb.watch", "m.me", "messenger.com"), False),
    ("instagram", "Instagram", ("instagram.com", "instagr.am"), False),
    ("canva", "Canva", ("canva.com", "canva.cn"), True),
    ("gdrive", "Google Drive", ("drive.google.com", "docs.google.com", "sheets.google.com", "slides.google.com",
                                "forms.google.com", "photos.google.com", "photos.app.goo.gl", "goo.gl", "g.co", "drive.usercontent.google.com"), True),
    ("onedrive", "OneDrive", ("onedrive.live.com", "1drv.ms", "sharepoint.com", "office.com", "onedrive.com", "live.com"), True),
    ("wps", "WPS", ("wps.com", "kdocs.cn", "wpscloud.cn", "wps.cn", "wpsoffice.com", "docs.wps.com"), True),
    ("dropbox", "Dropbox", ("dropbox.com", "db.tt", "dropboxusercontent.com"), True),
    ("icloud", "iCloud", ("icloud.com",), True),
    ("mega", "MEGA", ("mega.nz", "mega.io"), True),
    ("box", "Box", ("box.com", "app.box.com"), True),
    ("wetransfer", "WeTransfer", ("wetransfer.com", "we.tl"), False),
    ("notion", "Notion", ("notion.so", "notion.site"), True),
    ("figma", "Figma", ("figma.com",), True),
    ("pinterest", "Pinterest", ("pinterest.com", "pin.it"), False),
    ("tiktok", "TikTok", ("tiktok.com",), False),
    ("youtube", "YouTube", ("youtube.com", "youtu.be"), False),
    ("telegram", "Telegram", ("t.me", "telegram.me"), False),
    ("viber", "Viber", ("viber.com",), False),
    ("whatsapp", "WhatsApp", ("wa.me", "whatsapp.com"), False),
    ("shopee", "Shopee", ("shopee.ph", "shope.ee"), False),
    ("lazada", "Lazada", ("lazada.com.ph", "lzd.co"), False),
    ("adobe", "Adobe", ("adobe.com", "adobe.ly"), True),
    ("scribd", "Scribd", ("scribd.com",), False),
    ("pdf", "PDF host", ("pdfdrive.com",), False),
]


def service_of(url: str):
    """-> (key, label, needs_permission_hint) for a known service, or (None, '', False)."""
    h = _host(url)
    for key, label, hosts, perm in SERVICES:
        if any(h == d or h.endswith("." + d) for d in hosts):
            return key, label, perm
    return None, "", False


def service_label(url: str) -> str:
    return service_of(url)[1] or "Other site"


def link_info(links: list) -> list:
    return [{"url": u, "label": service_label(u), "known": service_of(u)[0] is not None} for u in links]


def services_for_pages() -> list:
    """What the customer pages need to label links (hosts + label)."""
    return [{"label": lab, "hosts": list(hosts)} for _, lab, hosts, _ in SERVICES]


def extract_links(text: str, limit: int = 10) -> list:
    """Every http(s) link found in a piece of text, in order, without duplicates."""
    out, seen = [], set()
    for m in _URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(_TRAIL)
        if len(u) > MAX_LINK_LEN or not _host(u):
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def collect_links(shared_text, listed, limit: int = 10) -> list:
    """Union of the links the page detected (a list) and the links found in the raw shared text."""
    pool = []
    if isinstance(listed, (list, tuple)):
        pool.extend(str(x) for x in listed[: limit * 2])
    pool.append(str(shared_text or "")[:5000])
    return extract_links("\n".join(pool), limit)


def link_warnings(links: list) -> list:
    """Staff warnings for links to sites we do not recognise (a stranger could send anything)."""
    out = []
    for u in links:
        if service_of(u)[0] is None:
            out.append(f"Link goes to {_host(u) or 'an unknown site'}, a site we do not recognise - open it with care: {u}")
    return out


def access_hints(links: list) -> list:
    """Reminders for cloud/design links that only open when the customer set them to 'anyone with the link'."""
    labels = sorted({service_of(u)[1] for u in links if service_of(u)[2]})
    if not labels:
        return []
    return ["If " + ", ".join(labels) + " asks for permission or says the file is private, message or call the customer "
            "and ask them to set it to \"Anyone with the link can view\" (Canva: Share > Anyone with the link)."]


def preset_for(links: list, n_files: int) -> str:
    keys = {service_of(u)[1] for u in links}
    if len(keys) == 1:
        only = next(iter(keys))
        if only:
            return f"Shared from {only}"
    if links and all(service_of(u)[0] is not None for u in links):
        return "Shared Links"
    return "Shared Items"


def mask_name(name: str) -> str:
    """'Juan Dela Cruz' -> 'Juan D.'  (used where anybody may search by name)."""
    parts = (name or "").split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][:1].upper()}."


def normalize_code(q: str):
    """Returns the 6-character order code if the text looks like one, else None."""
    c = re.sub(r"[\s\-_]", "", (q or "")).upper()
    return c if CODE_RE.match(c) else None


def name_tokens(q: str, min_len: int = 2) -> list:
    """Words of a name search (letters, digits, dots and apostrophes), at most 4."""
    words = re.findall(r"[^\W_][\w.'\u2019-]*", (q or "").strip(), re.UNICODE)
    return [w for w in words if len(w) >= min_len][:4]
