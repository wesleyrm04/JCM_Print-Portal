"""Staff login: one PIN, signed session cookie, in-memory rate limiting. No internet needed."""
import hashlib
import hmac
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
    _load()
    return hmac.compare_digest((pin or "").strip().encode(), _state["pin"].encode())


def make_token(hours: float) -> str:
    _load()
    exp = str(int(time.time() + hours * 3600))
    sig = hmac.new(_state["secret"], exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_token(tok: str) -> bool:
    _load()
    try:
        exp, sig = (tok or "").split(".", 1)
        good = hmac.new(_state["secret"], exp.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except Exception:
        return False


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
