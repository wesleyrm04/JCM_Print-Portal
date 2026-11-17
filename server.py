"""Start the JCM Print Portal:   python server.py
Environment variables (all optional):
  JCM_PORT        port(s) to listen on (default 80;  use 8080 if you do not run as root).
                  Several at once: JCM_PORT=80,1013  - the same portal answers on every port
  JCM_UPLOAD_DIR  where orders are saved (default from config.json)
  JCM_MOUNT_POINT NAS mount point to verify ("" disables the check)
  JCM_ADMIN_PIN   staff PIN (otherwise generated into data/admin_pin.txt on first run)
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

async def serve_all(ports):
    """One portal, one process, any number of ports. The first port runs the app's start-up / shutdown work
    (backups, clean-up); the others only add listeners, so nothing runs twice."""
    import asyncio
    import contextlib
    servers = []
    for i, port in enumerate(ports):
        conf = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info", proxy_headers=False,
                              lifespan="on" if i == 0 else "off")
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
    if len(PORTS) == 1:
        uvicorn.run(app, host="0.0.0.0", port=PORTS[0], log_level="info", proxy_headers=False)
    else:
        import asyncio
        print("Listening on ports: " + ", ".join(str(p) for p in PORTS))
        asyncio.run(serve_all(PORTS))
