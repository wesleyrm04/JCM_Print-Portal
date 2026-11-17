(async function () {
  'use strict';
  const T = (k, v) => I18N.t(k, v), $id = id => document.getElementById(id);
  try { fillShop(await getConfig()); } catch (e) { /* ignore */ }
  let timer = null, cur = null;      // cur = { q, data }

  function stepLabel(s) { const k = 'status.' + s; const v = T(k); return v === k ? s : v; }

  // One order card. A "limited" card (found by name) shows only the status - no price, no staff message.
  function card(j) {
    const cancelled = j.status === 'Cancelled';
    const cls = cancelled ? 'bad' : (j.status === 'Ready for Pickup' || j.status === 'Completed') ? 'ok' : '';
    const steps = cancelled ? '' : '<ol class="steps">' + j.steps.map((s, i) => '<li class="' + (i < j.step ? 'done' : i === j.step ? 'now' : '') + '">' + esc(stepLabel(s)) + '</li>').join('') + '</ol>';
    const ahead = j.ahead > 0 ? '<div class="alert info">' + esc(T('st.ahead', { n: j.ahead })) + '</div>' : '';
    const note = !j.limited && j.public_note ? '<div class="alert info">' + esc(j.public_note) + '</div>' : '';
    const pay = !j.limited && j.total > 0 ? '<div><b>' + esc(peso(j.total)) + '</b> &middot; ' + esc(j.paid ? T('st.paid') : T('st.unpaid')) + '</div>' : '';
    const hint = j.limited ? '<p class="small muted">' + esc(T('st.limited')) + '</p>' : '';
    const d = new Date(j.updated);
    return '<section class="card">' +
      '<div class="row spread"><div style="font-size:1.25rem;font-weight:800;word-break:break-word">' + esc(j.first_name || '') + '</div><span class="badge ' + cls + '">' + esc(stepLabel(j.status)) + '</span></div>' +
      '<p class="muted">' + esc(j.preset) + '</p>' + steps + ahead + note + pay + hint +
      '<p class="small muted">' + esc(T('st.updated', { t: isNaN(d) ? '' : d.toLocaleString() })) + '</p></section>';
  }

  function render(data) {
    const res = data.results || [];
    $id('results').innerHTML = res.map(card).join('');
    const n = $id('resNote');
    if (data.mode === 'name' && res.length) { n.textContent = T('st.matches', { n: res.length }); n.classList.remove('hidden'); }
    else n.classList.add('hidden');
  }

  async function search(q, quiet) {
    $id('lookErr').classList.add('hidden');
    try {
      const r = await fetch('/api/status/find?q=' + encodeURIComponent(q), { cache: 'no-store' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || T('st.nomatch'));
      if (!j.results || !j.results.length) {
        cur = null; clearInterval(timer);
        if (!quiet) { $id('lookErr').textContent = j.too_short ? T('st.tooshort') : T('st.nomatch'); $id('lookErr').classList.remove('hidden'); $id('results').innerHTML = ''; $id('resNote').classList.add('hidden'); }
        return;
      }
      cur = { q, data: j };
      render(j);
      clearInterval(timer); timer = setInterval(() => { if (!document.hidden) search(q, true); }, 15000);
    } catch (e) {
      if (!quiet) { $id('lookErr').textContent = e.message || T('st.nomatch'); $id('lookErr').classList.remove('hidden'); $id('results').innerHTML = ''; $id('resNote').classList.add('hidden'); }
    }
  }

  function recent() {
    const list = store.get('jcm_orders', []);
    $id('recentBox').hidden = !list.length;
    $id('recentBtns').innerHTML = list.map(o => '<button type="button" class="chip" data-code="' + esc(o.code) + '">' + esc(o.what || o.code) + ' &middot; ' + esc(o.code) + '</button>').join('');
  }
  $id('recentBtns').addEventListener('click', e => { const b = e.target.closest('[data-code]'); if (b) { $id('oq').value = b.dataset.code; search(b.dataset.code); } });
  $id('lookup').addEventListener('submit', e => { e.preventDefault(); const v = $id('oq').value.trim(); if (v) search(v); });
  $id('langBtn').addEventListener('click', () => I18N.toggle());
  I18N.onChange(() => { recent(); if (cur) render(cur.data); });

  // links: /status?c=CODE  (older links /status?o=NUMBER&c=CODE still work - the number is simply ignored)  or  /status?q=NAME
  const p = new URLSearchParams(location.search);
  const first = (p.get('c') || p.get('q') || '').trim();
  if (first) { $id('oq').value = first; search(first); }
  I18N.apply(); recent();
})();
