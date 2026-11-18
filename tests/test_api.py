"""End-to-end test: starts the real server and drives it over HTTP (stdlib only).
Run:  python tests/test_api.py"""
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import Client, Server, make_jpeg, make_pdf
from portal import docgen

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def order(c, name, files, meta, extra=None, phone=""):
    fields = [("customer_name", name), ("phone", phone), ("instructions", "please staple"),
              ("order_meta", json.dumps(meta))] + (extra or [])
    return c.post_form("/api/submit_order", fields, [("files", n, d) for n, d in files])


def main():
    tmp = Path(tempfile.mkdtemp())
    up, data = tmp / "orders", tmp / "data"
    cfg = json.load(open(Path(__file__).resolve().parent.parent / "config.json"))
    cfg["analysis"]["use_libreoffice"] = False
    cfg["limits"]["max_file_mb"] = 1            # makes the size-limit test cheap
    cfg["limits"]["max_request_mb_tunnel"] = 1   # makes the Cloudflare-limit test cheap
    cfg["limits"]["blocked_extensions"] = ["doc"]
    cfg["admin"]["allow_shared_pin"] = True       # these tests log in with the shared PIN next to named accounts
    scan = tmp / "scan.py"
    scan.write_text("import sys\nsys.exit(1 if b'EICAR' in open(sys.argv[1],'rb').read() else 0)\n")
    cfg["limits"]["scan_command"] = f'"{sys.executable}" "{scan}" {{file}}'
    srv = Server(8123, up, data, config=cfg)
    c = Client(srv.base)
    try:
        print("basics")
        s, j, h = c.get("/health")
        check(s == 200 and j["ok"], "health ok")
        s, j, h = c.get("/api/config")
        check(s == 200 and len(j["layouts"]) >= 7 and "storage" not in j, "config served, no server paths leaked")
        check(h.get("X-Content-Type-Options") == "nosniff" and "script-src 'self'" in h.get("Content-Security-Policy", ""), "security headers present")
        s, j, h = c.get("/api/config")
        check("no-store" in h.get("Cache-Control", ""), "API responses are not cached")

        print("estimate")
        pdf_mixed = make_pdf(["bw", "color", "bw"])
        s, j, _ = c.post_form("/api/estimate", [], [("file", "a.pdf", pdf_mixed)])
        f = j["files"][0]
        check(s == 200 and f["pages"] == 3 and f["color_pages"] == 1 and f["bw_pages"] == 2, f"mixed pdf 3 pages / 1 colour ({f['pages']}/{f['color_pages']})")
        s, j, _ = c.post_form("/api/estimate", [], [("file", "evil.pdf", b"MZ\x90\x00not a pdf" * 10)])
        check(j["files"][0]["accepted"] is False, "fake pdf rejected by estimate")
        s, j, _ = c.post_form("/api/estimate", [], [("file", "run.exe", b"MZ" * 10)])
        check(j["files"][0]["accepted"] is False, ".exe rejected by estimate")

        print("document order")
        files = [("one.pdf", make_pdf(["bw", "bw"])), ("two.pdf", make_pdf(["color", "bw", "bw"])), ("p.jpg", make_jpeg())]
        meta = {"kind": "document", "color_mode": "auto", "copies": 2, "paper": "A4", "client_total": 1.0, "status": "Paid"}
        s, j, _ = order(c, "Juan Dela Cruz", files, meta)
        # 4 bw pages x3 + 1 medium-colour page x7.50 + 1 photo (full colour) x10 = 29.50, x2 copies = 59
        check(s == 200 and j["order_id"].endswith("-001") and j["total"] == 59.0, f"ALL files priced with the POS formula, copies applied: total {j.get('total')} == 59")
        check("folder" not in j, "server path is not leaked to customers")
        d = next(up.glob("*/001_*"))
        oj = json.load(open(d / "order.json"))
        check(oj["status"] == "Pending" and oj["paid"] is False, "client cannot set status/paid")
        check(any("shown PHP 1.00" in n for n in oj["review_notes"]), "price mismatch with the browser is recorded")
        check((d / "instructions.txt").exists() and len(oj["files"]) == 3, "instructions.txt + files saved")
        first_id, first_code = j["order_id"], j["access_code"]

        print("errors are clean and do not burn numbers")
        s, j, _ = order(c, "Ana", [("empty.pdf", b"")], {"kind": "document"})
        check(s == 400 and "empty" in j["detail"] and not j["detail"].startswith("400"), f"empty file -> clean 400: {j.get('detail')}")
        s, j, _ = order(c, "Ana", [("big.pdf", b"%PDF-1.4 " + b"0" * (2 * 1024 * 1024))], {"kind": "document"})
        check(s == 400 and "bigger than" in j["detail"], "oversize file -> clean 400")
        s, j, _ = order(c, "", [("a.pdf", pdf_mixed)], {"kind": "document"})
        check(s == 400 and "name" in j["detail"].lower(), "missing name -> 400")
        s, j, _ = order(c, "Ana", [("a.pdf", pdf_mixed)], {"kind": "nonsense"})
        check(s == 400, "unknown kind -> 400")
        s, j, _ = order(c, "Ana", [], {"kind": "document"})
        check(s == 400, "no files -> 400")
        s, j, _ = order(c, "Ana", [("a.pdf", make_pdf(["bw"]))], {"kind": "document"})
        check(s == 200 and j["order_id"].endswith("-002") and j["total"] == 3.0, "next order is 002 (no gap) and 1 page = 3.00")

        print("photo order")
        imgs = [(f"{i}.jpg", make_jpeg((i * 40, 90, 120))) for i in range(3)]
        layout_pdf = make_pdf(["color", "color"])
        meta = {"kind": "photo", "layout": "10x15", "paper_size": "A4", "paper_type": "180gsm", "copies": 1, "client_total": 60}
        s, j, _ = order(c, "Maria", imgs + [("Generated_Photo_Layout.pdf", layout_pdf)], meta)
        check(s == 200 and j["total"] == 60.0 and not j["needs_review"], f"3 photos @ 2/sheet = 2 sheets x 30 = 60 (got {j.get('total')})")
        s, j, _ = order(c, "Maria", imgs + [("Generated_Photo_Layout.pdf", make_pdf(["color"]))], meta)
        check(s == 200 and j["needs_review"], "layout PDF with wrong page count is flagged for staff")
        s, j, _ = order(c, "Maria", imgs + [("Generated_Photo_Layout.pdf", layout_pdf)], dict(meta, skipped=["iphone.heic"]))
        check(s == 200 and j["needs_review"], "photos the browser could not place are flagged for staff")
        oj2 = json.load(open(sorted(up.glob("*/*Photo Print*/order.json"))[-1]))
        check(any("iphone.heic" in n for n in oj2["review_notes"]), "the file name is in the staff notes")
        s, j, _ = order(c, "Maria", imgs, dict(meta, layout="nope"))
        check(s == 400, "unknown layout -> 400")

        print("rush id / typed / tickets")
        s, j, _ = order(c, "Pedro", imgs[:2] + [("Generated_RushID_Layout.pdf", make_pdf(["color"]))], {"kind": "rushid", "package": "C"})
        check(s == 200 and j["total"] == 60.0, "Rush ID package C x2 photos = 60")
        text = "Letter of intent.\n" * 70
        s, j, _ = order(c, "Lola", [], {"kind": "typed", "copies": 2}, extra=[("typed_text", text)])
        pages = docgen.count_pages(text)
        check(s == 200 and j["total"] == 5 + pages * 3 * 2, f"typed doc priced server-side ({j.get('total')})")
        d = next(up.glob("*/*Typed Document*"))
        check({"Typed_Document.pdf", "Typed_Document.docx", "Typed_Document.txt"} <= {p.name for p in d.iterdir()}, "typed doc produced PDF + DOCX + TXT")
        s, j, _ = c.post_json("/api/typed/estimate", {"text": text, "copies": 1})
        check(s == 200 and j["pages"] == pages, "typed estimate endpoint matches")
        s, j, _ = order(c, "Ben", [], {"kind": "repair", "details": {"issue": "No power"}})
        check(s == 400 and "mobile" in j["detail"], "repair ticket needs a phone number")
        s, j, _ = order(c, "Ben", [("pic.jpg", make_jpeg())], {"kind": "repair", "details": {"device_type": "Laptop", "issue": "No power"}}, phone="09171234567")
        check(s == 200 and j["order_id"].startswith("R-") and j["total"] == 0, f"repair ticket {j.get('order_id')}")
        rid, rcode = j["order_id"], j["access_code"]
        s, j, _ = order(c, "Ben", [("doc.pdf", pdf_mixed)], {"kind": "repair", "details": {"issue": "x"}}, phone="09171234567")
        check(s == 400, "tickets accept images only")

        print("customer status lookup")
        s, j, _ = c.get(f"/api/status?id={first_id}&code={first_code}")
        check(s == 200 and j["status"] == "Pending" and j["first_name"] == "Juan", "status lookup works with the right code")
        s, j, _ = c.get(f"/api/status?id={first_id}&code=WRONG1")
        check(s == 404, "wrong code -> 404")
        s, j, _ = c.get("/api/status?id=../../etc&code=x")
        check(s == 404, "bad id -> 404")

        print("staff area")
        check(c.get("/api/admin/orders")[0] == 401, "orders list needs login")
        check(c.get(f"/api/admin/orders/{first_id}")[0] == 401 and c.get(f"/api/admin/orders/{first_id}/zip")[0] == 401, "detail/zip need login")
        check(c.post_json("/api/admin/login", {"pin": "000000"})[0] == 401, "wrong PIN rejected")
        s, j, h = c.post_json("/api/admin/login", {"pin": "123456"})
        check(s == 200 and "HttpOnly" in h.get("Set-Cookie", ""), "login ok, cookie is HttpOnly")
        s, j, _ = c.get("/api/admin/orders")
        check(s == 200 and len(j["orders"]) >= 6 and j["poll"]["latest"], f"orders listed ({len(j['orders'])})")
        s, j, _ = c.get("/api/admin/orders?q=Juan")
        check(len(j["orders"]) == 1, "search by name")
        s, j, _ = c.get(f"/api/admin/orders/{first_id}")
        check(s == 200 and len(j["file_list"]) == 5 and j["statuses"][0] == "Pending", "detail has file list (3 files + 2 json/txt)")
        s, j, _ = c.post_json(f"/api/admin/orders/{first_id}", {"status": "Printing", "paid": True, "payment_method": "GCash", "total": 70, "public_note": "Almost done"}, method="PATCH")
        check(s == 200 and j["json_synced"], "staff update saved to DB and order.json")
        oj = json.load(open(next(up.glob("*/001_*")) / "order.json"))
        check(oj["status"] == "Printing" and oj["paid"] and oj["total"] == 70 and oj["computed_total"] == 59 and len(oj["history"]) >= 5 and all("by" in h for h in oj["history"]), "order.json updated, original computed total kept, every change logged with who did it")
        check(c.post_json(f"/api/admin/orders/{first_id}", {"status": "Bogus"}, method="PATCH")[0] == 400, "invalid status rejected")
        check(c.post_json(f"/api/admin/orders/{rid}", {"status": "Repairing"}, method="PATCH")[0] == 200, "repair statuses work")
        s, j, _ = c.get(f"/api/status?id={first_id}&code={first_code}")
        check(j["status"] == "Printing" and j["public_note"] == "Almost done" and j["paid"], "customer sees the update")
        s, body, h = c.get(f"/api/admin/orders/{first_id}/file/one.pdf", raw=True)
        s, body, h = c.get(f"/api/admin/orders/{first_id}/file/01_one.pdf", raw=True)
        check(s == 200 and body[:4] == b"%PDF", "file download")
        check(c.get(f"/api/admin/orders/{first_id}/file/..%2Forder.json", raw=True)[0] == 404, "no path traversal")
        s, body, h = c.get(f"/api/admin/orders/{first_id}/zip", raw=True)
        check(s == 200 and "01_one.pdf" in zipfile.ZipFile(io.BytesIO(body)).namelist(), "zip download")
        s, body, h = c.get(f"/admin/slip/{first_id}", raw=True)
        check(s == 200 and first_code.encode() in body and b"Juan Dela Cruz" in body, "printable slip")
        s, j, _ = c.get("/api/admin/summary?from=2000-01-01&to=2999-01-01")
        check(s == 200 and j["totals"]["orders"] >= 6 and j["totals"]["paid"] == 70, f"sales summary ({j['totals']})")
        s, body, h = c.get("/api/admin/export.csv?from=2000-01-01&to=2999-01-01", raw=True)
        check(s == 200 and b"Juan Dela Cruz" in body and "text/csv" in h["Content-Type"], "CSV export")
        s, j, _ = c.get("/api/admin/poll")
        check(s == 200 and j["waiting"] >= 3, f"poll reports waiting orders ({j['waiting']})")
        s, j, _ = c.post_json("/api/admin/reindex", {})
        check(s == 200 and j["added"] == 0, "re-scan finds nothing new")
        import sqlite3
        os.remove(data / "portal.db")      # simulate losing the index
        s, j, _ = c.post_json("/api/admin/reindex", {})
        s, j, _ = c.get("/api/admin/orders?date=all")
        check(len(j["orders"]) >= 6, f"index rebuilt from order.json files ({len(j['orders'])} orders)")
        print("quote endpoint = same numbers the order will get")
        s, j, _ = c.post_json("/api/quote", {"files": [{"bw_pages": 4, "tq_pages": 1, "full_pages": 1}], "copies": 2, "color_mode": "auto", "paper": "A4"})
        check(s == 200 and j["total"] == 59.0, f"quote for the first order = {j.get('total')}")
        s, j, _ = c.post_json("/api/quote", {"files": [{"bw_pages": 4}], "duplex": True, "paper": "Long"})
        check(j["total"] == 4 * 4 - 2, f"two-sided Long: 4 pages x 4.00 - 2 sheets x 1.00 = {j['total']}")

        print("queue position on the status page")
        s, j, _ = c.get(f"/api/status?id={first_id}&code={first_code}")
        check(j["ahead"] == 0, "first order has nobody ahead")
        s, jq, _ = order(c, "Queue Tester", [("q.pdf", make_pdf(["bw"]))], {"kind": "document"})
        s, j, _ = c.get(f"/api/status?id={jq['order_id']}&code={jq['access_code']}")
        check(j["ahead"] >= 1, f"a later order sees the orders ahead of it ({j['ahead']})")
        s, j2, _ = c.post_json("/api/quote", {"files": []})
        check(s == 200 and j2["total"] == 0, "empty quote is 0")

        print("security: cross-site requests and blocked files")
        s, j, _ = c.req("POST", "/api/admin/login", json.dumps({"pin": "123456"}).encode(), {"Content-Type": "application/json", "Origin": "https://evil.example"})
        check(s == 403, "login from another website is blocked (CSRF)")
        s, j, _ = c.req("POST", "/api/admin/login", json.dumps({"pin": "123456"}).encode(), {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"})
        check(s == 403, "Sec-Fetch-Site: cross-site is blocked")
        s, j, _ = c.req("POST", "/api/typed/estimate", json.dumps({"text": "x"}).encode(), {"Content-Type": "application/json", "Origin": srv.base})
        check(s == 200, "same-site browser requests still work")
        s, j, _ = order(c, "Ana", [("old.doc", b"\xd0\xcf\x11\xe0" + b"0" * 100)], {"kind": "document"})
        check(s == 400 and "not a supported" in j["detail"], ".doc is refused when listed in blocked_extensions")
        s, j, _ = order(c, "Ana", [("EICAR.pdf", b"%PDF-1.4 EICAR-TEST " + b"0" * 50)], {"kind": "document"})
        check(s == 400 and "virus scan" in j["detail"], "virus-scan hook rejects a flagged upload")
        check(not list(up.glob("*/*EICAR*")), "…and leaves no files behind")

        print("POS connection (LAN only, key protected)")
        POS = {"Authorization": "Bearer " + open(data / "pos_api_key.txt").read().strip()}
        check(c.req("GET", "/api/pos/ping")[0] == 401, "POS API refuses requests without the key")
        check(c.req("GET", "/api/pos/ping", headers={"Authorization": "Bearer nope"})[0] == 401, "…and with a wrong key")
        s, j, _ = c.req("GET", "/api/pos/ping", headers=POS)
        check(s == 200 and j["ok"] and j["prices_source"] == "portal", "ping works with the key")
        check(c.req("GET", "/api/pos/ping", headers=dict(POS, **{"CF-Connecting-IP": "203.0.113.7"}))[0] == 403, "POS API is blocked for internet (tunnel) visitors even with the key")
        s, j, _ = c.req("GET", "/api/pos/orders?status=all&category=print", headers=POS)
        check(s == 200 and len(j["orders"]) >= 3 and j["orders"][0]["customer"], f"POS lists print orders ({len(j['orders'])})")
        s, j, _ = c.req("GET", f"/api/pos/orders/{first_id}", headers=POS)
        check(s == 200 and len(j["file_list"]) >= 3 and j["options"]["copies"] == 2, "POS can read an order with its options and file list")
        fname = [f["name"] for f in j["file_list"] if f["name"].endswith(".pdf")][0]
        s, body, _ = c.req("GET", f"/api/pos/orders/{first_id}/file/{fname}", headers=POS, raw=True)
        check(s == 200 and body[:4] == b"%PDF", "POS can download the customer's file")
        check(c.req("GET", f"/api/pos/orders/{first_id}/file/..%2Forder.json", headers=POS, raw=True)[0] == 404, "no path tricks on POS downloads")
        s, j, _ = c.req("PATCH", f"/api/pos/orders/{first_id}", json.dumps({"status": "Ready for Pickup", "claimed_by": "Maria", "pos_ref": "OR-000123", "total": 59, "actor": "Maria"}).encode(), dict(POS, **{"Content-Type": "application/json"}))
        check(s == 200, "POS can update the status")
        s, j, _ = c.get(f"/api/status?id={first_id}&code={first_code}")
        check(j["status"] == "Ready for Pickup", "customer sees the POS status change")
        oj = json.load(open(next(up.glob("*/001_*/order.json"))))
        check(oj["claimed_by"] == "Maria" and oj["pos_ref"] == "OR-000123" and any(h["by"] == "POS: Maria" for h in oj["history"]), "POS changes are logged as 'POS: <cashier>'")
        s, j, _ = c.req("PATCH", f"/api/pos/orders/{first_id}", json.dumps({"status": "Bogus"}).encode(), dict(POS, **{"Content-Type": "application/json"}))
        check(s == 400, "POS cannot set an invalid status")
        s, j, _ = c.req("PUT", "/api/pos/rates", json.dumps({"rates": {"rate_bw": 4, "rate_doc_full": 12, "rate_package_a": 22, "junk": 9}}).encode(), dict(POS, **{"Content-Type": "application/json"}))
        check(s == 200 and "junk" not in j["applied"], f"POS can push its price list ({j.get('applied')})")
        s, j, _ = c.post_json("/api/quote", {"files": [{"bw_pages": 2, "full_pages": 1}]})
        check(j["total"] == 2 * 4 + 12, f"customers are now quoted with the POS prices ({j['total']})")
        s, j, _ = c.get("/api/config")
        check(j["prices_source"] == "pos" and [p["price"] for p in j["rush_id"]["packages"]][0] == 22, "public config shows POS prices and Rush ID package price")
        s, j, _ = c.req("PUT", "/api/pos/rates", json.dumps({"rates": {"evil": 1}}).encode(), dict(POS, **{"Content-Type": "application/json"}))
        check(s == 400, "a price list without rate_bw is rejected")
        c.post_json("/api/admin/login", {"pin": "123456"})
        s, j, _ = c.get("/api/admin/pos")
        check(s == 200 and j["last_seen"] and j["prices_source"] == "pos", "staff page shows when the POS last connected")
        s, j, _ = c.post_json("/api/admin/pos/rotate", {})
        check(s == 200 and j["key"] != POS["Authorization"][7:] and c.req("GET", "/api/pos/ping", headers=POS)[0] == 401, "rotating the key locks out the old one")

        print("named staff accounts, roles and audit trail")
        c.post_json("/api/admin/login", {"pin": "123456"})
        s, j, _ = c.post_json("/api/admin/staff", {"name": "Maria", "pin": "482913", "role": "owner"})
        check(s == 200 and j["staff"][0]["name"] == "Maria", "owner (shared-PIN session) can create the first account")
        check(c.post_json("/api/admin/staff", {"name": "Ben", "pin": "56ab", "role": "staff"})[0] == 400, "PIN must be digits")
        check(c.post_json("/api/admin/staff", {"name": "Ben", "pin": "739206", "role": "staff"})[0] == 200, "staff account created")
        s, j, _ = Client(srv.base).get("/api/admin/staff-names")
        check(j["names"] == ["Ben", "Maria"] and j["shared_pin"] is True, f"login drop-down lists names {j['names']}")
        ben = Client(srv.base)
        check(ben.post_json("/api/admin/login", {"name": "Ben", "pin": "0000"})[0] == 401, "wrong PIN for a named account is refused")
        check(ben.post_json("/api/admin/login", {"name": "Nobody", "pin": "739206"})[0] == 401, "unknown name is refused")
        s, j, _ = ben.post_json("/api/admin/login", {"name": "Ben", "pin": "739206"})
        check(s == 200 and j["name"] == "Ben" and j["role"] == "staff", "named login works")
        check(ben.get("/api/admin/me")[1]["name"] == "Ben", "session knows who is logged in")
        check(ben.get("/api/admin/orders")[0] == 200, "staff can work with orders")
        check(ben.get("/api/admin/staff")[0] == 403 and ben.get("/api/admin/pos")[0] == 403 and ben.get("/api/admin/backup")[0] == 403, "staff cannot manage accounts, see the POS key or the backups")
        check(ben.post_json("/api/admin/pos/rotate", {})[0] == 403, "staff cannot rotate the POS key")
        ben.post_json(f"/api/admin/orders/{first_id}", {"public_note": "Ben was here"}, method="PATCH")
        oj = json.load(open(next(up.glob("*/001_*/order.json"))))
        check(any(h["by"] == "Ben" for h in oj["history"]), "the order history names the real person (Ben)")
        maria = Client(srv.base)
        check(maria.post_json("/api/admin/login", {"name": "maria", "pin": "482913"})[1]["role"] == "owner", "names are not case-sensitive; owner role")
        check(maria.post_json("/api/admin/staff/delete", {"name": "Maria"})[0] == 400, "cannot remove your own account")
        s, j, _ = maria.post_json("/api/admin/staff/delete", {"name": "Ben"})
        check(s == 200 and ben.get("/api/admin/orders")[0] == 401, "removing an account ends their session immediately")
        check(c.post_json("/api/admin/staff", {"name": "Maria", "pin": "482913", "role": "staff"})[0] == 400, "the last owner cannot be demoted")

        print("backups")
        s, j, _ = maria.post_json("/api/admin/backup", {})
        check(s == 200 and j["name"].startswith("portal-"), f"owner can make a backup ({j.get('name')})")
        import zipfile as zf, sqlite3 as sq
        z = zf.ZipFile(data / "backups" / j["name"])
        names = z.namelist()
        check("portal.db" in names and "staff.json" in names and "admin_pin.txt" not in names and not any("secret" in n or "pos_api_key" in n for n in names), f"backup holds the index + staff accounts, but no PIN/keys ({names})")
        dbp = tmp / "restored.db"; dbp.write_bytes(z.read("portal.db"))
        check(sq.connect(dbp).execute("select count(*) from orders").fetchone()[0] >= 6, "the backed-up index opens and has the orders")
        s, body, _ = maria.get("/api/admin/backup/download", raw=True)
        check(s == 200 and body[:2] == b"PK", "owner can download the latest backup")

        print("warnings, GCash reference")
        s, j, _ = c.post_form("/api/submit_order", [("customer_name", "Gina"), ("phone", ""), ("order_meta", json.dumps({"kind": "document"})), ("pay_ref", "GCASH 123-456 <b>!!")],
                              [("files", "sheet.xls", b"\xd0\xcf\x11\xe0" + b"0" * 200), ("files", "ok.pdf", make_pdf(["bw"]))])
        check(s == 200, "order with an old .xls and a GCash reference accepted")
        d = next(up.glob(f"*/*Gina*")); oj = json.load(open(d / "order.json"))
        check(oj["customer_pay_ref"] == "GCASH 123-456 b" and any("macros" in w for w in oj["warnings"]), f"reference cleaned ({oj['customer_pay_ref']!r}) and macro warning recorded")
        check("GCASH 123-456" in (d / "instructions.txt").read_text() and "WARNING" in (d / "instructions.txt").read_text(), "staff see both in instructions.txt")
        POS = {"Authorization": "Bearer " + open(data / "pos_api_key.txt").read().strip()}      # the key was rotated above
        s, jd, _ = c.req("GET", f"/api/pos/orders/{j['order_id']}", headers=POS)
        check(jd["customer_pay_ref"] == "GCASH 123-456 b" and jd["warnings"], "the POS receives the reference and the warning")

        print("behind Cloudflare Tunnel (requests carry CF-* headers)")
        H = {"CF-Connecting-IP": "203.0.113.5", "CF-Ray": "abc123", "X-Forwarded-Proto": "https"}
        check(c.req("GET", "/admin", headers=H, raw=True)[0] == 403, "/admin page is blocked for internet visitors")
        check(c.req("GET", "/api/admin/orders", headers=H)[0] == 403, "staff API is blocked for internet visitors even with a valid session")
        hj = dict(H, **{"Content-Type": "application/json"})
        check(c.req("POST", "/api/admin/login", json.dumps({"pin": "123456"}).encode(), hj)[0] == 403, "staff login is blocked for internet visitors")
        check(c.req("GET", f"/admin/slip/{first_id}", headers=H, raw=True)[0] == 403, "printable slip is blocked for internet visitors")
        check(c.get("/admin", raw=True)[0] == 200, "the same pages still work on the shop network")
        # rate limits are per real visitor, not shared through 127.0.0.1
        codes_a = [c.req("GET", "/api/status?id=20260101-001&code=NOPE", headers={"CF-Connecting-IP": "198.51.100.1", "CF-Ray": "x"})[0] for _ in range(45)]
        codes_b = c.req("GET", "/api/status?id=20260101-001&code=NOPE", headers={"CF-Connecting-IP": "198.51.100.2", "CF-Ray": "x"})[0]
        lan = c.get("/api/status?id=20260101-001&code=NOPE")[0]
        check(429 in codes_a and codes_b == 404 and lan == 404, "one visitor hitting the limit does not block other visitors or the shop Wi-Fi")
        big = b"x" * (2 * 1024 * 1024)
        def post_big(h):
            try:
                return c.req("POST", "/api/estimate", big, dict(h, **{"Content-Type": "application/octet-stream"}), raw=True)[0]
            except Exception:
                return "closed"          # server answered early and hung up while we were still sending
        st, st_lan = post_big(H), post_big({})
        check(st in (413, "closed") and st_lan != 413, f"tunnel uploads are capped below Cloudflare's limit (tunnel {st}, shop Wi-Fi {st_lan})")
        c2 = Client(srv.base)
        s, j, h = c2.req("POST", "/api/admin/login", json.dumps({"pin": "123456"}).encode(), {"Content-Type": "application/json", "X-Forwarded-Proto": "https"})
        check(s == 200 and "Secure" in h.get("Set-Cookie", ""), "login cookie is marked Secure over https")
        s, j, h = Client(srv.base).req("POST", "/api/admin/login", json.dumps({"pin": "123456"}).encode(), {"Content-Type": "application/json"})
        check(s == 200 and "Secure" not in h.get("Set-Cookie", ""), "…but not over plain http (so the shop Wi-Fi login still works)")

        print("lock-out")
        for _ in range(8): Client(srv.base).post_json("/api/admin/login", {"name": "Maria", "pin": "9999"})
        check(Client(srv.base).post_json("/api/admin/login", {"name": "Maria", "pin": "482913"})[0] == 429, "repeated wrong PINs lock the account for a while")

        c.post_json("/api/admin/logout", {})
        check(c.get("/api/admin/orders")[0] == 401, "logout works")
        check(c.get("/nope")[0] == 404, "404 page")
    finally:
        out = srv.stop()
        if "Traceback" in out:
            print("SERVER LOG:\n", out[-2000:])
            failures.append("server traceback")

    print("storage offline")
    srv = Server(8124, tmp / "o2", tmp / "d2", mount_point="/mnt/definitely_not_mounted")
    try:
        c = Client(srv.base)
        s, j, _ = c.get("/health")
        check(j["ok"] is False, "health reports storage problem")
        s, j, _ = order(c, "Ana", [("a.pdf", make_pdf(["bw"]))], {"kind": "document"})
        check(s == 503 and "offline" in j["detail"], "orders refused with 503 instead of landing on the local disk")
        check(not (tmp / "o2").exists(), "no stray folder created on the local disk")
    finally:
        srv.stop()

    print("low disk space")
    cfg3 = json.loads(json.dumps(cfg)); cfg3["limits"]["min_free_gb"] = 1e9; cfg3["limits"]["scan_command"] = ""
    srv = Server(8128, tmp / "o4", tmp / "d4", config=cfg3)
    try:
        s, j, _ = order(Client(srv.base), "Ana", [("a.pdf", make_pdf(["bw"]))], {"kind": "document"})
        check(s == 503 and "storage" in j["detail"], "orders are refused (clean message) when the disk is nearly full")
    finally:
        srv.stop()

    print("remote staff access when explicitly enabled")
    cfg2 = json.loads(json.dumps(cfg)); cfg2["admin"]["allow_remote"] = True
    srv = Server(8127, tmp / "o3", tmp / "d3", config=cfg2)
    try:
        s, j, _ = Client(srv.base).req("POST", "/api/admin/login", json.dumps({"pin": "123456"}).encode(), {"Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.9", "CF-Ray": "r"})
        check(s == 200, "allow_remote=true lets staff log in through the tunnel")
    finally:
        srv.stop()

    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
