# JCM Print Portal (offline edition)

Customers join the shop Wi-Fi - **even the Piso WiFi without inserting a coin** - open the portal,
and send documents / photos / ID pictures straight to your counter. Nothing is loaded from the
internet: no CDN, no web fonts, no remote images. If the internet is down, the portal still works.

Pages: `/` home · `/print` send files · `/status` customer order tracking · `/repair` · `/souvenirs` ·
`/admin` staff dashboard.

## 1. Install (one time - the server needs internet only for this step)

```bash
cd JCM_Print-Portal
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```
No internet on the server at all? On any PC with internet run
`pip download -r requirements.txt -d wheels` (same Python version/OS as the server), copy the `wheels`
folder across, then `venv/bin/pip install --no-index --find-links wheels -r requirements.txt`.

## 2. Run

```bash
sudo venv/bin/python server.py          # port 80  ->  http://<server-ip>/
# or without root:  JCM_PORT=8080 venv/bin/python server.py   ->  http://<server-ip>:8080/
```
Auto-start on boot: see `jcm-portal.service`.

First run creates the **staff PIN** in `data/admin_pin.txt` (or set `JCM_ADMIN_PIN`). Open `/admin`.

## 3. Piso WiFi setup (important)

Customers' phones are on the Piso WiFi network but **not** "paid", so the Piso WiFi software blocks
them from the internet. The portal is on your own server, so you only need to let them reach *that*:

1. Give the portal server a **fixed IP** (DHCP reservation or static), e.g. `192.168.10.5`.
2. In your Piso WiFi admin, add that IP to the free-access list. It is called different things in
   different software: *whitelist*, *walled garden*, *allowed hosts*, *free sites*, *bypass*, *no-internet
   access*. Add the server's IP (and port if not 80). If your software only whitelists websites by
   name, use the IP address.
3. Make sure client isolation is **off** between customers and the server (or the server is on the
   same LAN the Piso WiFi hands out).
4. Test from a phone that has *not* paid: open `http://192.168.10.5/`.
5. Print the poster: `/admin` -> Tools -> *Print poster* (needs `pip install segno` for the QR code,
   already in requirements.txt). Put the Wi-Fi name on it.

Phones often say "No internet - stay connected?". Customers must tap *Stay connected* (the home page
explains this). If the page will not load, turning off mobile data for a moment fixes it.
I could not test your specific Piso WiFi software - step 2 is the part to check on your side.

## 4. Where orders go

`config.json -> storage`: `upload_dir` (default `/mnt/nas_spool/customer_orders`) and `mount_point`
(default `/mnt/nas_spool`). Orders are refused with a clear message - instead of silently filling the
server's own disk - when the NAS is not mounted. (If your orders folder is a plain folder, not a mounted drive, set `"mount_point": ""`.) Folder layout is unchanged:
`<date>/<NNN>_[Name] - <Request>/` with the files, `instructions.txt` and `order.json`.
Repairs are `R001_...`, souvenir inquiries `S001_...`.
`data/portal.db` is only a search index; if lost, **Tools -> Re-scan** rebuilds it from the folders.

## 5. Changing prices, layouts, shop info

Edit `config.json` (shop name, hours, phone, prices, photo sizes, Rush ID packages, GCash number,
retention days). It reloads automatically - no restart. A typo falls back to defaults and logs a warning.
Photo sizes are real millimetres; the best orientation is chosen automatically.
`analysis.min_ratio` controls how much colour makes a page count as colour (default 0.05% of pixels,
roughly a 2 cm logo). Raise it to charge colour less often.

Word/Excel/PowerPoint: page count is read from the file; installing LibreOffice on the server
(`sudo apt install libreoffice-core`) makes it exact and detects colour. Otherwise those orders are
flagged "check price" for staff.

## 6. Staff dashboard (`/admin`)

Live queue with sound alert for new orders, status buttons (the customer sees them on `/status`),
paid/unpaid + method, final price override, messages to the customer, download all as zip, printable
slip, sales summary + CSV export, re-scan, poster.

## 7. Privacy

Customer files are deleted `retention.days` (default 14) after an order is Completed/Cancelled;
`order.json` stays for your sales records. Set `"days": 0` to keep everything.

## 8. Tests

`./run_tests.sh` - logic, JS-vs-Python layout agreement, full server API, and real-browser tests.

## Known limits

- HTTP only (no HTTPS) - fine on an offline LAN; staff PIN travels in clear text on that LAN.
- Customers with HEIC photos on non-Apple phones cannot preview them; those are sent as-is and
  flagged for staff to place by hand.
- No online payments; GCash number is shown for manual payment only.
