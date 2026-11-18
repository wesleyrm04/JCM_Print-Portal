# JCM Print Portal v6 - security guide

v6 is v5 plus hardening. Nothing was removed; every new protection has a setting in `config.json` (or an environment
variable) so it can be loosened if a real customer file or device is refused.

## 1. What was fixed (from the v5 review)

| v5 problem | v6 fix |
|---|---|
| "Staff area is LAN-only" only because Cloudflare headers were absent (fails open) | Staff area and POS API are also refused on the **tunnel port** (`JCM_PUBLIC_PORTS`), when the connecting address is not private, and (optional) when it is not in `allowed_networks` |
| Customers on the shop Wi-Fi can open `/admin` | `JCM_ADMIN_NETWORKS` lets only the staff PC / phone in |
| Anyone on the LAN could fake `X-Forwarded-For` to dodge rate limits | Proxy headers are believed only from a trusted proxy (this PC) and must be a real IP |
| Upload limit trusted `Content-Length` (chunked uploads dodged it) | Bytes are counted as they arrive; JSON endpoints capped at 2 MB |
| Unauthenticated estimate could trigger huge PDF renders, zip bombs, parallel LibreOffice | Render capped (12 M pixels), zip metadata size capped, one LibreOffice at a time, process tree killed on timeout, max 4 analyses at once |
| Fake / macro / zip-bomb documents accepted | Deep checks for docx/xlsx/pptx/odt/ods/odp, program-disguised-as-text/PDF refused, macros in old .doc/.xls/.ppt flagged to staff |
| Virus-scan hook mis-handled Windows quoting and Defender's exit code | Quotes fixed, `scan_infected_codes`, optional `scan_fail_closed` |
| 4-digit PINs, 200k PBKDF2, shared PIN = owner and on by default | 6+ digits or 8+ chars password, trivial PINs refused, 600k rounds (old hashes upgrade on next login), shared PIN off by default |
| Lock-out per IP only; sessions could not be revoked; demoted owner stayed owner for 12 h | Lock-out per **account**; server-side sessions; role read live; "Log out everyone else" button |
| Secret files world-readable on Windows | `icacls` lock-down (and 0600 on Linux), also for backups |
| `/health` leaked the NAS path | Public answer is just "problem"; detail goes to the log |
| Service ran as root | Hardened service file (own user, no new privileges, read-only system, write only to data + NAS) |
| No HSTS / Permissions-Policy, early errors lacked headers | Added; every response (incl. 403/413) now carries them |
| Order folder from the database trusted blindly | Must live inside the orders directory |
| Dependencies unpinned | Minimums raised; `tools/audit_deps.*` runs `pip-audit` |

## 2. Do these three things (5 minutes)

**a) Wall off the tunnel port.** Your tunnel must point at a port that is *only* for the internet, and that port must be named
in `JCM_PUBLIC_PORTS`. Example: shop network uses port 80, tunnel points at 1013.

Windows launcher (add before `python server.py`; the same `set` lines go in `run_windows_tunnel.bat` / the NAS launcher):

    set JCM_PORT=80,1013
    set JCM_PUBLIC_PORTS=1013

and point the tunnel at `http://localhost:1013`. Linux: already in `jcm-portal.service`.
The start-up log warns `SECURITY: no tunnel port is set` until this is done.

**b) Only let your own devices open the staff area.** Customers on the Piso WiFi are on the same network as you.

    set JCM_ADMIN_NETWORKS=127.0.0.1/32,10.0.4.37/32,10.0.4.60/32

(this PC, plus the staff phone/laptop - give them fixed addresses / DHCP reservations). If you also use the POS connection:
`set JCM_POS_NETWORKS=10.0.4.20/32`. The same lists exist in `config.json` as `admin.allowed_networks` and
`integration.allowed_networks`. A typo in the list locks everyone out rather than letting everyone in.

**c) Create named accounts, then the shared PIN switches itself off.** Staff -> Tools -> Staff accounts (owners only).
Use 6+ digits or a password of 8+ characters. Old 4-digit accounts keep working until you reset them - please reset them.

## 3. Recommended next

* **Virus scanning (Windows Defender).** In `config.json`:

      "scan_command": "\"C:\\Program Files\\Windows Defender\\MpCmdRun.exe\" -Scan -ScanType 3 -File {file} -DisableRemediation",
      "scan_infected_codes": [2]

  Defender reports "threat found" as exit code 2. Add `"scan_fail_closed": true` to refuse orders when the scanner is broken.
* **Block old Office files** if you can live without them: `"blocked_extensions": ["doc","xls","ppt"]`. Staff open customer files on
  the shop PC; old formats can carry macros. (Macro-enabled .docx/.xlsx/.pptx/.odt are now refused automatically.)
* **HTTPS on the shop network** so the PIN and cookie are not readable on the Wi-Fi: `python tools/make_lan_cert.py 10.0.4.37`,
  then `set JCM_TLS_PORTS=8443` and use `https://10.0.4.37:8443/admin` on staff devices (accept the certificate once).
* **Client isolation** on the customer Wi-Fi / Piso WiFi (customers should not see each other or your staff devices). Put staff
  devices on a separate SSID or VLAN if your router allows it.
* **Turn off the name search** if you do not need it: `"status": {"name_search": false}` shows nothing about other people's orders.
* Run `tools/audit_deps.bat` once a month (needs internet) and keep Windows, Python and LibreOffice updated.
* Back up `data/` (it holds the staff hashes and sessions); keep the backup folder private.

## 4. Windows notes

* Secret files (`secret.key`, `admin_pin.txt`, `pos_api_key.txt`, `staff.json`, `sessions.json`, backups) are locked to your Windows
  account + SYSTEM + Administrators with `icacls`. Run the portal as the same Windows user every time.
* Do not run it as Administrator just to get port 80 if you can avoid it; 1013 and 8443 need no special rights.
* `require_private_peer` treats 10.x / 172.16-31.x / 192.168.x / 127.x as "inside". A Tailscale/CGNAT address (100.64.x) is not; set
  `"require_private_peer": false` only if you deliberately reach `/admin` that way (and then use `allowed_networks`).

## 5. If something is refused that should not be

| Symptom | Setting |
|---|---|
| Customer file refused as "not a real .docx" | `limits.deep_file_checks: false` (then tell me the file type) |
| Staff locked out | wait 10 min (`admin.lockout_minutes`) or restart the portal |
| `/admin` shows 403 on the shop PC | the PC's address is not in `JCM_ADMIN_NETWORKS`, or you opened it through the tunnel port |
| Tunnel on a different computer than the portal | `network.trusted_proxies`: add that computer's address |
| Order files not found in staff page | `security.confine_folders: false` if you moved the orders folder after taking orders |

## 6. What was NOT verified (be aware)

* Dependency vulnerabilities were not audited (no internet in the review environment) - run `tools/audit_deps.*`.
* Linkora's managed tunnel: the port wall (2a) makes the staff area safe whether or not it sends Cloudflare headers.
* Anyone with local access to the shop PC (or the NAS share) can read the order files; this portal cannot fix that.
* The tunnel provider can technically see tunnel traffic (normal for any managed tunnel).

## 7. Tests

`run_tests.sh` now also runs `test_security.py`, `test_middleware.py`, `test_app_rules.py` (no web server needed) and
`test_api_security.py` (starts the real server). The first three passed in the review environment; the web-server tests
(`test_api.py`, `test_api_security.py`, ...) need Starlette installed - run `./run_tests.sh` once after installing.
