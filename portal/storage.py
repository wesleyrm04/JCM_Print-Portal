"""Order folders on the NAS: numbering, safe names, order.json, zip, retention.
The folders stay the source of truth; SQLite (db.py) is only a searchable index."""
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from . import settings

_BAD = re.compile(r'[\\/*?:"<>|\x00-\x1f\x7f]')
ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif",
               "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "txt", "csv"}


def sanitize(name: str, maxlen: int = 60) -> str:
    s = _BAD.sub("", name or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:maxlen].strip(" .")


def safe_filename(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/"))
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = sanitize(stem, 80) or "file"
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:8].lower()
    return f"{stem}.{ext}" if ext else stem


def sniff_ok(ext: str, head: bytes) -> bool:
    """Cheap check that the file contents match its extension (blocks renamed .exe etc.)."""
    h = head
    if ext == "pdf":
        return h.startswith(b"%PDF") or b"%PDF" in h[:1024]
    if ext in ("jpg", "jpeg"):
        return h.startswith(b"\xff\xd8")
    if ext == "png":
        return h.startswith(b"\x89PNG")
    if ext == "gif":
        return h.startswith(b"GIF8")
    if ext == "webp":
        return h[:4] == b"RIFF" and h[8:12] == b"WEBP"
    if ext == "bmp":
        return h.startswith(b"BM")
    if ext in ("tif", "tiff"):
        return h[:4] in (b"II*\x00", b"MM\x00*")
    if ext in ("heic", "heif"):
        return h[4:8] == b"ftyp"
    if ext in ("docx", "xlsx", "pptx", "odt", "ods", "odp"):
        return h.startswith(b"PK\x03\x04")
    if ext in ("doc", "xls", "ppt"):
        return h.startswith(b"\xd0\xcf\x11\xe0")
    if ext == "rtf":
        return h.lstrip().startswith(b"{\\rtf")
    if ext in ("txt", "csv"):
        return b"\x00" not in h
    return False


def storage_status(cfg=None):
    cfg = cfg or settings.get_config()
    mp, base = settings.mount_point(cfg), settings.upload_dir(cfg)
    if mp and not os.path.ismount(mp):
        return False, f"Shop storage is not connected ({mp} is not mounted)"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"Cannot create the orders folder: {e.strerror or e}"
    if not os.access(base, os.W_OK):
        return False, "The orders folder is not writable"
    return True, "ok"


