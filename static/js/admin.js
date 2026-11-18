/* Staff dashboard. Plain JS, offline. */
(function () {
  'use strict';
  const $id = id => document.getElementById(id);
  const A = { date: '', all: false, status: 'active', cat: 'all', q: '', sel: null, latest: null, sound: false, ctx: null, orders: [], statuses: null, timer: null, newIds: new Set() };
  const today = () => { const d = new Date(Date.now() + 8 * 3600e3); return d.toISOString().slice(0, 10); };   // Philippines date

  async function api(path, opt) {
    const r = await fetch(path, Object.assign({ credentials: 'same-origin', cache: 'no-store' }, opt || {}));
    if (r.status === 401) { showLogin(); throw new Error('login'); }
    const ct = r.headers.get('content-type') || '';
    const body = ct.includes('json') ? await r.json() : await r.text();
    if (!r.ok) throw new Error((body && body.detail) || 'Error ' + r.status);
    return body;
  }
  const jpost = (p, o, m) => api(p, { method: m || 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(o) });

  // ---------- login
  async function loadNames() {
    try {
      const r = await (await fetch('/api/admin/staff-names', { credentials: 'same-origin' })).json();
      const sel = $id('staffName'); sel.innerHTML = '';
      (r.names || []).forEach(n => { const o = document.createElement('option'); o.value = n; o.textContent = n; sel.appendChild(o); });
      if (r.shared_pin) { const o = document.createElement('option'); o.value = ''; o.textContent = '(shared PIN)'; sel.appendChild(o); }
      $id('nameRow').classList.toggle('hidden', !(r.names || []).length);
    } catch (e) { /* ignore */ }
  }
  function showLogin() { loadNames(); $id('loginCard').classList.remove('hidden'); $id('app').classList.add('hidden'); $id('logoutBtn').classList.add('hidden'); $id('soundBtn').classList.add('hidden'); clearInterval(A.timer); }
  async function showApp() {
    $id('loginCard').classList.add('hidden'); $id('app').classList.remove('hidden');
    $id('logoutBtn').classList.remove('hidden'); $id('soundBtn').classList.remove('hidden');
    try { A.me = await api('/api/admin/me'); $id('whoami').textContent = A.me.name + (A.me.role === 'owner' ? ' (owner)' : ''); $id('ownerTools').classList.toggle('hidden', A.me.role !== 'owner'); } catch (e) { /* ignore */ }
    await loadOrders();
    clearInterval(A.timer); A.timer = setInterval(poll, 5000);
  }
  $id('loginForm').addEventListener('submit', async e => {
    e.preventDefault();
    $id('loginErr').classList.add('hidden');
    try { await jpost('/api/admin/login', { name: $id('nameRow').classList.contains('hidden') ? '' : $id('staffName').value, pin: $id('pin').value }); $id('pin').value = ''; showApp(); }
    catch (er) { $id('loginErr').textContent = er.message === 'login' ? 'Wrong name or PIN.' : er.message; $id('loginErr').classList.remove('hidden'); }
  });
  $id('logoutBtn').addEventListener('click', async () => { try { await jpost('/api/admin/logout', {}); } catch (e) { /* ignore */ } showLogin(); });

  // ---------- sound + polling
  function beep() {
    if (!A.sound || !A.ctx) return;
    [0, 0.25].forEach((t, i) => {
      const o = A.ctx.createOscillator(), g = A.ctx.createGain();
      o.frequency.value = i ? 1180 : 880; o.connect(g); g.connect(A.ctx.destination);
      g.gain.setValueAtTime(0.0001, A.ctx.currentTime + t); g.gain.exponentialRampToValueAtTime(0.35, A.ctx.currentTime + t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, A.ctx.currentTime + t + 0.22);
      o.start(A.ctx.currentTime + t); o.stop(A.ctx.currentTime + t + 0.25);
    });
  }
  $id('soundBtn').addEventListener('click', () => {
    A.sound = !A.sound;
    if (A.sound && !A.ctx) { try { A.ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { A.sound = false; } }
    if (A.ctx && A.ctx.state === 'suspended') A.ctx.resume();
    $id('soundBtn').textContent = A.sound ? '\uD83D\uDD14 Sound on' : '\uD83D\uDD14 Sound off';
    $id('soundBtn').setAttribute('aria-pressed', A.sound);
    if (A.sound) beep();
  });
  async function poll() {
    if (document.hidden && !A.sound) return;
    try {
      const p = await api('/api/admin/poll');
      setWaiting(p.waiting);
      if (A.latest !== null && p.latest && p.latest !== A.latest) { beep(); toast('New order!', 'ok'); document.title = '(new) Staff - Orders'; setTimeout(() => { document.title = 'Staff - Orders'; }, 8000); await loadOrders(true); }
      A.latest = p.latest || A.latest;
    } catch (e) { /* offline blip */ }
  }
  function setWaiting(n) { const b = $id('waiting'); b.textContent = n + ' waiting'; b.classList.toggle('hidden', !n); }

  // ---------- list
  function chips(el, items, cur, cb) {
    el.innerHTML = items.map(([v, l]) => '<button type="button" class="chip" data-v="' + esc(v) + '" aria-pressed="' + (v === cur) + '">' + esc(l) + '</button>').join('');
    el.onclick = e => { const b = e.target.closest('[data-v]'); if (b) { cb(b.dataset.v); } };
  }
  function buildFilters(counts) {
    const c = counts || {};
    const act = ['Pending', 'Printing', 'Received', 'Diagnosing', 'Waiting for Approval', 'Repairing', 'Inquiry Received', 'Quoted', 'In Production', 'Ready for Pickup'].reduce((s, k) => s + (c[k] || 0), 0);
    chips($id('fStatus'), [['active', 'Active (' + act + ')'], ['Completed', 'Completed (' + (c.Completed || 0) + ')'], ['Cancelled', 'Cancelled (' + (c.Cancelled || 0) + ')'], ['all', 'All']], A.status, v => { A.status = v; loadOrders(); });
    chips($id('fCat'), [['all', 'Everything'], ['print', 'Printing'], ['repair', 'Repairs'], ['souvenir', 'Souvenirs']], A.cat, v => { A.cat = v; loadOrders(); });
  }
  async function loadOrders(keepSel) {
    const qs = new URLSearchParams({ date: A.all ? 'all' : A.date, status: A.status, category: A.cat, q: A.q });
    const d = await api('/api/admin/orders?' + qs);
    const prev = new Set(A.orders.map(o => o.id));
    A.newIds = new Set(A.orders.length ? d.orders.filter(o => !prev.has(o.id)).map(o => o.id) : []);
    A.orders = d.orders; A.statuses = d.statuses;
    if (A.latest === null) A.latest = d.poll.latest || '';
    setWaiting(d.poll.waiting);
    const w = $id('storageWarn'); w.classList.toggle('hidden', d.storage.ok); w.textContent = d.storage.ok ? '' : '\u26A0 ' + d.storage.message + ' - customers cannot send orders until this is fixed.';
    buildFilters(d.counts);
    $id('oempty').classList.toggle('hidden', d.orders.length > 0);
    $id('olist').innerHTML = d.orders.map(o => {
      const t = (o.created || '').slice(11, 16);
      return '<li data-id="' + esc(o.id) + '" class="' + (o.id === A.sel ? 'sel ' : '') + (A.newIds.has(o.id) ? 'new' : '') + '"><div class="row spread"><b>' + esc(o.id) + '</b><span class="badge ' + badgeCls(o) + '">' + esc(o.status) + '</span></div>' +
        '<div>' + esc(o.name) + (o.needs_review ? ' <span class="badge warn">check price</span>' : '') + '</div>' +
        '<div class="small muted">' + esc(o.preset) + '</div><div class="small row spread"><span>' + esc(t) + ' &middot; ' + o.files + ' file(s)</span><b>' + (o.total ? peso(o.total) : '') + (o.paid ? ' \u2705' : '') + '</b></div></li>';
    }).join('');
    if (A.sel && keepSel !== false && d.orders.some(o => o.id === A.sel)) showDetail(A.sel, true);
  }
  const badgeCls = o => o.status === 'Cancelled' ? 'bad' : (o.status === 'Completed' || o.status === 'Ready for Pickup') ? 'ok' : (['Pending', 'Received', 'Inquiry Received'].includes(o.status) ? 'warn' : '');
  $id('olist').addEventListener('click', e => { const li = e.target.closest('li[data-id]'); if (li) { A.sel = li.dataset.id; $id('olist').querySelectorAll('li').forEach(x => x.classList.toggle('sel', x === li)); showDetail(A.sel); document.getElementById('detail').scrollIntoView({ behavior: 'smooth', block: 'start' }); } });

  // ---------- detail
  async function showDetail(id, quiet) {
    let o;
    try { o = await api('/api/admin/orders/' + encodeURIComponent(id)); } catch (e) { if (!quiet) toast(e.message, 'bad'); return; }
    const d = o.data || {}, base = '/api/admin/orders/' + encodeURIComponent(id);
    const opts = Object.keys(d.options || {}).filter(k => k !== 'per_file').map(k => '<dt>' + esc(k.replace(/_/g, ' ')) + '</dt><dd>' + esc(String(d.options[k])) + '</dd>').join('');
    const perFile = (d.options && d.options.per_file || []).map(f => '<li>' + esc(f.name) + ' - ' + f.pages + ' pg (' + f.bw_pages + ' B&W / ' + (f.half_pages || 0) + ' light / ' + (f.tq_pages || 0) + ' medium / ' + (f.full_pages || 0) + ' full)' + (f.status !== 'ok' ? ' <span class="badge warn">' + esc(f.status) + '</span>' : '') + '</li>').join('');
    const files = o.file_list.map(f => {
      const ext = f.name.split('.').pop().toLowerCase(), isImg = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(ext);
      const url = base + '/file/' + encodeURIComponent(f.name);
      return '<li style="display:flex;gap:10px;align-items:center;margin:6px 0">' + (isImg ? '<img class="preview" src="' + url + '?inline=1" alt="" loading="lazy">' : '<span style="font-size:1.6rem">\uD83D\uDCC4</span>') +
        '<div class="grow"><div style="word-break:break-all">' + esc(f.name) + '</div><div class="small muted">' + fmtBytes(f.size) + '</div></div>' +
        '<a class="btn small outline" href="' + url + '" download>Download</a>' + (ext === 'pdf' ? ' <a class="btn small ghost" target="_blank" href="' + url + '?inline=1">Open</a>' : '') + '</li>';
    }).join('');
    const info = d.links_info || (d.links || []).map(u => ({ url: u, label: 'Link', known: true }));
    const links = info.filter(x => /^https?:\/\//i.test(x.url)).map(x => { const u = x.url;
      return '<li style="margin:6px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="badge ' + (x.known ? 'ok' : 'warn') + '">' + esc(x.label) + '</span>' +
        '<a class="grow" style="word-break:break-all" href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(u) + '</a></li>';
    }).join('');
    const sts = (o.statuses || []).map(s => '<button type="button" class="chip" data-st="' + esc(s) + '" aria-pressed="' + (s === o.status) + '">' + esc(s) + '</button>').join('');
    $id('detail').innerHTML =
      '<div class="row spread"><h2 style="margin:0">' + esc(o.id) + '</h2><span class="badge ' + badgeCls(o) + '">' + esc(o.status) + '</span></div>' +
      '<dl class="kv" style="margin-top:10px"><dt>Customer</dt><dd><b>' + esc(o.name) + '</b></dd>' +
      '<dt>Phone</dt><dd>' + (o.phone ? '<a href="tel:' + esc(o.phone) + '">' + esc(o.phone) + '</a>' : '-') + '</dd>' +
      '<dt>Request</dt><dd>' + esc(o.preset) + '</dd><dt>Received</dt><dd>' + esc(d.date || '') + ' ' + esc(d.time || '') + '</dd>' +
      '<dt>Code</dt><dd><b>' + esc(o.code) + '</b></dd>' + opts + '</dl>' +
      (perFile ? '<ul class="small">' + perFile + '</ul>' : '') +
      (d.breakdown && d.breakdown.length ? '<div class="alert info small">' + d.breakdown.map(esc).join('<br>') + '</div>' : '') +
      (d.review_notes && d.review_notes.length ? '<div class="alert warn small"><b>Please check:</b><br>' + d.review_notes.map(esc).join('<br>') + '</div>' : '') +
      (d.customer_pay_ref ? '<div class="alert warn small"><b>Customer says they paid by GCash.</b> Reference: <b>' + esc(d.customer_pay_ref) + '</b> - check it in your GCash app before you mark it paid.</div>' : '') +
      (d.warnings && d.warnings.length ? '<div class="alert bad small"><b>Warning:</b><br>' + d.warnings.map(esc).join('<br>') + '</div>' : '') +
      (links ? '<h3>Shared links</h3><ul style="list-style:none;padding:0;margin:0">' + links + '</ul>' : '') +
      (d.instructions ? '<h3>Customer notes</h3><div class="alert info" style="white-space:pre-wrap">' + esc(d.instructions) + '</div>' : '') +
      '<h3>Files' + (o.files_deleted ? ' (deleted after retention period)' : '') + '</h3><ul style="list-style:none;padding:0;margin:0">' + files + '</ul>' +
      '<div class="row" style="margin:10px 0"><a class="btn small" href="' + base + '/zip">Download all (zip)</a> <a class="btn small outline" target="_blank" href="/admin/slip/' + encodeURIComponent(id) + '?print=1">Print slip</a></div>' +
      '<div><label class="small">Folder on the NAS</label><input type="text" readonly value="' + esc(o.folder) + '"></div>' +
      '<h3 style="margin-top:14px">Status</h3><div class="chips" id="stChips">' + sts + '</div>' +
      '<h3 style="margin-top:14px">Payment</h3><div class="row">' +
      '<div><label class="small" for="eTotal">Final price</label><input type="number" id="eTotal" min="0" step="0.5" value="' + o.total + '" style="width:130px"></div>' +
      '<div><label class="small" for="ePay">Method</label><select id="ePay" style="width:130px"><option value="">-</option><option>Cash</option><option>GCash</option><option>Other</option></select></div>' +
      '<label class="check" style="margin-top:20px"><input type="checkbox" id="ePaid"' + (o.paid ? ' checked' : '') + '><span>Paid</span></label></div>' +
      (d.computed_total !== undefined && d.computed_total !== o.total ? '<p class="small muted">Computed by the system: ' + peso(d.computed_total) + '</p>' : '') +
      '<h3 style="margin-top:14px">Notes</h3>' +
      '<label class="small" for="ePub">Message the customer can see on their status page</label><textarea id="ePub" maxlength="1000">' + esc(d.public_note || '') + '</textarea>' +
      '<label class="small" for="eStaff">Private staff note</label><textarea id="eStaff" maxlength="1000">' + esc(d.staff_note || '') + '</textarea>' +
      '<details style="margin-top:12px"><summary><b>History</b> (who changed what)</summary><ul class="small" style="padding-left:18px">' +
      (d.history || []).slice().reverse().map(h => '<li>' + esc((h.at || '').replace('T', ' ').slice(0, 16)) + ' - ' + esc(h.by || '') + ': ' + esc(h.status ? 'status \u2192 ' + h.status : (h.change || 'created')) + '</li>').join('') + '</ul></details>' +
      (d.claimed_by || d.pos_ref ? '<p class="small muted">POS: ' + esc(d.claimed_by || '') + ' ' + esc(d.pos_ref || '') + '</p>' : '') +
      '<div class="sticky-actions"><button class="btn block" id="eSave" type="button">Save changes</button></div>';
    $id('ePay').value = o.pay_method || '';
    const fi = $id('detail').querySelector('input[readonly]'); if (fi) fi.addEventListener('focus', ev => ev.target.select());
    $id('stChips').onclick = async e => {
      const b = e.target.closest('[data-st]'); if (!b) return;
      try { await jpost(base, { status: b.dataset.st }, 'PATCH'); toast('Status: ' + b.dataset.st, 'ok'); await loadOrders(); showDetail(id, true); } catch (er) { toast(er.message, 'bad'); }
    };
    $id('eSave').onclick = async () => {
      try {
        const r = await jpost(base, { total: $id('eTotal').value, paid: $id('ePaid').checked, payment_method: $id('ePay').value, public_note: $id('ePub').value, staff_note: $id('eStaff').value }, 'PATCH');
        toast(r.json_synced ? 'Saved' : 'Saved (NAS offline - order.json not updated)', r.json_synced ? 'ok' : 'bad'); await loadOrders(); showDetail(id, true);
      } catch (er) { toast(er.message, 'bad'); }
    };
  }

  // ---------- filters
  $id('fDate').addEventListener('change', e => { A.date = e.target.value || today(); A.all = false; $id('fAll').checked = false; loadOrders(); });
  $id('fAll').addEventListener('change', e => { A.all = e.target.checked; loadOrders(); });
  let qt = null; $id('fQ').addEventListener('input', e => { clearTimeout(qt); qt = setTimeout(() => { A.q = e.target.value.trim(); loadOrders(); }, 300); });

  // ---------- tabs, summary, tools
  document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('[data-tab]').forEach(x => x.setAttribute('aria-pressed', x === b));
    ['orders', 'summary', 'catalog', 'tools'].forEach(t => $id('tab-' + t).classList.toggle('hidden', t !== b.dataset.tab));
    if (b.dataset.tab === 'summary') loadSummary(); if (b.dataset.tab === 'tools') loadTools();
    if (b.dataset.tab === 'catalog' && window.JCMCatalogAdmin) window.JCMCatalogAdmin.open();
  }));
  async function loadSummary() {
    const f = $id('sFrom').value, t = $id('sTo').value, qs = new URLSearchParams({ from: f, to: t });
    $id('sCsv').href = '/api/admin/export.csv?' + qs;
    const s = await api('/api/admin/summary?' + qs), T = s.totals;
    $id('sOut').innerHTML = '<div class="row" style="gap:20px;margin:8px 0"><div><div class="small muted">Orders</div><b style="font-size:1.4rem">' + T.orders + '</b></div><div><div class="small muted">Sales</div><b style="font-size:1.4rem">' + peso(T.total) + '</b></div><div><div class="small muted">Paid</div><b style="font-size:1.4rem;color:#16803c">' + peso(T.paid) + '</b></div><div><div class="small muted">Unpaid</div><b style="font-size:1.4rem;color:#b42318">' + peso(T.unpaid) + '</b></div></div>' +
      '<div class="tablewrap"><table><thead><tr><th>Date</th><th>Orders</th><th>Total</th><th>Paid</th><th>Unpaid</th></tr></thead><tbody>' + s.days.map(d => '<tr><td>' + esc(d.date) + '</td><td>' + d.orders + '</td><td>' + peso(d.total) + '</td><td>' + peso(d.paid) + '</td><td>' + peso(d.unpaid) + '</td></tr>').join('') + '</tbody></table></div>' +
      '<h3 style="margin-top:14px">By type</h3><div class="tablewrap"><table><thead><tr><th>Type</th><th>Orders</th><th>Total</th></tr></thead><tbody>' + s.kinds.map(k => '<tr><td>' + esc(k.kind) + '</td><td>' + k.orders + '</td><td>' + peso(k.total) + '</td></tr>').join('') + '</tbody></table></div>' +
      '<p class="small muted">Cancelled orders are not counted.</p>';
  }
  $id('sGo').addEventListener('click', loadSummary);
  async function loadTools() {
    const d = await api('/api/admin/orders?date=' + today());
    $id('toolStatus').innerHTML = d.storage.ok ? '<div class="alert ok">Shop storage: connected.</div>' : '<div class="alert bad">' + esc(d.storage.message) + '</div>';
    const url = location.origin + '/';
    if (A.me && A.me.role === 'owner') { loadPos(); loadStaff(); loadBackup(); }
    $id('qrBox').textContent = '';
    try {
      const r = await fetch('/api/admin/qr.svg?text=' + encodeURIComponent(url), { credentials: 'same-origin' });
      if (!r.ok) throw new Error('no qr');
      const img = document.createElement('img'); img.className = 'poster-qr'; img.alt = 'QR code'; img.src = URL.createObjectURL(await r.blob());
      const cap = document.createElement('p'); cap.className = 'small muted'; cap.textContent = url;
      $id('qrBox').appendChild(img); $id('qrBox').appendChild(cap);
    } catch (e) {
      $id('qrBox').innerHTML = '<p class="alert warn small">QR codes need the optional package: <b>pip install segno</b> (then restart). The poster still shows the web address.</p>';
    }
  }
  async function loadPos() {
    try {
      const p = await api('/api/admin/pos');
      const when = p.last_seen ? p.last_seen.replace('T', ' ').slice(0, 16) + ' (' + p.last_ip + ')' : 'never';
      $id('posBox').innerHTML = '<div class="alert ' + (p.last_seen ? 'ok' : 'info') + '">POS last connected: <b>' + esc(when) + '</b></div>' +
        '<div class="small">Prices: <b>' + (p.prices_source === 'pos' ? 'from the POS (synced ' + esc((p.prices_synced_at || '').replace('T', ' ').slice(0, 16)) + ')' : 'from config.json (POS has not synced yet)') + '</b></div>' +
        '<label class="small" style="margin-top:8px">POS key (paste into the POS Settings)</label><div class="row"><input type="password" id="posKey" readonly value="' + esc(p.key) + '" class="grow">' +
        '<button type="button" class="btn small ghost" id="posKeyShow">Show</button><button type="button" class="btn small danger" id="posKeyNew">New key</button></div>';
      $id('posKeyShow').onclick = () => { const k = $id('posKey'); k.type = k.type === 'password' ? 'text' : 'password'; };
      $id('posKeyNew').onclick = async () => { if (confirm('Make a new key? The POS must be given the new one.')) { await jpost('/api/admin/pos/rotate', {}); loadPos(); } };
    } catch (e) { /* ignore */ }
  }
  async function loadStaff() {
    try {
      const r = await api('/api/admin/staff');
      $id('staffBox').innerHTML = r.staff.length ? '<ul style="padding-left:18px;margin:4px 0">' + r.staff.map(p => '<li><b>' + esc(p.name) + '</b> - ' + esc(p.role) + ' <button type="button" class="btn small ghost" data-del="' + esc(p.name) + '">Remove</button></li>').join('') + '</ul>' : '<span class="muted">No named accounts yet - everyone uses the shared PIN. Add yourself first as <b>Owner</b>.</span>';
      $id('staffBox').querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { if (confirm('Remove ' + b.dataset.del + '?')) { try { await jpost('/api/admin/staff/delete', { name: b.dataset.del }); loadStaff(); } catch (e) { toast(e.message, 'bad'); } } });
    } catch (e) { /* ignore */ }
  }
  $id('stSave').addEventListener('click', async () => {
    try { await jpost('/api/admin/staff', { name: $id('stName').value, pin: $id('stPin').value, role: $id('stRole').value }); $id('stPin').value = ''; toast('Saved', 'ok'); loadStaff(); }
    catch (e) { toast(e.message, 'bad'); }
  });
  $id('stRevokeAll').addEventListener('click', async () => {
    if (!confirm('Log out every other staff login (other phones and computers)? You stay logged in.')) return;
    try { const r = await jpost('/api/admin/sessions/revoke-all', {}); toast('Logged out ' + r.ended + ' other session(s)', 'ok'); }
    catch (e) { toast(e.message, 'bad'); }
  });
  async function loadBackup() {
    try {
      const b = await api('/api/admin/backup');
      $id('backupBox').innerHTML = b.last ? 'Latest backup: <b>' + esc((b.last_at || '').replace('T', ' ').slice(0, 16)) + '</b> (' + b.count + ' kept' + (b.extra_dir ? ', also copied to ' + esc(b.extra_dir) : '') + ')' : '<span class="muted">No backup yet - one is made automatically every day.</span>';
    } catch (e) { /* ignore */ }
  }
  $id('backupNow').addEventListener('click', async () => { try { await jpost('/api/admin/backup', {}); toast('Backup made', 'ok'); loadBackup(); } catch (e) { toast(e.message, 'bad'); } });
  $id('reindexBtn').addEventListener('click', async () => { try { const r = await jpost('/api/admin/reindex', {}); toast('Found ' + r.added + ' new order(s)', 'ok'); loadOrders(); } catch (e) { toast(e.message, 'bad'); } });
  $id('posterBtn').addEventListener('click', () => {
    const url = location.origin + '/';
    $id('pTitle').textContent = document.title && 'Print Portal'; $id('pWifi').textContent = $id('wifiName').value || '(ask at the counter)';
    $id('pUrl').textContent = url; $id('pQr').style.display = ''; $id('pQr').onerror = () => { $id('pQr').style.display = 'none'; }; $id('pQr').src = '/api/admin/qr.svg?text=' + encodeURIComponent(url);
    fetch('/api/config').then(r => r.json()).then(c => { $id('pTitle').textContent = c.shop.name; $id('pPublic').textContent = c.shop.public_url || ''; $id('pPublicWrap').classList.toggle('hidden', !c.shop.public_url); }).catch(() => {}).finally(() => {
      document.body.classList.add('printing-poster'); setTimeout(() => { window.print(); setTimeout(() => document.body.classList.remove('printing-poster'), 500); }, 300);
    });
  });

  // ---------- init
  A.date = today(); $id('fDate').value = A.date; $id('sFrom').value = A.date; $id('sTo').value = A.date;
  api('/api/admin/me').then(m => { if (m.ok) showApp(); else showLogin(); }).catch(showLogin);
})();
