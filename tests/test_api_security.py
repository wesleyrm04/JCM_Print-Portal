"""End-to-end security tests: starts the real server and attacks it over HTTP (stdlib only).
Covers: tunnel-port wall, session revocation, PIN policy, account lock-out, chunked-upload limit, JSON size cap,
hidden health details, fake / macro documents.   Run: python tests/test_api_security.py"""
import http.client
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import Client, Server, make_pdf

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def zbytes(names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return b.getvalue()


def token_of(client):
    return next(c.value for c in client.jar if c.name == "jcm_admin")


def with_token(srv, tok, path, method="GET", body=None):
    return Client(srv.base).req(method, path, body, {"Cookie": "jcm_admin=" + tok, "Content-Type": "application/json"})


def main():
    tmp = Path(tempfile.mkdtemp())
    cfg = json.load(open(Path(__file__).resolve().parent.parent / "config.json", encoding="utf-8"))
    cfg["analysis"]["use_libreoffice"] = False
    cfg["limits"]["max_request_mb"] = 1
    cfg["limits"]["max_json_mb"] = 0.01
    cfg["limits"]["scan_command"] = ""

    print("tunnel port wall")
    os.environ["JCM_PUBLIC_PORTS"] = "8150"
    srv = Server(8150, tmp / "o1", tmp / "d1", config=cfg)
    os.environ.pop("JCM_PUBLIC_PORTS")
    try:
        c = Client(srv.base)
        check(c.get("/admin", raw=True)[0] == 403, "/admin is refused on the tunnel port even with no tunnel headers")
        check(c.post_json("/api/admin/login", {"pin": "123456"})[0] == 403, "staff login refused there")
        check(c.req("GET", "/api/pos/ping", headers={"Authorization": "Bearer x"})[0] == 403, "POS API refused there")
        check(c.get("/", raw=True)[0] == 200, "the customer pages still work")
    finally:
        srv.stop()

    print("sessions, PIN policy, uploads, limits")
    srv = Server(8151, tmp / "o2", tmp / "d2", config=cfg)
    try:
        c = Client(srv.base)
        s, j, h = c.post_json("/api/admin/login", {"pin": "123456"})
        check(s == 200 and "SameSite=Strict" in h.get("Set-Cookie", ""), "login works; cookie is SameSite=Strict")
        tok = token_of(c)
        check(with_token(srv, tok, "/api/admin/orders")[0] == 200, "cookie works")
        for bad in ["1234", "123456", "000000", "abc"]:
            check(c.post_json("/api/admin/staff", {"name": "Maria", "pin": bad, "role": "owner"})[0] == 400, f"weak PIN {bad!r} refused")
        check(c.post_json("/api/admin/staff", {"name": "Staff", "pin": "482913", "role": "owner"})[0] == 400, "the name 'Staff' is reserved")
        check(c.post_json("/api/admin/staff", {"name": "Maria", "pin": "482913", "role": "owner"})[0] == 200, "good PIN accepted")
        check(c.post_json("/api/admin/staff", {"name": "Ben", "pin": "739206", "role": "staff"})[0] == 200, "staff account created")
        ben = Client(srv.base)
        check(ben.post_json("/api/admin/login", {"name": "Ben", "pin": "739206"})[0] == 200, "Ben logs in")
        ben_tok = token_of(ben)
        check(with_token(srv, ben_tok, "/api/admin/orders")[0] == 200, "Ben's session works")
        check(c.post_json("/api/admin/staff", {"name": "Ben", "pin": "851726", "role": "staff"})[0] == 200, "owner resets Ben's PIN")
        check(with_token(srv, ben_tok, "/api/admin/orders")[0] == 401, "a PIN reset ends Ben's old session")
        check(with_token(srv, tok, "/api/admin/orders")[0] == 200, "...but not the owner's own session")
        ben2 = Client(srv.base); ben2.post_json("/api/admin/login", {"name": "Ben", "pin": "851726"}); t2 = token_of(ben2)
        check(c.post_json("/api/admin/sessions/revoke-all", {})[1].get("ended", 0) >= 1 and with_token(srv, t2, "/api/admin/orders")[0] == 401 and with_token(srv, tok, "/api/admin/orders")[0] == 200, "'log out everyone else' ends other sessions, keeps mine")
        ben3 = Client(srv.base); ben3.post_json("/api/admin/login", {"name": "Ben", "pin": "851726"}); t3 = token_of(ben3)
        c.post_json("/api/admin/staff/delete", {"name": "Ben"})
        check(with_token(srv, t3, "/api/admin/orders")[0] == 401, "removing an account ends its session")
        c.post_json("/api/admin/logout", {})
        check(with_token(srv, tok, "/api/admin/orders")[0] == 401, "logout ends the session on the server (a copied cookie is dead)")

        # request size limits
        conn = http.client.HTTPConnection("127.0.0.1", 8151, timeout=30)
        conn.putrequest("POST", "/api/estimate"); conn.putheader("Transfer-Encoding", "chunked"); conn.putheader("Content-Type", "application/octet-stream"); conn.endheaders()
        try:
            for _ in range(30):
                conn.send(b"%x\r\n" % 100000 + b"a" * 100000 + b"\r\n")
            conn.send(b"0\r\n\r\n")
            status = conn.getresponse().status
        except (BrokenPipeError, ConnectionResetError, http.client.HTTPException, OSError):
            status = "closed"
        check(status in (413, "closed"), f"a chunked 3 MB upload (no Content-Length) is stopped at the 1 MB limit ({status})")
        s, j, _ = c.req("POST", "/api/admin/login", b"x" * 20000, {"Content-Type": "application/json"})
        check(s == 413, "20 KB to a JSON endpoint -> 413")

        # customer uploads
        def order(files):
            return c.post_form("/api/submit_order", [("customer_name", "Ana"), ("phone", ""), ("instructions", ""),
                                                      ("order_meta", json.dumps({"kind": "document"}))], files)
        s, j, _ = order([("files", "fake.docx", zbytes(["readme.txt"]))])
        check(s == 400, f"a random zip renamed .docx is refused ({j})")
        s, j, _ = order([("files", "macro.docx", zbytes(["[Content_Types].xml", "word/vbaProject.bin"]))])
        check(s == 400, "a macro-enabled .docx is refused")
        s, j, _ = order([("files", "prog.txt", b"MZ\x90\x00program")])
        check(s == 400, "a program renamed .txt is refused")
        s, j, _ = order([("files", "ok.pdf", make_pdf(["bw"]))])
        check(s == 200, "a normal PDF order still works")
        s, j, h = c.get("/health")
        check("Strict-Transport-Security" not in h and "Permissions-Policy" in h, "new security headers present")
    finally:
        out = srv.stop()
        if "Traceback" in out:
            print("SERVER LOG:\n", out[-2000:]); failures.append("server traceback")

    print("health hides server paths")
    srv = Server(8152, tmp / "o3", tmp / "d3", mount_point="/mnt/definitely_not_mounted")
    try:
        s, j, _ = Client(srv.base).get("/health")
        check(j["ok"] is False and j["storage"] == "problem" and "/mnt" not in json.dumps(j), "storage problem reported without naming a path")
    finally:
        srv.stop()

    print("account lock-out")
    cfg2 = json.loads(json.dumps(cfg)); cfg2["admin"]["allow_shared_pin"] = True
    srv = Server(8153, tmp / "o4", tmp / "d4", config=cfg2)
    try:
        c = Client(srv.base)
        c.post_json("/api/admin/login", {"pin": "123456"})
        c.post_json("/api/admin/staff", {"name": "Maria", "pin": "482913", "role": "owner"})
        codes = [Client(srv.base).post_json("/api/admin/login", {"name": "Maria", "pin": "000999"})[0] for _ in range(5)]
        check(codes == [401] * 5, "five wrong PINs are plain refusals")
        check(Client(srv.base).post_json("/api/admin/login", {"name": "Maria", "pin": "482913"})[0] == 429, "...then even the right PIN is refused for a while (lock-out is per account)")
    finally:
        srv.stop()

    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
