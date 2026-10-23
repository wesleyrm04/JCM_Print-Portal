/* Shared helpers. No external libraries; everything works offline. */
(function (w) {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const PESO = '\u20B1';

  function peso(n) {
    n = Number(n) || 0;
    return PESO + n.toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function fmtBytes(b) {
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(0) + ' KB';
    return (b / 1048576).toFixed(1) + ' MB';
  }
  // localStorage can throw (private mode, disabled) - never let it break the page
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
  };

  let _cfg = null;
  async function getConfig() {
    if (_cfg) return _cfg;
    try {
      const r = await fetch('/api/config', { cache: 'no-store' });
      if (!r.ok) throw new Error('config');
      _cfg = await r.json();
      store.set('jcm_cfg', _cfg);
    } catch (e) {
      _cfg = store.get('jcm_cfg', null);
      if (!_cfg) throw e;
    }
    return _cfg;
  }

  function toast(msg, kind, ms) {
    let box = $('#toasts');
    if (!box) { box = document.createElement('div'); box.id = 'toasts'; box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite'); document.body.appendChild(box); }
    const t = document.createElement('div');
    t.className = 'toast ' + (kind || '');
    t.textContent = msg;
    box.appendChild(t);
    setTimeout(() => t.remove(), ms || 4500);
  }

  function openDialog(d) { if (d.showModal) { if (!d.open) d.showModal(); } else d.setAttribute('open', ''); }
  function closeDialog(d) { if (d.close) d.close(); else d.removeAttribute('open'); }

  // Upload with progress. Resolves {status, json}; status 0 = network failure.
  function upload(url, formData, onProgress) {
    return new Promise(resolve => {
      const x = new XMLHttpRequest();
      x.open('POST', url);
      x.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      x.onload = () => { let j = null; try { j = JSON.parse(x.responseText); } catch (e) { /* not json */ } resolve({ status: x.status, json: j }); };
      x.onerror = () => resolve({ status: 0, json: null });
      x.ontimeout = () => resolve({ status: 0, json: null });
      x.timeout = 20 * 60 * 1000;
      x.send(formData);
    });
  }
  async function uploadWithRetry(url, buildForm, onProgress, onRetry) {
    let res;
    for (let attempt = 0; attempt < 3; attempt++) {
      res = await upload(url, buildForm(), onProgress);
      if (res.status !== 0) return res;
      if (attempt < 2) { if (onRetry) onRetry(attempt + 1); await new Promise(r => setTimeout(r, 2000)); }
    }
    return res;
  }

  // Remember orders made on this phone so the customer can check them later
  function rememberOrder(o) {
    const list = store.get('jcm_orders', []).filter(x => x.id !== o.id);
    list.unshift({ id: o.id, code: o.code, at: Date.now(), what: o.what || '' });
    store.set('jcm_orders', list.slice(0, 10));
  }

  function fillShop(cfg) {
    const s = cfg.shop;
    $$('[data-shop-name]').forEach(e => e.textContent = s.name);
    $$('[data-shop-short]').forEach(e => e.textContent = s.short_name);
    $$('[data-shop-tagline]').forEach(e => e.textContent = s.tagline);
    $$('[data-shop-hours]').forEach(e => { e.textContent = s.hours; e.closest('[data-if-hours]') && (e.closest('[data-if-hours]').hidden = !s.hours); });
    $$('[data-shop-phone]').forEach(e => { if (s.phone) { e.textContent = s.phone; e.href = 'tel:' + s.phone.replace(/[^0-9+]/g, ''); } else (e.closest('[data-if-phone]') || e).hidden = true; });
    $$('[data-shop-address]').forEach(e => { e.textContent = s.address; (e.closest('[data-if-address]') || e).hidden = !s.address; });
    $$('[data-shop-fb]').forEach(e => { if (s.facebook) e.href = s.facebook; else e.hidden = true; });
    $$('[data-shop-email]').forEach(e => { if (s.email) { e.href = 'mailto:' + s.email; e.textContent = s.email; } else (e.closest('[data-if-email]') || e).hidden = true; });
  }

  w.$ = $; w.$$ = $$; w.peso = peso; w.esc = esc; w.fmtBytes = fmtBytes; w.store = store;
  w.getConfig = getConfig; w.toast = toast; w.openDialog = openDialog; w.closeDialog = closeDialog;
  w.uploadWithRetry = uploadWithRetry; w.rememberOrder = rememberOrder; w.fillShop = fillShop;
})(window);
