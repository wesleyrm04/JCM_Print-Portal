/* "Send from Facebook" page.
   Opened by the phone's Share button (Android, after the app is installed) or by pasting a link.
   Shared files arrive through the service worker (static/sw.js), which keeps them in Cache Storage. */
(async function () {
  'use strict';
  const T = (k, v) => I18N.t(k, v), $id = id => document.getElementById(id);
  let cfg = null;
  try { cfg = await getConfig(); fillShop(cfg); } catch (e) { /* page still works without the config */ }
  const maxMb = cfg ? cfg.limits.max_file_mb : 50;
  const maxFiles = cfg ? cfg.limits.max_files : 50;
  const maxLinks = cfg && cfg.share ? cfg.share.max_links : 10;
  const SHARE_CACHE = 'jcm-share-v1';
  const OK_EXT = ['pdf', 'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tif', 'tiff', 'heic', 'heif', 'doc', 'docx', 'xls', 'xlsx',
    'ppt', 'pptx', 'odt', 'ods', 'odp', 'rtf', 'txt', 'csv'];
  const MIME_EXT = { 'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif',
    'image/heic': 'heic', 'image/heif': 'heif', 'image/bmp': 'bmp', 'image/tiff': 'tif', 'application/pdf': 'pdf',
    'text/plain': 'txt', 'text/csv': 'csv', 'application/rtf': 'rtf', 'application/msword': 'doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.ms-excel': 'xls', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.ms-powerpoint': 'ppt', 'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx' };
  const SERVICES = (cfg && cfg.share && cfg.share.services) || [];
  let files = [];                 // [{ file, url }]  url = preview object URL (images only)
  let busy = false;

  // ---------- links (same rule as portal/sharing.py; the server checks again)
  function extractLinks(text) {
    const out = [], seen = new Set();
    const re = /https?:\/\/[^\s<>"'`\u200b]+/gi;
    let m;
    while ((m = re.exec(text || '')) !== null) {
      const u = m[0].replace(/[.,;:!?)\]}'"\u2019\u201d]+$/, '');
      if (u.length > 500 || seen.has(u.toLowerCase())) continue;
      try { new URL(u); } catch (e) { continue; }
      seen.add(u.toLowerCase()); out.push(u);
      if (out.length >= maxLinks) break;
    }
    return out;
  }
  const hostOf = u => { try { return new URL(u).hostname.toLowerCase(); } catch (e) { return ''; } };
  const labelOf = u => { const h = hostOf(u); const s = SERVICES.find(x => x.hosts.some(d => h === d || h.endsWith('.' + d))); return s ? s.label : ''; };

  function refreshLinks() {
    const links = extractLinks($id('shText').value);
    $id('linkCount').textContent = links.length ? T('sh.found', { n: links.length }) : T('sh.none');
    $id('linkList').innerHTML = links.map(u =>
      '<li><div class="meta"><div class="nm">' + esc(hostOf(u).replace(/^www\./, '')) + '</div><div class="sub" style="word-break:break-all">' + esc(u.length > 90 ? u.slice(0, 90) + '...' : u) + '</div></div>' +
      '<span class="badge ' + (labelOf(u) ? 'ok' : 'warn') + '">' + esc(labelOf(u) || T('sh.other')) + '</span></li>').join('');
    return links;
  }

  // ---------- files
  const extOf = n => { const i = (n || '').lastIndexOf('.'); return i < 0 ? '' : n.slice(i + 1).toLowerCase(); };
  function fixFile(f) {
    if (OK_EXT.indexOf(extOf(f.name)) >= 0) return f;
    const e = MIME_EXT[(f.type || '').toLowerCase()];
    if (!e) return null;
    const base = (f.name || 'shared').replace(/\.[^.]*$/, '') || 'shared';
    return new File([f], base + '.' + e, { type: f.type });
  }
  function addFiles(list) {
    const bad = [];
    Array.from(list).forEach(f0 => {
      const f = fixFile(f0);
      if (!f || f.size === 0 || f.size > maxMb * 1048576) { bad.push(f0.name || 'file'); return; }
      if (files.some(x => x.file.name === f.name && x.file.size === f.size)) return;
      if (files.length >= maxFiles) { bad.push(f.name); return; }
      const img = /^image\//.test(f.type) && !/hei[cf]$/.test(extOf(f.name));
      files.push({ file: f, url: img ? URL.createObjectURL(f) : '' });
    });
    const m = $id('fileMsg');
    m.textContent = bad.length ? T('sh.skipped', { names: bad.join(', ') }) : '';
    m.classList.toggle('hidden', !bad.length);
    renderFiles();
  }
  function renderFiles() {
    $id('sharedFilesBox').classList.toggle('hidden', !files.length);
    $id('fileList').innerHTML = files.map((x, i) =>
      '<li>' + (x.url ? '<img class="thumb" alt="" src="' + x.url + '">' : '<span class="thumb" aria-hidden="true" style="display:flex;align-items:center;justify-content:center">&#128196;</span>') +
      '<div class="meta"><div class="nm">' + esc(x.file.name) + '</div><div class="sub">' + esc(fmtBytes(x.file.size)) + '</div></div>' +
      '<button type="button" class="x" data-rm="' + i + '" aria-label="Remove">&times;</button></li>').join('');
  }
  $id('fileList').addEventListener('click', e => {
    const b = e.target.closest('[data-rm]'); if (!b) return;
    const x = files.splice(+b.dataset.rm, 1)[0]; if (x && x.url) URL.revokeObjectURL(x.url);
    renderFiles();
  });
  $id('fileInput').addEventListener('change', e => { addFiles(e.target.files); e.target.value = ''; });

  // ---------- what the Share button sent
  async function loadShared() {
    if (!('caches' in window)) return null;
    try {
      const c = await caches.open(SHARE_CACHE);
      const m = await c.match('/__share__/meta');
      if (!m) return null;
      const meta = await m.json(), out = [];
      for (const f of meta.files || []) {
        const r = await c.match(f.key);
        if (r) out.push(new File([await r.blob()], f.name, { type: f.type || '' }));
      }
      return { meta, files: out };
    } catch (e) { return null; }
  }
  async function clearShared() { try { if ('caches' in window) await caches.delete(SHARE_CACHE); } catch (e) { /* ignore */ } }
  function composeText(title, text, url) {
    const parts = [];
    [text, url].forEach(p => { p = (p || '').trim(); if (p && !parts.some(x => x.indexOf(p) >= 0)) parts.push(p); });
    const t = (title || '').trim();
    if (t && !extractLinks(parts.join('\n')).length && parts.indexOf(t) < 0) parts.unshift(t);
    return parts.join('\n');
  }

  const q = new URLSearchParams(location.search);
  if (q.get('received')) {
    const got = await loadShared();
    if (got) {
      $id('shText').value = composeText(got.meta.title, got.meta.text, got.meta.url);
      if (got.files.length) addFiles(got.files);
      $id('discardBtn').classList.remove('hidden');
    }
  } else {
    clearShared();                                                  // do not keep an old share around
    if (q.get('text') || q.get('url') || q.get('title')) {          // link opened with the share text in the address
      $id('shText').value = composeText(q.get('title'), q.get('text'), q.get('url'));
      $id('discardBtn').classList.remove('hidden');
    }
  }
  const me = store.get('jcm_me', {});
  $id('custName').value = me.name || ''; $id('custPhone').value = me.phone || '';
  refreshLinks();

  $id('shText').addEventListener('input', refreshLinks);
  $id('pasteBtn').addEventListener('click', async () => {
    try {
      const t = (await navigator.clipboard.readText() || '').trim();
      if (!t) throw new Error('empty');
      const box = $id('shText'), cur = box.value.trim();
      if (cur.indexOf(t) < 0) box.value = cur ? cur + '\n' + t : t;
      refreshLinks();
    } catch (e) { toast(T('sh.pasteFail'), 'bad', 6000); $id('shText').focus(); }
  });
  $id('discardBtn').addEventListener('click', async () => {
    await clearShared(); $id('shText').value = ''; files.forEach(x => x.url && URL.revokeObjectURL(x.url)); files = [];
    renderFiles(); refreshLinks(); $id('discardBtn').classList.add('hidden');
  });

  // ---------- send
  function showErr(m) { const b = $id('formErr'); b.textContent = m; b.classList.remove('hidden'); b.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
  $id('shareForm').addEventListener('submit', async e => {
    e.preventDefault();
    if (busy) return;
    $id('formErr').classList.add('hidden');
    const name = $id('custName').value.trim(), phone = $id('custPhone').value.trim(), text = $id('shText').value.trim();
    const links = extractLinks(text);
    if (!links.length && !files.length) { showErr(T('sh.empty')); $id('shText').focus(); return; }
    if (!name) { showErr(T('err.name')); $id('custName').focus(); return; }
    const build = () => {
      const fd = new FormData();
      fd.append('customer_name', name); fd.append('phone', phone); fd.append('instructions', $id('notes').value.trim());
      fd.append('shared_text', text.slice(0, 5000));
      fd.append('order_meta', JSON.stringify({ kind: 'share', links }));
      files.forEach(x => fd.append('files', x.file, x.file.name));
      return fd;
    };
    busy = true; $id('submitBtn').disabled = true; $id('upProgress').classList.remove('hidden');
    $id('upText').textContent = T('up.sending', { p: 0 });
    const res = await uploadWithRetry('/api/submit_order', build,
      p => { const v = Math.round(p * 100); $id('upBar').style.width = v + '%'; $id('upText').textContent = T('up.sending', { p: v }); },
      n => { $id('upText').textContent = T('up.retry', { n }); });
    busy = false; $id('submitBtn').disabled = false; $id('upProgress').classList.add('hidden');
    if (res.status === 200 && res.json && res.json.order_id) {
      const code = res.json.access_code;
      store.set('jcm_me', { name, phone });
      rememberOrder({ id: res.json.order_id, code, what: T('sh.what') });
      await clearShared();
      $id('shareForm').classList.add('hidden'); $id('introCard').classList.add('hidden'); $id('result').classList.remove('hidden');
      $id('resCode').textContent = code;
      $id('resStatus').href = '/status?c=' + encodeURIComponent(code);
      if (navigator.share) {
        $id('resShare').classList.remove('hidden');
        $id('resShare').onclick = () => navigator.share({ text: T('sh.sharetext', { code }) }).catch(() => { /* cancelled */ });
      }
      window.scrollTo(0, 0);
    } else {
      showErr((res.json && res.json.detail) || (res.status === 0 ? T('err.network') : T('err.generic')));
    }
  });
  $id('againBtn').addEventListener('click', () => {
    files.forEach(x => x.url && URL.revokeObjectURL(x.url)); files = [];
    $id('shText').value = ''; $id('notes').value = ''; renderFiles(); refreshLinks();
    $id('discardBtn').classList.add('hidden');
    $id('result').classList.add('hidden'); $id('shareForm').classList.remove('hidden'); $id('introCard').classList.remove('hidden');
    window.scrollTo(0, 0);
  });

  $id('langBtn').addEventListener('click', () => I18N.toggle());
  I18N.onChange(refreshLinks);
  I18N.apply();
})();