def acquire_number(date_folder: Path, prefix: str) -> int:
    """Atomic daily counter: O_CREAT|O_EXCL lock files, also skipping numbers whose folder exists."""
    locks = date_folder / ".locks"
    locks.mkdir(parents=True, exist_ok=True)
    for n in range(1, 10000):
        if any(date_folder.glob(f"{prefix}{n:03d}_*")):
            continue
        try:
            fd = os.open(locks / f"{prefix}{n:03d}.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return n
        except FileExistsError:
            continue
    raise RuntimeError("Too many orders today")


def release_number(date_folder: Path, prefix: str, n: int):
    try:
        (date_folder / ".locks" / f"{prefix}{n:03d}.lock").unlink()
    except OSError:
        pass


def make_order_id(prefix: str, date_compact: str, n: int) -> str:
    return f"{prefix + '-' if prefix else ''}{date_compact}-{n:03d}"


def folder_name(prefix: str, n: int, customer: str, preset: str) -> str:
    return f"{prefix}{n:03d}_[{sanitize(customer, 40) or 'Customer'}] - {sanitize(preset, 60) or 'Order'}"


def write_json_atomic(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def write_instructions(folder: Path, o: dict):
    L = ["=" * 42, "    PRINT SHOP CUSTOMER ORDER SUMMARY", "=" * 42,
         f"Order ID:          {o['order_id']}",
         f"Type:              {o.get('kind', '')}",
         f"Date & Time:       {o['date']} {o['time']}",
         f"Customer Name:     {o['customer_name']}",
         f"Phone:             {o.get('phone') or '-'}",
         f"Request:           {o['preset_request']}",
         f"Total Files Sent:  {o['total_files']}",
         f"Estimated Total:   PHP {o.get('total', 0):,.2f}" + ("   (STAFF: please verify)" if o.get("needs_review") else ""),
         "-" * 42, "PRICE BREAKDOWN:"]
    L += [f"  {x}" for x in o.get("breakdown", [])] or ["  -"]
    if o.get("customer_pay_ref"):
        L += ["-" * 42, f"CUSTOMER SAYS THEY PAID BY GCASH - reference: {o['customer_pay_ref']}  (STAFF: verify in your GCash app)"]
    for w in o.get("warnings", []):
        L += ["-" * 42, "WARNING: " + w]
    L += ["-" * 42, "CUSTOMER INSTRUCTIONS / NOTES:", o.get("instructions") or "None provided.",
          "-" * 42, "ATTACHED FILES:"]
    L += [f" - {f}" for f in o.get("files", [])]
    L.append("=" * 42)
    (folder / "instructions.txt").write_text("\n".join(L) + "\n", encoding="utf-8")


def resolve_file(folder: Path, name: str):
    """Safe lookup of one file inside an order folder (no path tricks)."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    p = (folder / name).resolve()
    if p.parent != folder.resolve() or not p.is_file():
        return None
    return p


def make_zip(folder: Path) -> str:
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="jcm_order_")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.iterdir()):
            if p.is_file() and not p.name.endswith(".tmp"):
                z.write(p, p.name)
    return tmp


def purge_files(folder: Path) -> int:
    """Delete customer files but keep order.json (sales record). Returns files removed."""
    n = 0
    if not folder.is_dir():
        return 0
    for p in folder.iterdir():
        if p.is_file() and p.name != "order.json":
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


# ---------------------------------------------------------------- deeper upload checks (v6)
# sniff_ok() above only looks at the first bytes.  These checks look inside the file, because staff open customer
# files on the shop computer.  Switch off with  "limits": {"deep_file_checks": false}  if a real customer file is refused.
_OOXML = {"docx", "xlsx", "pptx"}
_ODF = {"odt", "ods", "odp"}
_OLE = {"doc", "xls", "ppt"}
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNPACKED = 800 * 1024 * 1024          # everything inside, uncompressed
MAX_ZIP_ENTRY = 400 * 1024 * 1024             # any one part
_PROGRAM_MAGIC = (b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"#!", b"PK\x03\x04")
_OLE_MACRO_MARKS = [m for w in ("_VBA_PROJECT", "VBAProject", "PROJECTwm") for m in (w.encode(), w.encode("utf-16le"))]


def _looks_like_program(head: bytes) -> bool:
    h = head.lstrip(b"\xef\xbb\xbf")
    return any(h.startswith(m) for m in _PROGRAM_MAGIC)


def _open_zip(f):
    """ZipFile over an upload object. Python < 3.11 spooled files lack seekable(); fall back to a memory copy."""
    try:
        return zipfile.ZipFile(f)
    except AttributeError:
        f.seek(0)
        return zipfile.ZipFile(io.BytesIO(f.read()))


def zip_problem(f, ext: str) -> str:
    """'' when the zip-based document looks sane, else a short reason (zip bombs, path tricks, macros)."""
    try:
        z = _open_zip(f)
    except zipfile.BadZipFile:
        return "is damaged or is not a real document"
    with z:
        infos = z.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            return "has too many parts"
        total = 0
        names = set()
        for i in infos:
            n = i.filename.replace("\\", "/")
            parts = n.split("/")
            if n.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", n):
                return "contains an unsafe path"
            if i.file_size > MAX_ZIP_ENTRY:
                return "is too big once unpacked"
            total += i.file_size
            names.add(n.lower())
        if total > MAX_ZIP_UNPACKED:
            return "is too big once unpacked"
        if ext in _OOXML:
            if "[content_types].xml" not in names:
                return "is not a real ." + ext + " document"
            if any(n.endswith("vbaproject.bin") for n in names):
                return "contains macros, which are not accepted"
        elif ext in _ODF:
            if "mimetype" not in names:
                return "is not a real ." + ext + " document"
            if any(n.startswith(("basic/", "scripts/")) for n in names):
                return "contains macros, which are not accepted"
    return ""


def deep_check(ext: str, f):
    """-> (ok, reason).  `f` is a seekable binary file object; its position is restored."""
    pos = f.tell()
    try:
        f.seek(0)
        head = f.read(16)
        f.seek(0)
        if ext in _OOXML or ext in _ODF:
            why = zip_problem(f, ext)
            return (False, why) if why else (True, "")
        if ext in ("txt", "csv", "rtf") and _looks_like_program(head):
            return False, "looks like a program, not a ." + ext + " file"
        if ext == "pdf" and any(head.startswith(m) for m in _PROGRAM_MAGIC):
            return False, "looks like a program, not a PDF"
        return True, ""
    except Exception:
        return False, "could not be checked"
    finally:
        try:
            f.seek(pos)
        except Exception:
            pass


def has_ole_macros(path, limit: int = 30 * 1024 * 1024) -> bool:
    """Old Office files (.doc/.xls/.ppt) keep macros in a VBA storage. True when the file has one (staff are warned)."""
    try:
        with open(path, "rb") as fh:
            keep = b""
            read = 0
            while read < limit:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                read += len(chunk)
                buf = keep + chunk
                if any(m in buf for m in _OLE_MACRO_MARKS):
                    return True
                keep = buf[-64:]
    except OSError:
        return False
    return False
