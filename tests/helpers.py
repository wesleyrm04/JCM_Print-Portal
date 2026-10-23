"""Shared helpers: build test PDFs/images, multipart bodies, run the server."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent


def make_pdf(pages, w=827, h=1169) -> bytes:
    """pages: list of 'bw' | 'color' | 'logo' | 'tint' -> PDF bytes (A4 @100dpi)."""
    imgs = []
    for kind in pages:
        im = Image.new("RGB", (w, h), "white")
        d = ImageDraw.Draw(im)
        for y in range(100, 700, 40):
            d.rectangle([80, y, 700, y + 12], fill=(20, 20, 20))     # "text" lines
        if kind == "color":
            d.rectangle([100, 750, 500, 1000], fill=(220, 30, 30))
        elif kind == "logo":
            d.rectangle([100, 750, 160, 810], fill=(30, 60, 220))     # ~0.6in coloured logo
        elif kind == "tint":
            im = Image.new("RGB", (w, h), (252, 250, 244))            # slightly yellowed scan
            ImageDraw.Draw(im).rectangle([80, 100, 700, 112], fill=(30, 30, 30))
        imgs.append(im)
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:], resolution=100)
    return buf.getvalue()


def make_jpeg(color=(200, 80, 80), size=(600, 400)) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", size, color)
    ImageDraw.Draw(im).ellipse([size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4], fill=(240, 240, 240))
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def multipart(fields, files):
    """fields: list[(name, str)], files: list[(field, filename, bytes)] -> (body, content_type)"""
    boundary = "----jcm" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields:
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        out.write(str(v).encode("utf-8") + b"\r\n")
    for field, fname, data in files:
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'
                  f'Content-Type: application/octet-stream\r\n\r\n'.encode())
        out.write(data + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, method, path, body=None, headers=None, raw=False):
        r = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            resp = self.op.open(r, timeout=120)
            status, data, hdrs = resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            status, data, hdrs = e.code, e.read(), e.headers
        if raw:
            return status, data, hdrs
        try:
            return status, json.loads(data.decode("utf-8")), hdrs
        except ValueError:
            return status, data, hdrs

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post_json(self, path, obj, method="POST"):
        return self.req(method, path, json.dumps(obj).encode(), {"Content-Type": "application/json"})

    def post_form(self, path, fields, files):
        body, ct = multipart(fields, files)
        return self.req("POST", path, body, {"Content-Type": ct})


class Server:
    def __init__(self, port, upload_dir, data_dir, mount_point="", config=None, pin="123456"):
        self.port = port
        env = dict(os.environ)
        env.update(JCM_UPLOAD_DIR=str(upload_dir), JCM_DATA_DIR=str(data_dir), JCM_MOUNT_POINT=mount_point,
                   JCM_ADMIN_PIN=pin)
        if config is not None:
            Path(data_dir).mkdir(parents=True, exist_ok=True)
            cfgp = Path(data_dir) / "test_config.json"
            cfgp.write_text(json.dumps(config))
            env["JCM_CONFIG"] = str(cfgp)
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "portal.app:app", "--port", str(port),
                                      "--log-level", "warning"], cwd=ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/health", timeout=1)
                return
            except Exception:
                time.sleep(0.15)
        self.stop()
        raise RuntimeError("server did not start")

    def stop(self):
        self.proc.terminate()
        try:
            out = self.proc.communicate(timeout=5)[0].decode("utf-8", "ignore")
        except Exception:
            self.proc.kill()
            out = ""
        return out
