"""Tests SecurityMiddleware (size limits incl. chunked uploads, CSRF, security headers) at the ASGI level.
Needs no web framework: the class is lifted out of portal/app.py and run with tiny stand-ins for
JSONResponse / MutableHeaders.   Run: python tests/test_middleware.py"""
import ast, asyncio, json, os, sys, tempfile
from pathlib import Path
from urllib.parse import urlparse
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["JCM_DATA_DIR"] = tempfile.mkdtemp()
from portal import settings, netsec
src=open(ROOT / 'portal' / 'app.py', encoding='utf-8').read()
tree=ast.parse(src)
cls=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="SecurityMiddleware"][0]
code=ast.get_source_segment(src,cls)

class MutableHeaders:
    def __init__(self, scope): self.m=scope; self.m.setdefault("headers",[])
    def __setitem__(self,k,v):
        self.m["headers"]=[(a,b) for a,b in self.m["headers"] if a!=k.lower().encode()]+[(k.lower().encode(),v.encode())]
class JSONResponse:
    def __init__(self, content, status_code=200, headers=None): self.c=content; self.s=status_code; self.h=headers or {}
    async def __call__(self, scope, receive, send):
        await send({"type":"http.response.start","status":self.s,"headers":[(k.lower().encode(),v.encode()) for k,v in self.h.items()]})
        await send({"type":"http.response.body","body":json.dumps(self.c).encode()})
ns=dict(settings=settings, netsec=netsec, urlparse=urlparse, MutableHeaders=MutableHeaders, JSONResponse=JSONResponse)
exec(code, ns); MW=ns["SecurityMiddleware"]

cfg=settings.get_config(); cfg["limits"]["max_request_mb"]=1; cfg["limits"]["max_request_mb_tunnel"]=1; cfg["limits"]["max_json_mb"]=0.01

def make_app(swallow=False):
    async def app(scope, receive, send):
        # reads the whole body like a form parser would
        try:
            while True:
                m = await receive()
                if m["type"]=="http.request" and not m.get("more_body"): break
        except Exception:
            if not swallow: raise
            await send({"type":"http.response.start","status":400,"headers":[]})
            await send({"type":"http.response.body","body":b'{"detail":"could not read"}'}); return
        await send({"type":"http.response.start","status":200,"headers":[(b"content-type",b"text/plain")]})
        await send({"type":"http.response.body","body":b"fine"})
    return MW(app)

async def call(app, method="POST", path="/api/submit_order", headers=(), chunks=(b"x",), scheme="http"):
    scope={"type":"http","method":method,"path":path,"scheme":scheme,"headers":[(k.lower().encode(),v.encode()) for k,v in headers]}
    q=[{"type":"http.request","body":c,"more_body":i<len(chunks)-1} for i,c in enumerate(chunks)]
    async def receive(): return q.pop(0) if q else {"type":"http.disconnect"}
    out=[]
    async def send(m): out.append(m)
    await app(scope, receive, send)
    start=[m for m in out if m["type"]=="http.response.start"]
    body=b"".join(m.get("body",b"") for m in out if m["type"]=="http.response.body")
    return len(start), start[0]["status"], dict(start[0]["headers"]), body

R=[]
def check(c,m): print(("  ok   " if c else "  FAIL ")+m); R.append(c)
run=lambda *a,**k: asyncio.run(call(*a,**k))

n,st,h,b = run(make_app(), chunks=[b"a"*1000]*5)
check(st==200 and n==1 and b"fine" in b, "normal small upload passes")
check(h.get(b"x-content-type-options")==b"nosniff" and b"script-src 'self'" in h.get(b"content-security-policy",b"") and b"permissions-policy" in h and h.get(b"x-frame-options")==b"SAMEORIGIN", "security headers present")
check(b"strict-transport-security" not in h, "no HSTS over plain http")
n,st,h,b = run(make_app(), headers=[("x-forwarded-proto","https")])
check(b"strict-transport-security" in h, "HSTS when https")
n,st,h,b = run(make_app(), headers=[("content-length",str(2*1024*1024))], chunks=[b"x"])
check(st==413 and n==1 and b"limit 1 MB" in b and b"x-content-type-options" in h, "oversize Content-Length -> 413 (with security headers)")
n,st,h,b = run(make_app(), chunks=[b"a"*100_000]*15)
check(st==413 and n==1, "CHUNKED upload (no Content-Length) over the limit -> 413, handler error propagates")
n,st,h,b = run(make_app(swallow=True), chunks=[b"a"*100_000]*15)
check(st==413 and n==1 and b"too big" in b, "...and when the handler swallows the error (returns 400) the answer is still 413, sent once")
n,st,h,b = run(make_app(), path="/api/admin/login", chunks=[b"a"*20_000])
check(st==413 and b"too large" in b, "non-upload (JSON) endpoints have a tiny limit")
n,st,h,b = run(make_app(), path="/api/estimate", chunks=[b"a"*500_000])
check(st==200, "upload endpoints keep the big limit")
n,st,h,b = run(make_app(), headers=[("sec-fetch-site","cross-site")])
check(st==403, "cross-site POST blocked (Sec-Fetch-Site)")
n,st,h,b = run(make_app(), headers=[("origin","https://evil.example"),("host","10.0.4.37")])
check(st==403, "cross-site POST blocked (Origin)")
n,st,h,b = run(make_app(), headers=[("origin","http://10.0.4.37"),("host","10.0.4.37")])
check(st==200, "same-origin POST allowed")
n,st,h,b = run(make_app(), path="/share", headers=[("origin","android-app://x")])
check(st==200, "/share POST (phone share target) is exempt from the origin check")
n,st,h,b = run(make_app(), method="GET", path="/api/config")
check(h.get(b"cache-control")==b"no-store", "API responses are not cached")
print("ALL PASSED" if all(R) else "FAILURES"); sys.exit(0 if all(R) else 1)
