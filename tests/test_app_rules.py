"""Tests the access rules in portal/app.py (staff-area / POS network rules, client address, order-folder
confinement, virus-scan hook, upload checks) by calling the functions directly with fake requests.
Works with or without Starlette installed.   Run: python tests/test_app_rules.py"""
import asyncio
import io
import json
import os
import stat
import sys
import tempfile
import types
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp())
os.environ["JCM_DATA_DIR"] = str(TMP / "data")
os.environ.pop("JCM_PUBLIC_PORTS", None)
os.environ.pop("JCM_ADMIN_NETWORKS", None)
os.environ.pop("JCM_POS_NETWORKS", None)
os.environ["JCM_UPLOAD_DIR"] = str(TMP / "orders")

try:
    import starlette  # noqa: F401
except ImportError:                                   # no framework here: tiny stand-ins so app.py can be imported
    def _mod(name, **kw):
        m = types.ModuleType(name); m.__dict__.update(kw); sys.modules[name] = m
    class _Any:
        def __init__(self, *a, **k): pass
        def add_middleware(self, *a, **k): pass
    async def _rtp(f, *a, **k): return f(*a, **k)
    _mod("starlette"); _mod("starlette.applications", Starlette=_Any); _mod("starlette.background", BackgroundTask=_Any)
    _mod("starlette.concurrency", run_in_threadpool=_rtp); _mod("starlette.datastructures", MutableHeaders=_Any)
    _mod("starlette.requests", Request=_Any)
    _mod("starlette.responses", FileResponse=_Any, HTMLResponse=_Any, JSONResponse=_Any, RedirectResponse=_Any, Response=_Any)
    _mod("starlette.routing", Mount=_Any, Route=_Any); _mod("starlette.staticfiles", StaticFiles=_Any)

from portal import app as A, settings  # noqa: E402

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


class Req:
    def __init__(self, peer="127.0.0.1", headers=None, port=80):
        self.client = types.SimpleNamespace(host=peer)
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.scope = {"server": ("0.0.0.0", port)}


class Up:                                              # a fake uploaded file
    def __init__(self, name, data):
        self.filename, self.file, self.size = name, io.BytesIO(data), len(data)


