"""Staff login: signed session cookie backed by a SERVER-SIDE session list, hashed staff PINs, per-account lock-out,
in-memory rate limiting.  No internet needed.

v6 changes (everything the v5 file did is still here):
  * Sessions are kept on the server (data/sessions.json).  Logging out, removing an account or resetting its PIN
    really ends those sessions; a demoted owner loses owner rights immediately (the role is read from the
    account, not from the cookie).
  * PBKDF2 uses 600 000 rounds for new/changed PINs; old 200 000-round hashes still work and are upgraded the
    next time that person logs in.
  * PIN policy: 6+ digits, or a password of 8+ characters; trivial values (123456, 000000, password ...) refused.
  * Per-account / shared-PIN lock-out (not just per IP).
  * Secret files are created owner-only on Linux and locked down with icacls on Windows (os.chmod does nothing there).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
import time
from collections import defaultdict, deque

from . import settings

COOKIE = "jcm_admin"
PBKDF2_ITERS = 600_000
LEGACY_ITERS = 200_000
MIN_DIGITS, MIN_CHARS, MAX_PIN = 6, 8, 64
SHARED_NAME = "Staff"
_state = {"secret": None, "pin": None}
_lock = threading.Lock()


# ---------------------------------------------------------------- owner-only files
def lock_down(path):
    """Make a secret file readable only by this account (+ SYSTEM/Administrators on Windows). Best effort."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if os.name == "nt":
        try:
            who = os.environ.get("USERNAME", "")
            dom = os.environ.get("USERDOMAIN", "")
            grants = ["*S-1-5-18:(F)", "*S-1-5-32-544:(F)"]
            if who:
                grants.insert(0, f"{dom + chr(92) if dom else ''}{who}:(F)")
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", *grants],
                           capture_output=True, timeout=15, check=False)
        except Exception:
            pass


def write_secret(path, text: str):
    """Atomic write that never leaves the file world-readable, even for a moment."""
    path = os.fspath(path)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    lock_down(tmp)
    os.replace(tmp, path)
    lock_down(path)


def _load():
    with _lock:
        if _state["secret"]:
            return
        d = settings.data_dir()
        kf = d / "secret.key"
        if not kf.exists():
            write_secret(kf, secrets.token_hex(32))
        _state["secret"] = kf.read_text().strip().encode()
        env = os.environ.get("JCM_ADMIN_PIN")
        if env:
            _state["pin"] = env
        else:
            pf = d / "admin_pin.txt"
            if not pf.exists():
                write_secret(pf, f"{secrets.randbelow(900000) + 100000}\n")
                print(f"[admin] First run: staff PIN created. Read it in {pf}")
            _state["pin"] = pf.read_text().strip()


def check_pin(pin: str) -> bool:
    """The shared (legacy) staff PIN."""
    _load()
    return hmac.compare_digest((pin or "").strip().encode(), _state["pin"].encode())


# ---------------------------------------------------------------- PIN policy
_COMMON = {"123456", "1234567", "12345678", "123456789", "1234567890", "000000", "111111", "121212", "123123",
           "654321", "666666", "696969", "112233", "159753", "password", "password1", "qwertyui", "qwerty123",
           "admin123", "letmein1", "iloveyou", "11111111", "00000000", "jcmprints", "jcm12345"}


def validate_pin(pin) -> str:
    """-> the cleaned PIN, or raises ValueError with a message staff can read."""
    p = (pin or "").strip() if isinstance(pin, str) else ""
    msg = f"The PIN must be at least {MIN_DIGITS} digits, or a password of at least {MIN_CHARS} characters."
    if len(p) > MAX_PIN:
        raise ValueError(f"The PIN / password can be at most {MAX_PIN} characters.")
    if p.isdigit():
        if len(p) < MIN_DIGITS:
            raise ValueError(msg)
    elif len(p) < MIN_CHARS:
        raise ValueError(msg)
    low = p.lower()
    seq = "0123456789012345678909876543210987654321"
    if low in _COMMON or len(set(low)) == 1 or (p.isdigit() and low in seq):
        raise ValueError("That PIN is too easy to guess. Please choose another one.")
    return p


# ---------------------------------------------------------------- named staff accounts
def _staff_path():
    return settings.data_dir() / "staff.json"


def _load_staff() -> list:
    try:
        data = json.loads(_staff_path().read_text(encoding="utf-8"))
        return [a for a in data.get("staff", []) if a.get("name") and a.get("hash")]
    except Exception:
        return []


def _save_staff(accounts: list):
    write_secret(_staff_path(), json.dumps({"staff": accounts}, indent=1))


def _hash(pin: str, salt: bytes, iters: int = LEGACY_ITERS) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, int(iters)).hex()


def staff_names() -> list:
    return [a["name"] for a in _load_staff()]


