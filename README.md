# JCM Print Portal (offline edition)

Customers join the shop Wi-Fi - **even the Piso WiFi without inserting a coin** - open the portal,
and send documents / photos / ID pictures straight to your counter. Nothing is loaded from the
internet: no CDN, no web fonts, no remote images. If the internet is down, the portal still works.

Pages: `/` home · `/print` send files · `/share` send from Facebook · `/status` customer order tracking (by code or name) ·
`/repair` · `/souvenirs` · `/admin` staff dashboard.

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
**Several ports at once:** put them in a comma list, e.g. `sudo JCM_PORT=80,1013 venv/bin/python server.py` (Windows:
`set JCM_PORT=80,1013` then `python server.py`). It is the same portal and the same data on every port - handy when the Piso WiFi
only allows one port, or so you can reach it as `http://<server-ip>/` and `http://<server-ip>:1013/`. Open each port in the firewall
(and whitelist each one in the Piso WiFi software). Ports below 1024 (like 80) need root / Administrator, and the port must not be
used by another program (IIS, Apache, Skype...). The service file now starts on `80,1013`.
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

- **Staff accounts** (Staff -> Tools, owners only): each person gets a name + PIN (6+ digits, or a password of 8+ characters; stored hashed) and a role.
  *Owner* can manage accounts, see the POS key, make/download backups; *Staff* can work with orders only. Every change
  is recorded under the person's name (order -> History; POS changes show as "POS: <cashier>"). While no account exists the
  old shared PIN keeps working; once everyone has an account, set `"admin": {"allow_shared_pin": false}` in `config.json`.
  Five wrong PINs lock that account (whoever is typing) for ~10 minutes. v6: `allow_shared_pin` now ships as `false`, so the shared PIN stops working as soon as the first named account exists.
- **Backups**: a zip of the portal's own data (order index, config, POS prices, staff accounts - but no PIN/keys) is made
  automatically every day into `data/backups/` (newest `backup.keep` = 14 kept). Set `backup.extra_dir` to a USB drive or a
  NAS folder to get a second copy. Staff -> Tools has *Back up now* and *Download latest*. The customer order folders on the
  NAS are not part of it - back those up with whatever backs up the NAS. To restore: stop the portal, unzip into `data/`.
- **GCash reference**: when `payment.gcash_number` is set in `config.json`, customers see the number and an optional box
  for their GCash reference. Staff and the cashier see it with a reminder to verify it - it never marks an order paid.
- **Warnings**: old `.doc/.xls/.ppt` files (which can carry macros) are accepted but flagged for staff and the cashier.

## 3g. Link / file sharing (Facebook, Canva, Google Drive, OneDrive, WPS...), install on the phone, finding an order by code or name

**Send from Facebook (`/share`).** A customer can hand you a Facebook post, a Messenger photo or any file with the phone's
*Share* button, or paste a copied link on `/share`. It becomes an order titled *Shared from Facebook* (or *Shared Items*) with
the links and files saved in the order folder (`instructions.txt` lists the links). The portal **cannot open a Facebook link
itself** (Facebook needs a login and the portal may be offline), so staff open the link on the shop computer; the order is always
flagged *check price* and you set the final price. In the staff order view the links show as buttons; links that are not
Facebook/Messenger/Instagram get a yellow *Other site* tag and a warning, because a stranger can send anything - open those with care.

**Which links work.** Any http(s) link is accepted. Links from known services are labelled for staff and raise no warning:
Facebook/Messenger/Instagram, **Canva, Google Drive/Docs/Sheets/Slides/Photos, OneDrive/SharePoint, WPS (kdocs/wps.com), Dropbox, iCloud, MEGA, Box,
WeTransfer, Notion, Figma, Adobe, Pinterest, TikTok, YouTube, Telegram, Viber, WhatsApp, Shopee, Lazada**. Anything else is tagged
*Other site* with a warning. To add a service, add a line to `SERVICES` in `portal/sharing.py` (no other change needed).
Cloud/design links (Canva, Drive, OneDrive, WPS, Dropbox...) only open if the customer set them to "Anyone with the link can view";
the order shows a reminder, and the share page asks for a mobile number so you can ask them to fix the permission.

**Install on the home screen.** The portal is now an installable web app (`/manifest.webmanifest`, `/sw.js`, icons in
`static/icons/`). The home page shows an *Install* card.
- **Android (Chrome / Samsung Internet):** tap *Install app* (not just "Add shortcut"). After installing, **JCM Prints appears in the
  Share menu** of Facebook, Messenger, Gallery and Files, and opens `/share` with the link/photos already filled in.
