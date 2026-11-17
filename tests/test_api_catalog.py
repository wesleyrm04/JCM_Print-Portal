"""End-to-end test of the catalog editor API and the customer view. Starts the real server.
Run:  python tests/test_api_catalog.py"""
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


def main():
    tmp = Path(tempfile.mkdtemp())
    up, data = tmp / "orders", tmp / "data"
    cfg = json.load(open(Path(__file__).resolve().parent.parent / "config.json"))
    cfg["analysis"]["use_libreoffice"] = False
    srv = Server(8132, up, data, config=cfg)
    c = Client(srv.base)
    try:
        print("customers")
        s, j, _ = c.get("/api/catalog")
        check(s == 200 and [x["id"] for x in j["repair"]["sections"]] == ["desktop", "laptop"], "public catalog: Mobile section is off by default")
        check(sum(len(x["items"]) for x in j["souvenirs"]["categories"]) == 9, "public catalog: the 9 original souvenir pictures")
        check("rev" in j and "in_stock" in j["souvenirs"]["categories"][0]["items"][0], "public catalog has the fields the pages need")
        check(c.get("/api/admin/catalog")[0] == 401, "editing needs staff login")
        check(c.req("PUT", "/api/admin/catalog", b"{}", {"Content-Type": "application/json"})[0] == 401, "saving needs staff login")
        check(c.post_form("/api/admin/catalog/image", [], [("file", "a.jpg", make_jpeg())])[0] == 401, "uploading needs staff login")
        check(c.req("GET", "/media/../config.json", raw=True)[0] == 404 and c.req("GET", "/media/" + "0" * 32 + ".jpg", raw=True)[0] == 404, "/media only serves our own pictures")
        for f in ("souvenirs.html", "repair.html"):
            check(b"catalog.js" in c.req("GET", "/" + f.replace(".html", ""), raw=True)[1], f"{f} loads catalog.js")

        print("staff")
        check(c.post_json("/api/admin/login", {"pin": "123456"})[0] == 200, "login")
        s, cat, _ = c.get("/api/admin/catalog")
        check(s == 200 and cat["repair"]["sections"][2]["id"] == "mobile" and cat["repair"]["sections"][2]["enabled"] is False, "staff see everything, including the disabled Mobile section")
        s, j, _ = c.post_form("/api/admin/catalog/image", [], [("file", "new.jpg", make_jpeg(size=(1200, 900)))])
        check(s == 200 and j["url"].startswith("/media/") and j["thumb"].endswith("_t.jpg"), f"picture uploaded ({s})")
        url = j["url"]
        s, body, h = c.req("GET", url, raw=True)
        check(s == 200 and h.get("Content-Type") == "image/jpeg" and "immutable" in h.get("Cache-Control", "") and body[:2] == b"\xff\xd8", "uploaded picture is served")
        check(c.req("GET", j["thumb"], raw=True)[0] == 200, "thumbnail is served")
        check(c.post_form("/api/admin/catalog/image", [], [("file", "x.jpg", b"not a picture")])[0] == 400, "a non-picture is refused")
        check(c.post_form("/api/admin/catalog/image", [], [("file", "x.exe", b"MZ" + b"\0" * 50)])[0] == 400, "a program is refused")

        cat["souvenirs"]["categories"][0]["items"][0]["images"].append(url)
        cat["souvenirs"]["categories"][0]["items"][1]["in_stock"] = False
        cat["repair"]["sections"][0]["services"].append({"id": "n1", "name": "Data Recovery", "price": "Ask us", "notes": "", "available": True})
        cat["repair"]["sections"][2]["enabled"] = True
        s, saved, _ = c.post_json("/api/admin/catalog", cat, method="PUT")
        check(s == 200 and saved["rev"] == cat["rev"] + 1, f"catalog saved ({s})")
        s, j, _ = c.get("/api/catalog")
        first = j["souvenirs"]["categories"][0]["items"][0]
        check(url in first["images"] and len(first["images"]) == 2, "customers see the extra picture")
        check("Car Magnets" not in [i["name"] for x in j["souvenirs"]["categories"] for i in x["items"]], "out-of-stock product is gone for customers")
        check("Data Recovery" in [v["name"] for v in j["repair"]["sections"][0]["services"]], "new repair service is visible")
        check([x["id"] for x in j["repair"]["sections"]][-1] == "mobile" and "Mobile phone / Tablet" in j["repair"]["device_types"], "Mobile section + device type appear once enabled")
        s, j, _ = c.post_json("/api/admin/catalog", cat, method="PUT")
        check(s == 409, "saving from an out-of-date editor is refused (409)")
        s, j, _ = c.post_json("/api/admin/catalog", {"souvenirs": {"categories": [{"title": "x", "items": [{"name": "n", "images": ["https://evil.example/a.jpg"]}]}]}, "rev": saved["rev"]}, method="PUT")
        check(s == 200 and j["souvenirs"]["categories"][0]["items"][0]["images"] == [], "outside picture URLs are dropped")

        print("a customer can still order after the catalog changed")
        meta = {"kind": "souvenir", "details": {"item": "Keychains", "quantity": "20", "theme": "Frozen"}}
        s, j, _ = c.post_form("/api/submit_order", [("customer_name", "Ana"), ("phone", "09171234567"), ("instructions", ""), ("order_meta", json.dumps(meta))], [])
        check(s == 200 and j["kind"] == "souvenir", f"souvenir order still works ({s})")
    finally:
        out = srv.stop()
        if "Traceback" in out:
            print("SERVER LOG:\n", out[-1500:])
            failures.append("server traceback")
    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
