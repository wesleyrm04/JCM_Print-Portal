"""Start the JCM Print Portal:   python server.py
Environment variables (all optional):
  JCM_PORT        port(s) to listen on (default 80;  use 8080 if you do not run as root).
                  Several at once: JCM_PORT=80,1013  - the same portal answers on every port
  JCM_UPLOAD_DIR  where orders are saved (default from config.json)
  JCM_MOUNT_POINT NAS mount point to verify ("" disables the check)
  JCM_ADMIN_PIN   staff PIN (otherwise generated into data/admin_pin.txt on first run)
Security (v6, see SECURITY.md):
  JCM_PUBLIC_PORTS    the port(s) your tunnel / the internet uses, e.g. 1013. /admin and the POS API are refused there.
  JCM_ADMIN_NETWORKS  who may open the staff area, e.g. "127.0.0.1/32,10.0.4.37/32" (this PC + the staff phone)
  JCM_POS_NETWORKS    who may use the POS connection, e.g. "10.0.4.20/32"
  JCM_TLS_PORTS       extra port(s) served over HTTPS for the shop network, e.g. 8443 (needs a certificate:
                      python tools/make_lan_cert.py  ->  data/lan_cert.pem + data/lan_key.pem, or JCM_SSL_CERT / JCM_SSL_KEY)
"""
import os

import sys

try:
    import numpy  # noqa: F401  (needed for the page colour analysis)
except ImportError:
    sys.exit("A required package is missing.  Run this once, then start again:\n"
             "    venv/bin/pip install -r requirements.txt        (Windows: venv\\Scripts\\pip install -r requirements.txt)")

import uvicorn

from portal.app import app  # noqa: F401  (imported so `uvicorn server:app` also works)

def tls_files():
    """(certfile, keyfile) for the HTTPS port(s), or None when no certificate exists yet."""
    from portal import settings
    cert = os.environ.get("JCM_SSL_CERT") or str(settings.data_dir() / "lan_cert.pem")
    key = os.environ.get("JCM_SSL_KEY") or str(settings.data_dir() / "lan_key.pem")
    return (cert, key) if os.path.isfile(cert) and os.path.isfile(key) else None


async def serve_all(ports, tls_ports=()):
    """One portal, one process, any number of ports. The first port runs the app's start-up / shutdown work
    (backups, clean-up); the others only add listeners, so nothing runs twice."""
    import asyncio
    import contextlib
    servers = []
    tls = tls_files() if tls_ports else None
    if tls_ports and not tls:
        print("[tls] JCM_TLS_PORTS is set but no certificate was found - run  python tools/make_lan_cert.py  first. Skipping HTTPS.")
        tls_ports = ()
    listeners = [(p, False) for p in ports] + [(p, True) for p in tls_ports if p not in ports]
    for i, (port, use_tls) in enumerate(listeners):
        extra = {"ssl_certfile": tls[0], "ssl_keyfile": tls[1]} if use_tls else {}
        conf = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info", proxy_headers=False,
                              lifespan="on" if i == 0 else "off", **extra)
        srv = uvicorn.Server(conf)
        if i:                                                   # Ctrl+C / service stop is handled by the first server
            srv.install_signal_handlers = lambda: None          # uvicorn < 0.29
            srv.capture_signals = contextlib.nullcontext        # uvicorn >= 0.29
        servers.append(srv)

    async def follow():
        while not servers[0].should_exit and not servers[0].force_exit:
            await asyncio.sleep(0.3)
        for other in servers[1:]:
            other.should_exit = True

    await asyncio.gather(*(srv.serve() for srv in servers), follow())


if __name__ == "__main__":
    from portal import settings
    try:
        PORTS = settings.parse_ports(os.environ.get("JCM_PORT", "80"))
    except ValueError as e:
        sys.exit(str(e))
    try:
        TLS_PORTS = settings.parse_ports(os.environ["JCM_TLS_PORTS"]) if os.environ.get("JCM_TLS_PORTS", "").strip() else []
    except ValueError as e:
        sys.exit(str(e))
    if len(PORTS) == 1 and not TLS_PORTS:
        uvicorn.run(app, host="0.0.0.0", port=PORTS[0], log_level="info", proxy_headers=False)
    else:
        import asyncio
        print("Listening on ports: " + ", ".join(str(p) for p in PORTS) + ((" + HTTPS " + ", ".join(str(p) for p in TLS_PORTS)) if TLS_PORTS else ""))
        asyncio.run(serve_all(PORTS, TLS_PORTS))
