"""Start the JCM Print Portal:   python server.py
Environment variables (all optional):
  JCM_PORT        port to listen on (default 80;  use 8080 if you do not run as root)
  JCM_UPLOAD_DIR  where orders are saved (default from config.json)
  JCM_MOUNT_POINT NAS mount point to verify ("" disables the check)
  JCM_ADMIN_PIN   staff PIN (otherwise generated into data/admin_pin.txt on first run)
"""
import os

import uvicorn

from portal.app import app  # noqa: F401  (imported so `uvicorn server:app` also works)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("JCM_PORT", "80")), log_level="info", proxy_headers=False)
