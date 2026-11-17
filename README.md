# JCM Print Portal (offline edition)

Customers join the shop Wi-Fi - **even the Piso WiFi without inserting a coin** - open the portal,
and send documents / photos / ID pictures straight to your counter. Nothing is loaded from the
internet: no CDN, no web fonts, no remote images. If the internet is down, the portal still works.

Pages: `/` home · `/print` send files · `/status` customer order tracking · `/repair` · `/souvenirs` ·
`/admin` staff dashboard.

## Upgrading from an earlier version (overwriting is fine - keep these 3 things)

1. Stop the server.
2. Copy the new files over the old folder, but **do not replace**:
   - `venv/` (your Python packages),
   - `data/` (your order index, staff PIN, POS key, staff accounts, backups),
   - `config.json` (your shop name, hours, phone, prices, GCash number). New settings are added automatically with
     their defaults, so you do not need the new file. (If you overwrote it by mistake, just re-enter your shop info.)
3. Install the new package once:  `venv/bin/pip install -r requirements.txt`  (new this time: **numpy**).
   Without it the server stops at start-up and tells you to run this command.
4. Start the server. Existing orders appear as before (old orders keep the price they were given). Hard-refresh the
   customer pages on your phone once (they cache the old scripts).

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

## 3b. Also publishing through Cloudflare Tunnel (linkora.top)

The same server can be used two ways at once:

| Who | Opens | Needs internet? |
|---|---|---|
| Customers on your Piso WiFi (unpaid) | `http://<server-ip>/` (poster QR) | **No** |
| Anyone with mobile data / other Wi-Fi | `https://yourname.linkora.top` | Yes (shop's internet must be up) |

Customers on the unpaid Piso WiFi **cannot** open the linkora.top address - it needs internet - so give
them the local address/QR. Set `shop.public_url` in `config.json` and the poster shows both.

Point the tunnel at the portal, e.g. in `cloudflared` config:
```yaml
ingress:
  - hostname: yourname.linkora.top
    service: http://localhost:80      # or http://localhost:8080 if you use JCM_PORT=8080
  - service: http_status:404
```
Built-in protections for tunnel traffic (the server recognises Cloudflare's headers):
- **Staff area is blocked from the internet** (`/admin`, staff API, slips). It only works on the shop
  network. To allow remote staff access set `"admin": {"allow_remote": true}` - only do that together
  with **Cloudflare Access** (free Zero Trust, e.g. email one-time-PIN) in front of `/admin*`.
- **Rate limits are per real visitor** (uses `CF-Connecting-IP`), so one person cannot lock everyone out.
- **Uploads through the tunnel are capped at 95 MB per order** (Cloudflare's free plan rejects requests
  over 100 MB). Customers see a clear message telling them to send fewer files or use the shop Wi-Fi.
  Shop Wi-Fi uploads keep the larger limit (`limits.max_request_mb`).
- Login cookies are marked `Secure` automatically when the page is opened over https.
- Cloudflare also drops requests that take longer than ~100 s to get a response; on slow mobile data
  a very large upload can fail - customers can retry or use the shop Wi-Fi.
- Do not forward port 80 on your router at the same time; let the tunnel be the only way in.
- Customers' names and files pass through Cloudflare (encrypted); mention it if you publish a privacy note.
- "My recent orders" is remembered per address, so the LAN address and the linkora.top address keep
  separate lists (orders can always be looked up with number + code on either).

## 3c. Connecting the POS (cash register program)

The add-on in `JCM_PrintPOS_portal-addon.zip` adds an **Online Orders** tab to the POS. Setup:
1. Portal: **Staff -> Tools -> POS connection** shows the **POS key** (and when the POS last connected).
2. POS: **Online Orders -> Connection**: enter this server's address on the shop network (e.g. `http://192.168.1.10`)
   and the key. The POS connection works **only on the shop network** - it is refused for tunnel/internet traffic
   even with the right key. **New key** in the portal locks out the old one.
3. The POS sends its price list; the portal then quotes customers with the POS prices
   (Staff -> Tools shows "Prices: from the POS"). Until then the prices in `config.json` are used.

## 3d. Prices now match the POS

Pages are priced in four colour levels (B&W / light / medium / full) using the same rule and thresholds as the POS,
plus the paper, Long (folio) and two-sided rules. Photo sheets on plain/photo paper are priced like a full-colour
page; 180gsm and sticker keep fixed prices in `config.json`. Customers see the price build up per level, and the
server always recalculates the final price itself.

## 3e. Security: what is built in, what you should do

Built in: staff PIN with lockout and per-visitor rate limits; staff area and POS connection refused from the internet;
cross-site (CSRF) request blocking; uploads checked by type, size and real content; optional blocked file types
(`limits.blocked_extensions`, e.g. `["doc","xls","ppt"]` to refuse old macro-capable Office files); refuses orders when
the NAS is offline or the disk is nearly full (`limits.min_free_gb`); every staff/POS change is recorded with who and when
(order -> History); customer files auto-deleted after `retention.days`.

Optional virus scan of every upload: install ClamAV and set in `config.json`
`"scan_command": "clamscan --no-summary {file}"` (exit code 1 = infected = upload refused; scanner problems never
block customers, the order is just flagged "not scanned").

You should: give each person their own login (Staff -> Tools -> Staff accounts) and remove it when they leave - it stops working immediately;
keep Windows/Linux updated on the machine that opens customer files; back up `data/` and the NAS regularly;
put Cloudflare Access in front of the domain if you ever enable `admin.allow_remote`.

## 3f. Staff accounts, backups, GCash reference, warnings

- **Staff accounts** (Staff -> Tools, owners only): each person gets a name + PIN (4-12 digits, stored hashed) and a role.
  *Owner* can manage accounts, see the POS key, make/download backups; *Staff* can work with orders only. Every change
  is recorded under the person's name (order -> History; POS changes show as "POS: <cashier>"). While no account exists the
  old shared PIN keeps working; once everyone has an account, set `"admin": {"allow_shared_pin": false}` in `config.json`.
  Repeated wrong PINs lock that account for ~10 minutes.
- **Backups**: a zip of the portal's own data (order index, config, POS prices, staff accounts - but no PIN/keys) is made
  automatically every day into `data/backups/` (newest `backup.keep` = 14 kept). Set `backup.extra_dir` to a USB drive or a
  NAS folder to get a second copy. Staff -> Tools has *Back up now* and *Download latest*. The customer order folders on the
  NAS are not part of it - back those up with whatever backs up the NAS. To restore: stop the portal, unzip into `data/`.
- **GCash reference**: when `payment.gcash_number` is set in `config.json`, customers see the number and an optional box
  for their GCash reference. Staff and the cashier see it with a reminder to verify it - it never marks an order paid.
- **Warnings**: old `.doc/.xls/.ppt` files (which can carry macros) are accepted but flagged for staff and the cashier.

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
`analysis.thresh_*` are the colour-level cut-offs (same as the POS defaults 2% / 25% / 60%).

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
