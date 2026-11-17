"""Staff login: one PIN, signed session cookie, in-memory rate limiting. No internet needed."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from . import settings

COOKIE = "jcm_admin"
_state = {"secret": None, "pin": None}
_lock = threading.Lock()


def _load():
    with _lock:
        if _state["secret"]:
            return
        d = settings.data_dir()
        kf = d / "secret.key"
        if not kf.exists():
            kf.write_text(secrets.token_hex(32))
            try:
                os.chmod(kf, 0o600)
            except OSError:
                pass
        _state["secret"] = kf.read_text().strip().encode()
        env = os.environ.get("JCM_ADMIN_PIN")
        if env:
            _state["pin"] = env
        else:
            pf = d / "admin_pin.txt"
            if not pf.exists():
                pf.write_text(f"{secrets.randbelow(900000) + 100000}\n")
                try:
                    os.chmod(pf, 0o600)
                except OSError:
                    pass
                print(f"[admin] First run: staff PIN created. Read it in {pf}")
            _state["pin"] = pf.read_text().strip()


def check_pin(pin: str) -> bool:
    """The shared (legacy) staff PIN."""
    _load()
    return hmac.compare_digest((pin or "").strip().encode(), _state["pin"].encode())


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
    p = _staff_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"staff": accounts}, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _hash(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 200_000).hex()


def staff_names() -> list:
    return [a["name"] for a in _load_staff()]


def staff_list() -> list:
    return [{"name": a["name"], "role": a.get("role", "staff")} for a in _load_staff()]


def set_staff(name: str, pin: str, role: str = "staff"):
    name = " ".join((name or "").split())[:30]
    if not name:
        raise ValueError("Please enter a name.")
    if not (pin or "").isdigit() or not 4 <= len(pin) <= 12:
        raise ValueError("The PIN must be 4 to 12 digits.")
    if role not in ("owner", "staff"):
        raise ValueError("Role must be owner or staff.")
    accounts = [a for a in _load_staff() if a["name"].casefold() != name.casefold()]
    prev = [a for a in _load_staff() if a["name"].casefold() == name.casefold()]
    if prev and prev[0].get("role") == "owner" and role != "owner" and not any(a.get("role") == "owner" for a in accounts):
        raise ValueError("There must always be at least one owner.")
    salt = secrets.token_bytes(16)
    accounts.append({"name": name, "role": role, "salt": salt.hex(), "hash": _hash(pin, salt)})
    _save_staff(sorted(accounts, key=lambda a: a["name"].casefold()))


def delete_staff(name: str):
    accounts = _load_staff()
    keep = [a for a in accounts if a["name"].casefold() != (name or "").casefold()]
    if len(keep) == len(accounts):
        raise ValueError("No such person.")
    if keep and not any(a.get("role") == "owner" for a in keep):
        raise ValueError("There must always be at least one owner.")
    _save_staff(keep)


def check_staff(name: str, pin: str):
    """-> {'name','role'} or None. Always does the same work so timing does not reveal which names exist."""
    accounts = [a for a in _load_staff() if a["name"].casefold() == (name or "").strip().casefold()]
    a = accounts[0] if accounts else {"name": "", "role": "staff", "salt": "00" * 16, "hash": "x"}
    ok = hmac.compare_digest(_hash((pin or "").strip(), bytes.fromhex(a["salt"])), a["hash"]) and bool(accounts)
    return {"name": a["name"], "role": a.get("role", "staff")} if ok else None


def login(name: str, pin: str, allow_shared: bool):
    """-> session dict or None.  Named account when a name is given; the shared PIN otherwise (if allowed)."""
    if (name or "").strip():
        return check_staff(name, pin)
    if allow_shared or not _load_staff():
        return {"name": "Staff", "role": "owner"} if check_pin(pin) else None
    return None


def make_token(hours: float, name: str = "Staff", role: str = "owner") -> str:
    _load()
    payload = base64.urlsafe_b64encode(json.dumps({"n": name, "r": role, "e": int(time.time() + hours * 3600)}).encode()).decode()
    sig = hmac.new(_state["secret"], payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def valid_token(tok: str):
    """-> {'name','role'} or None"""
    _load()
    try:
        payload, sig = (tok or "").rsplit(".", 1)
        good = hmac.new(_state["secret"], payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        d = json.loads(base64.urlsafe_b64decode(payload.encode()))
        if int(d["e"]) <= time.time():
            return None
        # a removed account loses access immediately (the shared-PIN 'Staff' session is exempt)
        if d["n"] != "Staff" and d["n"].casefold() not in [n.casefold() for n in staff_names()]:
            return None
        return {"name": d["n"], "role": d.get("r", "staff")}
    except Exception:
        return None


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
            f.write_text(secrets.token_urlsafe(24) + "\n")
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
        _pos["key"] = f.read_text().strip()
        return _pos["key"]


def rotate_pos_key() -> str:
    with _lock:
        _pos["key"] = None
    f = settings.data_dir() / "pos_api_key.txt"
    f.write_text(secrets.token_urlsafe(24) + "\n")
    return pos_key()


def check_pos_key(token: str) -> bool:
    return hmac.compare_digest((token or "").encode(), pos_key().encode())
