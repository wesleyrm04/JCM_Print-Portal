"""End-to-end test for Facebook sharing, the installable app files and the code/name status search.
Starts the real server (like test_api.py).   Run:  python tests/test_api_share.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import Client, Server, make_jpeg

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def share(c, name, text="", links=None, files=(), notes=""):
    fields = [("customer_name", name), ("phone", ""), ("instructions", notes), ("shared_text", text),
              ("order_meta", json.dumps({"kind": "share", "links": links or []}))]
    return c.post_form("/api/submit_order", fields, [("files", n, d) for n, d in files])


def main():
    tmp = Path(tempfile.mkdtemp())
    up, data = tmp / "orders", tmp / "data"
    cfg = json.load(open(Path(__file__).resolve().parent.parent / "config.json"))
    cfg["analysis"]["use_libreoffice"] = False
    srv = Server(8131, up, data, config=cfg)
    c = Client(srv.base)
    try:
        print("installable app files")
        s, m, h = c.get("/manifest.webmanifest")
        check(s == 200 and "manifest+json" in h.get("Content-Type", "") and m["display"] == "standalone", "manifest is served")
        st = m.get("share_target", {})
        check(st.get("action") == "/share" and st.get("method") == "POST" and st["params"]["files"][0]["name"] == "files", "manifest has a share target (text, url, files)")
        check(len(m["icons"]) >= 3 and any(i["purpose"] == "maskable" for i in m["icons"]), "manifest has normal + maskable icons")
        s, body, h = c.req("GET", "/sw.js", raw=True)
        check(s == 200 and "javascript" in h.get("Content-Type", "") and h.get("Service-Worker-Allowed") == "/", "service worker is served from / with the right scope header")
        check(c.req("GET", "/static/icons/icon-512.png", raw=True)[0] == 200, "icon is reachable")
        check(c.req("GET", "/share", raw=True)[0] == 200, "/share page opens")
        s, _, h = c.req("POST", "/share", b"title=x&text=https%3A%2F%2Ffb.me%2Fabc&url=", {"Content-Type": "application/x-www-form-urlencoded", "Origin": "null"}, raw=True)
        check(s in (200, 303), f"POST /share without a service worker is not blocked (got {s})")

        print("share a Facebook link")
        s, j, _ = share(c, "Juan Dela Cruz", text="Check this https://www.facebook.com/share/p/1AbCdEf/", links=["https://fb.me/xyz"], notes="4R x 2")
        check(s == 200 and j["kind"] == "share" and j["total_files"] == 0 and j["needs_review"] is True, f"link-only order accepted and flagged for price check ({s})")
        oid, code = j["order_id"], j["access_code"]
        oj = json.load(open(next(up.glob("*/001_*/order.json"))))
        check(oj["links"] == ["https://fb.me/xyz", "https://www.facebook.com/share/p/1AbCdEf/"], "links saved in order.json")
        check(oj["preset_request"] == "Shared from Facebook" and oj["total"] == 0, "titled 'Shared from Facebook', price left for staff")
        txt = next(up.glob("*/001_*/instructions.txt")).read_text(encoding="utf-8")
        check("https://fb.me/xyz" in txt and "4R x 2" in txt, "instructions.txt lists the links and the customer's note")
        check(oj["warnings"] == [], "Facebook links raise no warning")

        print("share with files, and a non-Facebook link")
        s, j2, _ = share(c, "Maria Santos", text="https://evil.example/phish", files=[("IMG_1.jpg", make_jpeg())])
        check(s == 200 and j2["total_files"] == 1, "link + photo accepted")
        oj2 = json.load(open(next(up.glob("*/002_*/order.json"))))
        check(any("evil.example" in w for w in oj2["warnings"]) and oj2["preset_request"] == "Shared Items", "non-Facebook link is flagged for staff")
        s, j3, _ = share(c, "Pedro", files=[("IMG_2.jpg", make_jpeg())])
        check(s == 200 and j3["total_files"] == 1, "photo-only share accepted")
        s, j, _ = share(c, "Pedro", text="no link here")
        check(s == 400, "nothing to send -> clear error")
        s, j, _ = share(c, "", text="https://fb.me/a")
        check(s == 400 and "name" in j["detail"].lower(), "name still required")
        s, j, _ = share(c, "Pedro", text="https://fb.me/a", files=[("run.exe", b"MZ" * 20)])
        check(s == 400, "bad file types are still refused on shared orders")

        print("Canva / Google Drive / OneDrive / WPS links")
        s, j, _ = share(c, "Ana Reyes", text="Canva https://www.canva.com/design/DAF1/abc/view and https://drive.google.com/file/d/1x/view?usp=sharing "
                        "and https://1drv.ms/b/s!Abc and https://www.kdocs.cn/l/abc")
        check(s == 200, f"cloud links accepted ({s})")
        o = json.load(open(next(up.glob("*/004_*/order.json"))))
        check([x["label"] for x in o["links_info"]] == ["Canva", "Google Drive", "OneDrive", "WPS"], "each link is labelled")
        check(o["warnings"] == [] and any("permission" in n for n in o["review_notes"]), "no warning, but staff get the 'Anyone with the link' reminder")

        print("status search by code")
        s, j, _ = c.get(f"/api/status/find?q={code}")
        r = j["results"][0] if s == 200 and j.get("results") else {}
        check(s == 200 and j["mode"] == "code" and r.get("id") == oid and r.get("status") == "Pending" and r.get("limited") is False, "code (no order number) finds the order")
        check(c.get(f"/api/status/find?q={code.lower()}")[1]["results"][0]["id"] == oid, "code works in lower case")
        s, j, _ = c.get(f"/api/status?id={oid}&code={code}")
        check(s == 200 and j["status"] == "Pending", "old order-number + code lookup still works")

        print("status search by name")
        s, j, _ = c.get("/api/status/find?q=juan")
        r = j["results"][0] if j.get("results") else {}
        check(s == 200 and j["mode"] == "name" and len(j["results"]) == 1 and r["first_name"] == "Juan C.", "name finds the order with a shortened name")
        check(all(k not in r for k in ("id", "total", "paid", "public_note")), "name search never reveals number, price, payment or staff message")
        check(c.get("/api/status/find?q=dela%20cruz")[1]["results"][0]["status"] == "Pending", "several words work")
        s, j, _ = c.get("/api/status/find?q=ju")
        check(s == 404 or (s == 200 and j["results"] == []), "too-short names find nothing")
        s, j, _ = c.get("/api/status/find?q=Nobody%20Here")
        check(s == 200 and j["results"] == [], "unknown name -> empty result")
        check(c.get("/api/status/find?q=")[0] == 400, "empty search -> error")


        print("staff see links + can search by code")
        s, _, _ = c.post_json("/api/admin/login", {"pin": "123456"})
        s, j, _ = c.get(f"/api/admin/orders/{oid}")
        check(s == 200 and j["data"]["links"][0] == "https://fb.me/xyz", "staff order detail carries the links")
        s, j, _ = c.get(f"/api/admin/orders?date=all&q={code}")
        check(s == 200 and [o["id"] for o in j["orders"]] == [oid], "staff can search by code")

        print("brute-force protection")
        codes = [c.req("GET", "/api/status/find?q=ZZZZ2" + str(i % 9 + 2), headers={"CF-Connecting-IP": "198.51.100.7", "CF-Ray": "x"})[0] for i in range(40)]
        check(429 in codes, "many searches that find nothing get rate-limited")
        check(c.req("GET", f"/api/status/find?q={code}", headers={"CF-Connecting-IP": "198.51.100.8", "CF-Ray": "x"})[0] == 200, "other visitors are not affected")
    finally:
        out = srv.stop()
        if "Traceback" in out:
            print("SERVER LOG:\n", out[-1500:])
            failures.append("server traceback")
    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
