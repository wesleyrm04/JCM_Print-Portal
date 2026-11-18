"""Security checks that need no web server (pure Python): network rules, PIN policy, sessions, lock-out,
deep file checks, PDF/zip bombs, request-size limiter, config helpers.   Run: python tests/test_security.py"""
import asyncio
import io
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp())
os.environ["JCM_DATA_DIR"] = str(TMP / "data")
os.environ["JCM_ADMIN_PIN"] = "482913"
os.environ.pop("JCM_PUBLIC_PORTS", None)

from portal import auth, netsec, pricing, settings, storage  # noqa: E402

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def zbytes(names, data=b"x"):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, data)
    b.seek(0)
    return b


def main():
    print("network rules")
    check(netsec.is_private("10.0.4.37") and netsec.is_private("192.168.1.5") and netsec.is_private("127.0.0.1")
          and netsec.is_private("::1") and netsec.is_private("::ffff:10.0.4.5"), "private / loopback / mapped-IPv6 addresses recognised")
    check(not netsec.is_private("8.8.8.8") and not netsec.is_private("1.1.1.1") and not netsec.is_private("2606:4700::1111") and not netsec.is_private("?") and not netsec.is_private(""), "public / garbage addresses are not private")
    check(netsec.peer_in("10.0.4.37", []) and netsec.peer_in("1.2.3.4", None), "no allow-list = no restriction")
    check(netsec.peer_in("10.0.4.37", ["10.0.4.0/24"]) and not netsec.peer_in("10.0.5.1", ["10.0.4.0/24"]), "allow-list by network")
    check(netsec.peer_in("127.0.0.1", ["127.0.0.1", "10.0.4.37/32"]) and not netsec.peer_in("10.0.4.38", ["127.0.0.1", "10.0.4.37/32"]), "single addresses work")
    check(not netsec.peer_in("10.0.4.37", ["not-an-ip"]), "a typo in the allow-list denies everyone (fails closed)")
    check(netsec.peer_in("10.0.4.7", "10.0.4.0/24, 127.0.0.1"), "allow-list given as comma text")
    hd = lambda d: (lambda n: d.get(n, ""))        # noqa: E731
    check(netsec.real_client_ip("127.0.0.1", hd({"cf-connecting-ip": "198.51.100.7"}), True) == "198.51.100.7", "tunnel on this PC: real visitor address used")
    check(netsec.real_client_ip("10.0.4.50", hd({"cf-connecting-ip": "198.51.100.7"}), True) == "10.0.4.50", "a customer on the Wi-Fi cannot fake an address with headers")
    check(netsec.real_client_ip("127.0.0.1", hd({"x-forwarded-for": "6.6.6.6, 198.51.100.8"}), True) == "198.51.100.8", "X-Forwarded-For: the entry our proxy appended is used, not the first one")
    check(netsec.real_client_ip("127.0.0.1", hd({"cf-connecting-ip": "<script>"}), True) == "127.0.0.1", "garbage in the header is ignored")
    check(netsec.real_client_ip("127.0.0.1", hd({}), False) == "127.0.0.1", "no proxy headers: the peer address")
    check(netsec.real_client_ip("10.0.4.9", hd({"cf-connecting-ip": "198.51.100.7"}), True, ["10.0.4.9"]) == "198.51.100.7", "tunnel on another computer works when listed in trusted_proxies")

    print("settings helpers")
    os.environ["JCM_PUBLIC_PORTS"] = "1013, 8080,abc,99999"
    check(settings.public_ports() == [1013, 8080], f"JCM_PUBLIC_PORTS parsed safely {settings.public_ports()}")
    os.environ.pop("JCM_PUBLIC_PORTS")
    os.environ["JCM_ADMIN_NETWORKS"] = "127.0.0.1/32;10.0.4.37/32"
    check(settings.admin_networks() == ["127.0.0.1/32", "10.0.4.37/32"], "JCM_ADMIN_NETWORKS parsed")
    os.environ.pop("JCM_ADMIN_NETWORKS")
    cfg = settings.get_config()
    check(cfg["admin"]["require_private_peer"] is True and cfg["limits"]["deep_file_checks"] is True and cfg["limits"]["max_json_mb"] == 2, "secure defaults are on")

    print("PIN policy")
    for bad in ["1234", "12345", "123456", "000000", "654321", "password", "abcdefg", "", "a" * 65]:
        try:
            auth.validate_pin(bad)
            check(False, f"refused {bad[:12]!r}")
        except ValueError:
            check(True, f"refused {bad[:12]!r}")
    check(auth.validate_pin(" 482913 ") == "482913" and auth.validate_pin("correct horse 7") == "correct horse 7", "good PIN / password accepted")
    try:
        auth.set_staff("staff", "482913", "staff")
        check(False, "the name 'Staff' is reserved")
    except ValueError:
        check(True, "the name 'Staff' is reserved")

    print("accounts, hashes, sessions")
    auth.set_staff("Maria", "482913", "owner")
    auth.set_staff("Ben", "739206", "staff")
    acc = json.loads((TMP / "data" / "staff.json").read_text())["staff"]
    check(all(a["iter"] == 600000 for a in acc), "new accounts use 600 000 PBKDF2 rounds")
    check(auth.check_staff("maria", "482913") == {"name": "Maria", "role": "owner"} and auth.check_staff("Maria", "000000") is None and auth.check_staff("Nobody", "482913") is None, "login checks")
    # a v5-style account (200 000 rounds, no 'iter') still works and is upgraded
    import hashlib, secrets
    salt = secrets.token_bytes(16)
    old = {"name": "Old", "role": "staff", "salt": salt.hex(), "hash": hashlib.pbkdf2_hmac("sha256", b"1234", salt, 200000).hex()}
    data = json.loads((TMP / "data" / "staff.json").read_text()); data["staff"].append(old)
    (TMP / "data" / "staff.json").write_text(json.dumps(data))
    check(auth.check_staff("Old", "1234") is not None, "an old 4-digit v5 account can still log in once")
    check(any(a["name"] == "Old" and a.get("iter") == 600000 for a in json.loads((TMP / "data" / "staff.json").read_text())["staff"]), "...and its hash was upgraded")
    t1 = auth.make_token(12, "Maria", "owner")
    check(auth.valid_token(t1) == {"name": "Maria", "role": "owner"}, "session valid")
    p, sig = t1.rsplit(".", 1)
    check(auth.valid_token(p + "." + "0" * len(sig)) is None and auth.valid_token("") is None and auth.valid_token("a.b") is None, "forged / garbage tokens refused")
    auth.set_staff("Maria", "482913", "owner"); auth.set_staff("Zed", "551827", "owner")
    auth.set_staff("Maria", "482913", "staff")
    check(auth.valid_token(t1) == {"name": "Maria", "role": "staff"}, "a demoted owner loses owner rights at once (role is read from the account)")
    auth.revoke_session(t1)
    check(auth.valid_token(t1) is None, "logout really ends the session on the server")
    t2, t3 = auth.make_token(12, "Ben", "staff"), auth.make_token(12, "Zed", "owner")
    auth.delete_staff("Ben")
    check(auth.valid_token(t2) is None, "removing an account ends its sessions")
    keep = auth.session_id(t3)
    t4 = auth.make_token(12, "Maria", "staff")
    check(auth.revoke_all(keep_sid=keep) >= 1 and auth.valid_token(t3) is not None and auth.valid_token(t4) is None, "'log out everyone else' keeps the caller")
    check(json.loads((TMP / "data" / "sessions.json").read_text()) is not None, "sessions survive a restart (stored on disk)")
    auth._sessions.update(loaded=False, items={})
    check(auth.valid_token(t3) is not None, "...and are reloaded after a restart")
    short = auth.make_token(-1, "Zed", "owner")
    check(auth.valid_token(short) is None, "expired session refused")
    sh = auth.make_token(12, "Staff", "owner")
    check(settings.get_config()["admin"]["allow_shared_pin"] is False and auth.valid_token(sh) is None, "shared-PIN sessions stop working once the shared PIN is switched off and named accounts exist")
    settings.get_config()["admin"]["allow_shared_pin"] = True
    check(auth.valid_token(sh) == {"name": "Staff", "role": "owner"}, "...and work while it is allowed")
    settings.get_config()["admin"]["allow_shared_pin"] = False
    mode = oct(os.stat(TMP / "data" / "secret.key").st_mode & 0o777)
    check(mode == "0o600" and oct(os.stat(TMP / "data" / "staff.json").st_mode & 0o777) == "0o600" and oct(os.stat(TMP / "data" / "sessions.json").st_mode & 0o777) == "0o600", f"secret files are owner-only ({mode})")
    k = auth.pos_key(); auth.rotate_pos_key()
    check(oct(os.stat(TMP / "data" / "pos_api_key.txt").st_mode & 0o777) == "0o600" and not auth.check_pos_key(k), "rotated POS key file is owner-only; old key refused")

    print("lock-out")
    lk = auth.Lockout()
    for _ in range(5):
        lk.fail("acct:maria")
    check(lk.locked("acct:maria", 5, 600) and not lk.locked("acct:ben", 5, 600), "5 misses lock that account only")
    lk.clear("acct:maria")
    check(not lk.locked("acct:maria", 5, 600), "a good login clears the count")

    print("deep file checks")
    ok = lambda ext, f: storage.deep_check(ext, f)[0]      # noqa: E731
    check(ok("docx", zbytes(["[Content_Types].xml", "word/document.xml"])), "real-looking .docx accepted")
    check(not ok("docx", zbytes(["readme.txt"])), "any random zip renamed .docx refused")
    check(not ok("docx", io.BytesIO(b"PK\x03\x04" + b"junk" * 20)), "broken zip refused")
    check(not ok("docx", zbytes(["[Content_Types].xml", "word/vbaProject.bin"])), "macro-enabled OOXML refused")
    check(not ok("xlsx", zbytes(["[Content_Types].xml", "../../evil.dll"])), "path-traversal zip refused")
    check(ok("odt", zbytes(["mimetype", "content.xml"])) and not ok("odt", zbytes(["mimetype", "Basic/Standard/m.xml"])), ".odt ok, with macros refused")
    check(not ok("txt", io.BytesIO(b"MZ\x90\x00 program")) and ok("txt", io.BytesIO(b"hello")), "program renamed .txt refused")
    check(not ok("pdf", io.BytesIO(b"MZ%PDF")) and ok("pdf", io.BytesIO(b"%PDF-1.7\n")), "program renamed .pdf refused")
    big = zbytes(["[Content_Types].xml"]); 
    with tempfile.TemporaryFile() as tf:
        with zipfile.ZipFile(tf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", "x")
            for i in range(5100):
                z.writestr(f"f{i}.xml", "")
        tf.seek(0)
        check(not ok("docx", tf), "zip with thousands of parts refused")
    d = TMP / "m.doc"
    d.write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 500 + "_VBA_PROJECT_CUR".encode("utf-16le") + b"\0" * 50)
    e = TMP / "p.doc"; e.write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 500)
    check(storage.has_ole_macros(d) and not storage.has_ole_macros(e), "macros inside old .doc/.xls/.ppt are detected")

    print("PDF / zip bombs")
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 14400 14400]>>endobj\ntrailer<</Root 1 0 R>>\n"
    (TMP / "big.pdf").write_bytes(pdf)
    t0 = time.time()
    r = pricing.analyze_pdf(TMP / "big.pdf", cfg)
    check(r["pages"] == 1 and time.time() - t0 < 20, f"a 14400 x 14400 pt page is analysed within the pixel cap ({time.time() - t0:.1f}s)")
    (TMP / "bomb.docx").write_bytes(zbytes(["docProps/app.xml"], b"<Pages>3</Pages>" + b"0" * 5_000_000).getvalue())
    (TMP / "ok.docx").write_bytes(zbytes(["docProps/app.xml"], b"<Pages>3</Pages>").getvalue())
    check(pricing._office_pages_from_xml(TMP / "bomb.docx", "docx") is None and pricing._office_pages_from_xml(TMP / "ok.docx", "docx") == 3, "oversized metadata part is not read (zip-bomb guard)")

    print("streaming request-size limit (ASGI)")
    async def run(chunks, max_bytes):
        st = {"seen": 0, "exceeded": False}
        queue = [{"type": "http.request", "body": c, "more_body": True} for c in chunks]
        async def receive():
            return queue.pop(0) if queue else {"type": "http.disconnect"}
        rx = netsec.limited_receive(receive, max_bytes, st)
        try:
            while True:
                m = await rx()
                if m["type"] != "http.request":
                    return "ok", st
        except netsec.BodyTooLarge:
            return "too_big", st
    r1, s1 = asyncio.run(run([b"a" * 1000] * 5, 10_000))
    r2, s2 = asyncio.run(run([b"a" * 1000] * 20, 10_000))      # a chunked upload with no Content-Length
    check(r1 == "ok" and r2 == "too_big" and s2["exceeded"], "bytes are counted as they arrive, so chunked uploads cannot dodge the limit")

    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