- **iPhone / iPad:** Safari -> Share -> *Add to Home Screen*. iOS does **not** allow web apps in the Share menu, so iPhone users copy
  the Facebook link and tap *Paste link* on `/share`.
- **Needs https.** Browsers only install apps and run service workers on https, so this works on your tunnel address
  (e.g. `https://jcm-gcs.nx1.lol`), **not** on the plain shop-Wi-Fi address `http://<server-ip>/` (customers there simply do not see
  the install card; everything else keeps working). Set `shop.public_url` in `config.json` to the https address - the home page
  points people to it.
- The service worker fetches pages from the network first and only uses its saved copy when the network fails, so your updates
  show up immediately. `/api` and `/admin` are never cached. To force phones to drop an old copy after a big change, change
  `SHELL = 'jcm-shell-v1'` in `static/sw.js` to `v2`.

**Order status by code or name (`/status`).** The order-number box is gone. Customers type their **6-letter code** (shows everything:
status, price, paid, your message) **or their name** (shows only the status, with the surname shortened to an initial - *Juan C.* - and no
price, no staff message, no order number). Name search looks at orders from the last 14 days, needs 3+ letters, and misses are
rate-limited. Old links `/status?o=NUMBER&c=CODE` and the older API `/api/status?id=..&code=..` still work. Staff can also search the
dashboard by code. In `config.json` (all optional):
```json
"status": {"name_search": true, "name_search_days": 14, "name_search_max_results": 8, "name_search_min_chars": 3},
"share":  {"enabled": true, "max_links": 10}
```
Set `"name_search": false` if you only want code lookups; `"share": {"enabled": false}` hides the Facebook option.
The printed slip now shows the code (and says the name works too).

## 3h. Editing the Souvenirs gallery and the Tech-Repair services (staff dashboard > Catalog)

Open **/admin > Catalog**. Nothing changes for customers until you press **Save changes** (a bar at the top shows "Unsaved changes").

**Souvenirs**
- **+ Add pictures** on any product (several at once, up to 12 per product). Pictures are checked, shrunk and stored in
  `data/catalog_images/`. The first picture is the cover; the yellow star makes another one the cover; the red x removes one.
  Customers tap a product to flip through all its pictures.
- **In stock** unticked = the product disappears from the customer page (it stays in the editor so you can tick it again).
  The red **Delete** removes it for good. Tick *Still show out-of-stock products* if you prefer customers to see them greyed out
  with an "Out of stock" tag. A category with nothing in stock is hidden automatically.
- Add, rename, reorder or hide whole categories; add an optional price note per product; edit the "What do you want?" choices of the order form.

**Tech repair**
- Add or remove services (name, price text, notes) in each section, hide a single service with **Offered**, reorder, or add whole new sections.
- A **Mobile Services** section is already there but **switched off**. Check its placeholder prices ("Ask us"), tick **Show** and save to publish
  it - "Mobile phone / Tablet" is then added to the device list of the repair request form automatically.

Good to know: the original page content is the starting point (nothing is lost). The catalog is saved in `data/catalog.json` (the one
before the last save is kept as `catalog.prev.json`) and is included in the nightly backup together with the pictures. If the file is ever
damaged the pages fall back to the built-in content. Tests: `tests/test_catalog.py`, `tests/test_api_catalog.py`.

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
The sharing / status-search tests are `tests/test_share.py` (logic) and `tests/test_api_share.py` (server).

## Known limits

- HTTP only (no HTTPS) - fine on an offline LAN; staff PIN travels in clear text on that LAN.
- Customers with HEIC photos on non-Apple phones cannot preview them; those are sent as-is and
  flagged for staff to place by hand.
- No online payments; GCash number is shown for manual payment only.
- Shared Facebook links are not fetched or previewed by the portal; staff open them. Videos are not accepted as files (the link is enough).
- iPhone cannot add the app to its Share menu (Apple does not allow it) - use *Paste link*.

## Security hardening (v6) - read SECURITY.md

v6 adds: a tunnel-port wall and per-device allow-list for the staff area and POS API (`JCM_PUBLIC_PORTS`, `JCM_ADMIN_NETWORKS`),
a real byte-count upload limit (chunked uploads can no longer dodge it), file checks that look inside Office/zip files
(zip bombs, macros, fake files), a bounded PDF renderer, server-side sessions (logout / PIN reset / removed account really end
a login), per-account lock-out, stronger PIN rules and hashing, owner-only secret files (also on Windows), optional HTTPS
on the shop network, and a hardened service file. **Three settings are strongly recommended - see SECURITY.md section 2.**