def staff_list() -> list:
    return [{"name": a["name"], "role": a.get("role", "staff")} for a in _load_staff()]


def _account(name: str):
    n = (name or "").strip().casefold()
    for a in _load_staff():
        if a["name"].casefold() == n:
            return a
    return None


def set_staff(name: str, pin: str, role: str = "staff"):
    name = " ".join((name or "").split())[:30]
    if not name:
        raise ValueError("Please enter a name.")
    if name.casefold() == SHARED_NAME.casefold():
        raise ValueError(f"The name \"{SHARED_NAME}\" is reserved for the shared PIN. Please use a real name.")
    pin = validate_pin(pin)
    if role not in ("owner", "staff"):
        raise ValueError("Role must be owner or staff.")
    accounts = [a for a in _load_staff() if a["name"].casefold() != name.casefold()]
    prev = [a for a in _load_staff() if a["name"].casefold() == name.casefold()]
    if prev and prev[0].get("role") == "owner" and role != "owner" and not any(a.get("role") == "owner" for a in accounts):
        raise ValueError("There must always be at least one owner.")
    salt = secrets.token_bytes(16)
    accounts.append({"name": name, "role": role, "salt": salt.hex(), "iter": PBKDF2_ITERS,
                     "hash": _hash(pin, salt, PBKDF2_ITERS)})
    _save_staff(sorted(accounts, key=lambda a: a["name"].casefold()))


def delete_staff(name: str):
    accounts = _load_staff()
    keep = [a for a in accounts if a["name"].casefold() != (name or "").casefold()]
    if len(keep) == len(accounts):
        raise ValueError("No such person.")
    if keep and not any(a.get("role") == "owner" for a in keep):
        raise ValueError("There must always be at least one owner.")
    _save_staff(keep)
    revoke_account(name)


def check_staff(name: str, pin: str):
    """-> {'name','role'} or None. Always does the same work so timing does not reveal which names exist."""
    a = _account(name)
    real = a is not None
    if not real:
        a = {"name": "", "role": "staff", "salt": "00" * 16, "hash": "x", "iter": PBKDF2_ITERS}
    iters = int(a.get("iter") or LEGACY_ITERS)
    pin = (pin or "").strip()
    got = _hash(pin, bytes.fromhex(a["salt"]), iters)
    ok = hmac.compare_digest(got, a["hash"]) and real
    if not ok:
        return None
    if iters < PBKDF2_ITERS:                       # quietly upgrade an old hash now that we know the PIN
        try:
            accounts = [x for x in _load_staff() if x["name"].casefold() != a["name"].casefold()]
            salt = secrets.token_bytes(16)
            accounts.append({"name": a["name"], "role": a.get("role", "staff"), "salt": salt.hex(),
                             "iter": PBKDF2_ITERS, "hash": _hash(pin, salt, PBKDF2_ITERS)})
            _save_staff(sorted(accounts, key=lambda x: x["name"].casefold()))
        except Exception:
            pass
    return {"name": a["name"], "role": a.get("role", "staff")}


def login(name: str, pin: str, allow_shared: bool):
    """-> session dict or None.  Named account when a name is given; the shared PIN otherwise (if allowed)."""
    if (name or "").strip():
        return check_staff(name, pin)
    if allow_shared or not _load_staff():
        return {"name": SHARED_NAME, "role": "owner"} if check_pin(pin) else None
    return None


# ---------------------------------------------------------------- server-side sessions
_slock = threading.RLock()
_sessions = {"loaded": False, "items": {}}          # sid -> {"n","r","e"}


def _sessions_path():
    return settings.data_dir() / "sessions.json"


