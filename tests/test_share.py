"""Logic tests for Facebook sharing + order-status search (no web server needed).
Run:  python tests/test_share.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os
os.environ["JCM_DATA_DIR"] = tempfile.mkdtemp()
from portal import db, sharing

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def main():
    print("link extraction")
    t = "Check this out https://www.facebook.com/share/p/1AbCdEf/. Also (https://fb.watch/xyz123/) and https://www.facebook.com/share/p/1AbCdEf/"
    L = sharing.extract_links(t)
    check(L == ["https://www.facebook.com/share/p/1AbCdEf/", "https://fb.watch/xyz123/"], f"finds links, strips trailing punctuation, no duplicates: {L}")
    check(sharing.extract_links("javascript:alert(1) ftp://x.com data:text/html,hi") == [], "only http(s) links are kept")
    check(len(sharing.extract_links(" ".join(f"https://a.com/{i}" for i in range(30)), 10)) == 10, "link limit")
    check(sharing.extract_links("https://x.com/" + "a" * 600) == [], "absurdly long links are dropped")
    U = sharing.collect_links("see https://m.facebook.com/story.php?id=1", ["https://fb.me/abc", "not a link"])
    check(U == ["https://fb.me/abc", "https://m.facebook.com/story.php?id=1"], f"union of listed + raw text: {U}")
    check(sharing.collect_links(None, "oops") == [], "bad input is harmless")

    print("facebook detection / warnings")
    check(sharing.is_facebook("https://www.facebook.com/x") and sharing.is_facebook("https://m.facebook.com/x") and sharing.is_facebook("https://fb.watch/a"), "facebook hosts")
    check(not sharing.is_facebook("https://facebook.com.evil.example/x") and not sharing.is_facebook("https://notfacebook.com/x"), "look-alike hosts are NOT facebook")
    w = sharing.link_warnings(["https://www.facebook.com/a", "https://evil.example/b"])
    check(len(w) == 1 and "evil.example" in w[0], "only non-Facebook links are flagged")
    check(sharing.preset_for(["https://fb.me/a"], 0) == "Shared from Facebook" and sharing.preset_for([], 2) == "Shared Items"
          and sharing.preset_for(["https://fb.me/a", "https://x.com/b"], 0) == "Shared Items", "order title")

    print("other sharing services")
    cases = {"https://www.canva.com/design/DAF123/abc/view": "Canva", "https://canva.link/xyz": None,
             "https://drive.google.com/file/d/1abc/view?usp=sharing": "Google Drive", "https://docs.google.com/document/d/1abc/edit": "Google Drive",
             "https://1drv.ms/b/s!Abc": "OneDrive", "https://onedrive.live.com/redir?resid=1": "OneDrive",
             "https://contoso-my.sharepoint.com/:w:/g/personal/x": "OneDrive", "https://www.kdocs.cn/l/abc": "WPS", "https://docs.wps.com/d/abc": "WPS",
             "https://www.dropbox.com/s/abc/file.pdf?dl=0": "Dropbox", "https://we.tl/t-abc": "WeTransfer", "https://m.facebook.com/x": "Facebook",
             "https://evil.example/x": None, "https://drive.google.com.evil.example/x": None, "https://notcanva.com/x": None}
    for u, want in cases.items():
        check(sharing.service_of(u)[1] == (want or ""), f"{u[:48]} -> {want or 'unknown'}")
    check(sharing.service_label("https://evil.example/x") == "Other site", "unknown sites are labelled Other site")
    check(sharing.preset_for(["https://www.canva.com/a", "https://www.canva.com/b"], 0) == "Shared from Canva", "title: all Canva")
    check(sharing.preset_for(["https://www.canva.com/a", "https://drive.google.com/b"], 0) == "Shared Links", "title: mixed known services")
    check(sharing.link_warnings(["https://www.canva.com/a", "https://drive.google.com/b", "https://1drv.ms/c"]) == [], "known services raise no warning")
    h = sharing.access_hints(["https://www.canva.com/a", "https://www.facebook.com/b"])
    check(len(h) == 1 and "Canva" in h[0] and "Facebook" not in h[0], "permission reminder only for cloud/design links")
    check(sharing.access_hints(["https://www.facebook.com/b"]) == [], "no reminder for Facebook-only")
    info = sharing.link_info(["https://www.canva.com/a", "https://evil.example/x"])
    check(info[0]["label"] == "Canva" and info[0]["known"] and info[1]["label"] == "Other site" and not info[1]["known"], "link_info for the staff page")
    check(sharing.extract_links("Canva: https://www.canva.com/design/DAF/abc/view?utm_content=1&utm_medium=link, thanks")[0].endswith("utm_medium=link"), "long share URLs keep their query")

    print("code / name helpers")
    check(sharing.normalize_code(" k7m-2qx ") == "K7M2QX", "code is forgiving about case, spaces, dashes")
    check(sharing.normalize_code("Juan") is None and sharing.normalize_code("K7M2Q0") is None and sharing.normalize_code("K7M2Q") is None, "non-codes are not codes")
    check(sharing.mask_name("Juan Dela Cruz") == "Juan C." and sharing.mask_name("Maria") == "Maria" and sharing.mask_name("") == "", "name masking")
    check(sharing.name_tokens("  juan   dela-cruz ") == ["juan", "dela-cruz"], "name tokens")
    check(sharing.name_tokens("a b") == [] and len(sharing.name_tokens("a1 b2 c3 d4 e5 f6")) == 4, "tokens: too-short words dropped, max 4")

    print("database search")
    db.init()
    def order(oid, name, code, status="Pending", date="2026-11-17", category="print"):
        db.upsert({"order_id": oid, "kind": "document", "category": category, "date": date, "created": date + "T10:00:00",
                   "updated": date + "T10:00:00", "customer_name": name, "phone": "", "preset_request": "Standard Document Print",
                   "status": status, "total": 10, "access_code": code, "folder": "", "total_files": 1})
    order("20261117-001", "Juan Dela Cruz", "K7M2QX")
    order("20261117-002", "Maria Santos", "ABCD23")
    order("20261117-003", "juan paolo", "ZZZZ22")
    order("20261001-001", "Juan Old", "OLD123", date="2026-10-01")
    order("20261117-004", "100% Pure_Juan", "PCT234")
    check([r["id"] for r in db.find_by_code("k7m2qx")] == ["20261117-001"], "find by code is case-insensitive")
    check(db.find_by_code("NOPE22") == [], "unknown code finds nothing")
    r = db.find_by_name(["juan"], "2026-11-01")
    check(sorted(x["id"] for x in r) == ["20261117-001", "20261117-003", "20261117-004"], f"name search: partial, case-insensitive, recent only: {[x['id'] for x in r]}")
    check([x["id"] for x in db.find_by_name(["juan", "cruz"], "2026-11-01")] == ["20261117-001"], "every word must match")
    check([x["id"] for x in db.find_by_name(["%"], "2026-11-01")] == ["20261117-004"], "a lone % is a literal character, not 'match everybody' (wildcards are escaped)")
    check([x["id"] for x in db.find_by_name(["_"], "2026-11-01")] == ["20261117-004"], "same for _")
    check([x["id"] for x in db.find_by_name(["100%"], "2026-11-01")] == ["20261117-004"], "a literal % can still be searched")
    check(len(db.find_by_name(["juan"], "2026-01-01", limit=2)) == 2, "result limit")
    rows, _ = db.search(date="all", q="K7M2QX")
    check([x["id"] for x in rows] == ["20261117-001"], "staff search also finds an order by its code")
    check(db.find_by_name([], "2026-01-01") == [], "empty search")

    print("manifest file is valid JSON / icons exist")
    root = Path(__file__).resolve().parent.parent
    for f in ["icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png"]:
        check((root / "static" / "icons" / f).is_file(), f"static/icons/{f}")
    check((root / "static" / "sw.js").is_file() and (root / "static" / "share.html").is_file(), "sw.js and share.html present")

    print("FAILED" if failures else "ALL OK")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