def zbytes(names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return b.getvalue()


def main():
    cfg = settings.get_config()
    adm, pos = cfg["admin"], cfg["integration"]
    CF = {"CF-Connecting-IP": "198.51.100.7", "CF-Ray": "abc"}

    print("staff area: who may open it")
    check(A.admin_allowed(Req("127.0.0.1")) and A.admin_allowed(Req("10.0.4.50")), "this PC and shop-network devices are allowed by default")
    check(not A.admin_allowed(Req("127.0.0.1", CF)), "tunnel headers -> refused")
    check(not A.admin_allowed(Req("8.8.8.8")), "a public address connecting directly -> refused (even with no headers)")
    check(not A.admin_allowed(Req("127.0.0.1", port=1013)) or True, "(port rule checked below)")
    os.environ["JCM_PUBLIC_PORTS"] = "1013"
    check(not A.admin_allowed(Req("127.0.0.1", port=1013)) and A.admin_allowed(Req("127.0.0.1", port=80)), "on the tunnel port the staff area is refused even if the tunnel sends NO headers")
    os.environ.pop("JCM_PUBLIC_PORTS")
    adm["allowed_networks"] = ["127.0.0.1/32", "10.0.4.37/32"]
    check(A.admin_allowed(Req("10.0.4.37")) and A.admin_allowed(Req("127.0.0.1")) and not A.admin_allowed(Req("10.0.4.50")), "allowed_networks: only the listed PC / phone, not a customer on the Wi-Fi")
    adm["allowed_networks"] = []
    adm["allow_remote"] = True
    check(A.admin_allowed(Req("127.0.0.1", CF)), "allow_remote=true still lets staff in through the tunnel")
    adm["allow_remote"] = False
    adm["require_private_peer"] = False
    check(A.admin_allowed(Req("100.64.1.2")), "require_private_peer=false allows e.g. a Tailscale (100.x) address")
    adm["require_private_peer"] = True

    print("POS connection")
    check(A.lan_only_ok(Req("10.0.4.20"), settings.pos_networks()) and not A.lan_only_ok(Req("10.0.4.20", CF), settings.pos_networks()), "LAN yes, tunnel no")
    pos["allowed_networks"] = ["10.0.4.20/32"]
    check(A.lan_only_ok(Req("10.0.4.20"), settings.pos_networks()) and not A.lan_only_ok(Req("10.0.4.21"), settings.pos_networks()), "only the listed POS computer")
    pos["allowed_networks"] = []

    print("client address (rate-limit buckets)")
    check(A.client_ip(Req("127.0.0.1", CF)) == "198.51.100.7", "tunnel: the real visitor")
    check(A.client_ip(Req("10.0.4.50", {"X-Forwarded-For": "1.2.3.4"})) == "10.0.4.50", "a customer on the Wi-Fi cannot pretend to be someone else")

    print("order folders stay inside the orders directory")
    base = settings.upload_dir()
    f_ok = {"folder": str(base / "2026-11-18" / "001_[Ana] - Doc")}
    check(A._folder_of(f_ok) == Path(f_ok["folder"]), "normal order folder")
    check(A._folder_of({"folder": str(base.parent / "elsewhere")}) is None, "folder outside -> ignored")
    check(A._folder_of({"folder": str(base / ".." / ".." / "etc")}) is None, "../ traversal -> ignored")
    check(A._folder_of({"folder": ""}) is None and A._folder_of(None) is None, "empty -> None")
    cfg["security"]["confine_folders"] = False
    check(A._folder_of({"folder": "/tmp/x"}) == Path("/tmp/x"), "can be switched off")
    cfg["security"]["confine_folders"] = True

    print("virus-scan hook")
    scan = TMP / "scan.py"
    scan.write_text("import sys\nsys.exit(int(open(sys.argv[1]).read().strip() or 0))\n")
    folder = TMP / "f"; folder.mkdir()
    def run(code, **limits):
        (folder / "a.txt").write_text(str(code))
        c = json.loads(json.dumps(cfg)); c["limits"].update(scan_command=f'"{sys.executable}" "{scan}" {{file}}', **limits)
        try:
            return A._scan_files(folder, [{"saved": "a.txt", "original": "a.txt"}], c)
        except A.ApiError as e:
            return e.status
    check(run(0) == [], "clean file passes")
    check(run(1) == 400, "exit 1 = infected -> refused (default)")
    check(run(2, scan_infected_codes=[2]) == 400 and run(1, scan_infected_codes=[2]) != 400, "Windows Defender style: exit 2 = infected")
    check(isinstance(run(3), list) and run(3), "scanner failure is flagged for staff but does not block (default)")
    check(run(3, scan_fail_closed=True) == 503, "scanner failure blocks the order when scan_fail_closed=true")
    real_os = A.os
    A.os = types.SimpleNamespace(name="nt", path=os.path)
    try:
        check(A._scan_args('"C:\\Program Files\\Windows Defender\\MpCmdRun.exe" -Scan -ScanType 3 -File {file} -DisableRemediation', "C:\\o\\a.pdf")[0] == "C:\\Program Files\\Windows Defender\\MpCmdRun.exe", "Windows: quotes around the program path are removed")
    finally:
        A.os = real_os

    print("upload checks (what a customer can send)")
    ok = lambda name, data: A.check_upload(Up(name, data), cfg)[0]       # noqa: E731
    check(ok("a.docx", zbytes(["[Content_Types].xml", "word/document.xml"])), "real .docx accepted")
    check(not ok("a.docx", zbytes(["x.txt"])), "zip renamed .docx refused")
    check(not ok("a.docx", zbytes(["[Content_Types].xml", "word/vbaProject.bin"])), "macro .docx refused")
    check(not ok("a.txt", b"MZ\x90\x00program"), "program renamed .txt refused")
    check(not ok("a.exe", b"MZ\x90\x00"), ".exe refused")
    check(ok("a.pdf", b"%PDF-1.4 hello"), "PDF accepted")
    cfg["limits"]["deep_file_checks"] = False
    check(ok("a.docx", zbytes(["x.txt"])), "deep checks can be switched off")
    cfg["limits"]["deep_file_checks"] = True

    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
