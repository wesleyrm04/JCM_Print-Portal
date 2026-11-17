"""Tests for the editable catalog (souvenirs gallery + repair services). No web server needed.
Run:  python tests/test_catalog.py"""
import copy
import io
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from portal import catalog

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def jpeg(w=800, h=600, color=(200, 30, 30)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


def main():
    d = Path(tempfile.mkdtemp())
    print("defaults")
    cat = catalog.load(d)
    check(not catalog.path(d).exists() and cat["rev"] == 1, "no file yet -> built-in defaults (the original page content)")
    names = [i["name"] for c in cat["souvenirs"]["categories"] for i in c["items"]]
    check(len(names) == 9 and "Custom Keychain" in names, "all 9 original souvenir pictures are in the defaults")
    check(all(Path(__file__).resolve().parent.parent.joinpath(u.lstrip("/")).is_file() for c in cat["souvenirs"]["categories"] for i in c["items"] for u in i["images"]), "default pictures exist on disk")
    check(catalog.sanitize(cat) == {**cat, "rev": cat["rev"]}, "sanitizing the defaults changes nothing")
    pub = catalog.public_view(cat)
    ids = [s["id"] for s in pub["repair"]["sections"]]
    check(ids == ["desktop", "laptop"], "Mobile section is OFF by default (customers do not see it)")
    check([len(s["services"]) for s in pub["repair"]["sections"]] == [6, 5], "original 6 desktop + 5 laptop services")
    check("Mobile phone / Tablet" not in pub["repair"]["device_types"], "no mobile device type while the section is off")
    mob = next(s for s in cat["repair"]["sections"] if s["id"] == "mobile")
    check(mob["enabled"] is False and len(mob["services"]) >= 3, "Mobile section exists, ready to switch on")

    print("editing + saving")
    new = copy.deepcopy(cat)
    new["souvenirs"]["categories"][0]["items"][0]["in_stock"] = False
    saved = catalog.save(d, new)
    check(saved["rev"] == 2 and catalog.path(d).exists(), "save bumps the revision and writes data/catalog.json")
    try:
        catalog.save(d, new)                       # still revision 1 -> stale editor
        check(False, "stale editor is refused")
    except catalog.Conflict:
        check(True, "stale editor (someone else saved) is refused, nothing overwritten")
    pub = catalog.public_view(catalog.load(d))
    names = [i["name"] for c in pub["souvenirs"]["categories"] for i in c["items"]]
    check("Custom Keychain" not in names and "Car Magnets" in names, "out-of-stock product disappears from the customer view")
    cur = catalog.load(d); cur["souvenirs"]["show_out_of_stock"] = True; cur = catalog.save(d, cur)
    items = [i for c in catalog.public_view(cur)["souvenirs"]["categories"] for i in c["items"]]
    check(any(i["name"] == "Custom Keychain" and i["in_stock"] is False for i in items), "with 'show out of stock' on, it is shown but flagged")
    cur["souvenirs"]["show_out_of_stock"] = False
    for it in cur["souvenirs"]["categories"][2]["items"]:
        it["in_stock"] = False
    cur = catalog.save(d, cur)
    check("standees" not in [c["id"] for c in catalog.public_view(cur)["souvenirs"]["categories"]], "a category with nothing in stock is hidden")
    check(catalog.path(d).with_name("catalog.prev.json").exists(), "previous version kept as catalog.prev.json")

    print("repair services + mobile section")
    cur["repair"]["sections"][0]["services"].append({"id": "x", "name": "Data Recovery", "price": "Ask us", "notes": "", "available": True})
    cur["repair"]["sections"][0]["services"][0]["available"] = False
    cur["repair"]["sections"][2]["enabled"] = True
    cur = catalog.save(d, cur)
    pub = catalog.public_view(cur)
    desk = pub["repair"]["sections"][0]["services"]
    check(desk[-1]["name"] == "Data Recovery" and "Basic Diagnostic Fee" not in [s["name"] for s in desk], "services can be added and switched off")
    check([s["id"] for s in pub["repair"]["sections"]] == ["desktop", "laptop", "mobile"], "Mobile section appears once switched on")
    check(pub["repair"]["device_types"][-2:] == ["Mobile phone / Tablet", "Other"], "'Mobile phone / Tablet' joins the device list before 'Other'")
    cur["repair"]["sections"][1]["services"] = []
    check("laptop" not in [s["id"] for s in catalog.public_view(catalog.save(d, cur))["repair"]["sections"]], "a section with no services is not shown")

    print("safety")
    evil = catalog.sanitize({"souvenirs": {"categories": [{"title": "<b>x</b>", "items": [{"name": "n", "images": [
        "javascript:alert(1)", "https://evil.example/a.jpg", "/static/../config.json", "/media/../../etc/passwd.jpg", "/media/" + "a" * 32 + ".jpg", "/static/images/logo.jpg"]}]}]}})
    imgs = evil["souvenirs"]["categories"][0]["items"][0]["images"]
    check(imgs == ["/media/" + "a" * 32 + ".jpg", "/static/images/logo.jpg"], f"only our own picture URLs survive: {imgs}")
    big = catalog.sanitize({"souvenirs": {"categories": [{"items": [{"name": "x" * 500, "images": [f"/static/images/{i}.jpg" for i in range(50)]}] * 200}]}})
    check(len(big["souvenirs"]["categories"][0]["items"]) <= catalog.MAX_ITEMS and len(big["souvenirs"]["categories"][0]["items"][0]["images"]) <= catalog.MAX_IMAGES
          and len(big["souvenirs"]["categories"][0]["items"][0]["name"]) <= 80, "sizes are limited")
    dup = catalog.sanitize({"souvenirs": {"categories": [{"id": "a", "items": []}, {"id": "a", "items": []}]}})
    check(len({c["id"] for c in dup["souvenirs"]["categories"]}) == 2, "duplicate ids are fixed")
    try:
        catalog.sanitize([1, 2]); check(False, "non-object refused")
    except ValueError:
        check(True, "garbage input refused")
    (d / "catalog.json").write_text("{not json", encoding="utf-8")
    check(catalog.load(d)["souvenirs"]["categories"], "a damaged catalog.json falls back to the defaults instead of breaking the pages")

    print("pictures")
    d2 = Path(tempfile.mkdtemp())
    url = catalog.save_image(d2, jpeg(3000, 2000))
    name = url.split("/")[-1]
    check(catalog.MEDIA_NAME_RE.match(name) and (catalog.images_dir(d2) / name).is_file() and (catalog.images_dir(d2) / name.replace(".jpg", "_t.jpg")).is_file(), f"saved with a random name + thumbnail: {url}")
    from PIL import Image
    im = Image.open(catalog.images_dir(d2) / name); th = Image.open(catalog.images_dir(d2) / name.replace(".jpg", "_t.jpg"))
    check(max(im.size) == 1600 and max(th.size) == 480, f"shrunk to 1600 / 480 px: {im.size} {th.size}")
    b = io.BytesIO(); Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(b, "PNG")
    check(catalog.save_image(d2, b.getvalue()).endswith(".jpg"), "PNG with transparency is converted")
    for bad, why in [(b"hello", "text file"), (b"", "empty"), (b"MZ" + b"\0" * 100, "program"), (b"<svg onload=alert(1)>", "svg/html")]:
        try:
            catalog.save_image(d2, bad); check(False, "refused " + why)
        except ValueError:
            check(True, "refused " + why)
    print("orphan cleanup")
    c2 = catalog.load(d2)
    c2["souvenirs"]["categories"][0]["items"][0]["images"].append(url)
    catalog.save(d2, c2)
    old = catalog.images_dir(d2) / ("f" * 32 + ".jpg"); old.write_bytes(jpeg(10, 10)); os.utime(old, (time.time() - 7200,) * 2)
    fresh = catalog.images_dir(d2) / ("e" * 32 + ".jpg"); fresh.write_bytes(jpeg(10, 10))
    catalog.remove_orphans(d2, catalog.load(d2))
    check(not old.exists() and fresh.exists() and (catalog.images_dir(d2) / name).exists(), "old unused pictures are removed; used and just-uploaded ones are kept")
    print("FAILED" if failures else "ALL OK")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