def _sessions_load():
    with _slock:
        if _sessions["loaded"]:
            return
        try:
            data = json.loads(_sessions_path().read_text(encoding="utf-8"))
            _sessions["items"] = {k: v for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            _sessions["items"] = {}
        _sessions["loaded"] = True


def _sessions_save():
    now = time.time()
    _sessions["items"] = {k: v for k, v in _sessions["items"].items() if float(v.get("e", 0)) > now}
    try:
        write_secret(_sessions_path(), json.dumps(_sessions["items"]))
    except OSError:
        pass


def _sig(payload: str) -> str:
    return hmac.new(_state["secret"], payload.encode(), hashlib.sha256).hexdigest()


def make_token(hours: float, name: str = SHARED_NAME, role: str = "owner") -> str:
    _load()
    _sessions_load()
    sid = secrets.token_urlsafe(18)
    exp = int(time.time() + hours * 3600)
    with _slock:
        _sessions["items"][sid] = {"n": name, "r": role, "e": exp}
        _sessions_save()
    payload = base64.urlsafe_b64encode(json.dumps({"n": name, "r": role, "e": exp, "i": sid}).encode()).decode()
    return f"{payload}.{_sig(payload)}"


def _parse(tok: str):
    """-> payload dict if the signature is genuine (expiry NOT checked), else None."""
    _load()
    try:
        payload, sig = (tok or "").rsplit(".", 1)
        if not hmac.compare_digest(sig, _sig(payload)):
            return None
        return json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:
        return None


def session_id(tok: str):
    d = _parse(tok)
    return d.get("i") if d else None


def valid_token(tok: str):
    """-> {'name','role'} or None"""
    d = _parse(tok)
    if not d:
        return None
    try:
        if int(d["e"]) <= time.time():
            return None
        sid = d.get("i")
        if not sid:                                 # an old v5 cookie: log in again once
            return None
        _sessions_load()
        with _slock:
            s = _sessions["items"].get(sid)
        if not s or int(s.get("e", 0)) <= time.time() or s.get("n") != d["n"]:
            return None
        if d["n"] == SHARED_NAME:
            # shared-PIN session: only while the shared PIN is allowed (or nobody has a named account yet)
            if not settings.get_config()["admin"].get("allow_shared_pin", True) and _load_staff():
                return None
            return {"name": SHARED_NAME, "role": "owner"}
        acc = _account(d["n"])                      # a removed account loses access; the role is the CURRENT one
        if not acc:
            return None
        return {"name": acc["name"], "role": acc.get("role", "staff")}
    except Exception:
        return None


def revoke_session(tok: str):
    sid = session_id(tok)
    if not sid:
        return
    _sessions_load()
    with _slock:
        if _sessions["items"].pop(sid, None) is not None:
            _sessions_save()


def revoke_account(name: str, keep_sid=None) -> int:
    """End every session of one person (optionally keeping the caller's own). -> how many were ended."""
    _sessions_load()
    n = 0
    with _slock:
        for sid in [k for k, v in _sessions["items"].items()
                    if str(v.get("n", "")).casefold() == (name or "").casefold() and k != keep_sid]:
            _sessions["items"].pop(sid, None)
            n += 1
        if n:
            _sessions_save()
    return n


def revoke_all(keep_sid=None) -> int:
    """'Log out everyone' - the caller's own session can be kept."""
    _sessions_load()
    with _slock:
        gone = [k for k in _sessions["items"] if k != keep_sid]
        for k in gone:
            _sessions["items"].pop(k, None)
        if gone:
            _sessions_save()
    return len(gone)


# ---------------------------------------------------------------- rate limiting / lock-out
class RateLimiter:
    def __init__(self):
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float) -> bool:
        t = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < t - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(t)
            if len(self.hits) > 5000:                 # keep memory bounded
                for k in [k for k, v in self.hits.items() if not v or v[-1] < t - window][:2000]:
                    self.hits.pop(k, None)
            return True


limiter = RateLimiter()


class Lockout:
    """Counts wrong PINs per ACCOUNT (not per IP): after `limit` misses within `window` seconds the account is
    refused for the rest of the window, whoever is typing."""

    def __init__(self):
        self.fails = defaultdict(deque)
        self.lock = threading.Lock()

    def locked(self, key: str, limit: int, window: float) -> bool:
        t = time.time()
        with self.lock:
            q = self.fails[key]
            while q and q[0] < t - window:
                q.popleft()
            return len(q) >= limit

    def fail(self, key: str):
        with self.lock:
            t = time.time()
            self.fails[key].append(t)
            if len(self.fails) > 5000:                 # keep memory bounded (random names cannot fill it up)
                for k in [k for k, v in self.fails.items() if not v or v[-1] < t - 3600][:2000]:
                    self.fails.pop(k, None)

    def clear(self, key: str):
        with self.lock:
            self.fails.pop(key, None)


lockout = Lockout()


# ---------------------------------------------------------------- POS (cash-register program) API key
_pos = {"key": None}


def pos_key() -> str:
    """Shared secret the POS sends as 'Authorization: Bearer <key>'. Created on first use, kept in data/."""
    with _lock:
        if _pos["key"]:
            return _pos["key"]
        env = os.environ.get("JCM_POS_KEY")
        if env:
            _pos["key"] = env
            return env
        f = settings.data_dir() / "pos_api_key.txt"
        if not f.exists():
            write_secret(f, secrets.token_urlsafe(32) + "\n")
        else:
            lock_down(f)
        _pos["key"] = f.read_text().strip()
        return _pos["key"]


def rotate_pos_key() -> str:
    with _lock:
        _pos["key"] = None
    f = settings.data_dir() / "pos_api_key.txt"
    write_secret(f, secrets.token_urlsafe(32) + "\n")
    return pos_key()


def check_pos_key(token: str) -> bool:
    return hmac.compare_digest((token or "").encode(), pos_key().encode())
