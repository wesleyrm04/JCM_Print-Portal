(async function () {
  'use strict';
  const T = (k, v) => I18N.t(k, v), $id = id => document.getElementById(id);
  try { fillShop(await getConfig()); } catch (e) { /* ignore */ }
  let timer = null, cur = null;

  function stepLabel(s) { const k = 'status.' + s; const v = T(k); return v === k ? s : v; }
  async function check(id, code, quiet) {
    $id('lookErr').classList.add('hidden');
    try {
      const r = await fetch('/api/status?id=' + encodeURIComponent(id) + '&code=' + encodeURIComponent(code), { cache: 'no-store' });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || T('st.notfound'));
      cur = { id, code, j };
      show(j);
      clearInterval(timer); timer = setInterval(() => { if (!document.hidden) check(id, code, true); }, 15000);
    } catch (e) {
      if (!quiet) { $id('lookErr').textContent = e.message || T('st.notfound'); $id('lookErr').classList.remove('hidden'); $id('out').classList.add('hidden'); }
    }
  }
  function show(j) {
    $id('out').classList.remove('hidden');
    $id('oId').textContent = j.id;
    $id('oWhat').textContent = j.preset + (j.first_name ? ' - ' + j.first_name : '');
    const cancelled = j.status === 'Cancelled';
    $id('oBadge').textContent = stepLabel(j.status);
    $id('oBadge').className = 'badge ' + (cancelled ? 'bad' : j.status === 'Ready for Pickup' ? 'ok' : j.status === 'Completed' ? 'ok' : '');
    $id('oSteps').innerHTML = cancelled ? '' : j.steps.map((s, i) => '<li class="' + (i < j.step ? 'done' : i === j.step ? 'now' : '') + '">' + esc(stepLabel(s)) + '</li>').join('');
    const n = $id('oNote'); n.textContent = j.public_note || ''; n.classList.toggle('hidden', !j.public_note);
    $id('oPay').innerHTML = j.total > 0 ? '<b>' + esc(peso(j.total)) + '</b> &middot; ' + esc(j.paid ? T('st.paid') : T('st.unpaid')) : '';
    const d = new Date(j.updated);
    $id('oUpd').textContent = T('st.updated', { t: isNaN(d) ? '' : d.toLocaleString() });
  }
  function recent() {
    const list = store.get('jcm_orders', []);
    $id('recentBox').hidden = !list.length;
    $id('recentBtns').innerHTML = list.map(o => '<button type="button" class="chip" data-id="' + esc(o.id) + '" data-code="' + esc(o.code) + '">' + esc(o.id) + '</button>').join('');
  }
  $id('recentBtns').addEventListener('click', e => { const b = e.target.closest('[data-id]'); if (b) { $id('oid').value = b.dataset.id; $id('ocode').value = b.dataset.code; check(b.dataset.id, b.dataset.code); } });
  $id('lookup').addEventListener('submit', e => { e.preventDefault(); const id = $id('oid').value.trim().toUpperCase(), c = $id('ocode').value.trim().toUpperCase(); if (id && c) check(id, c); });
  $id('langBtn').addEventListener('click', () => I18N.toggle());
  I18N.onChange(() => { recent(); if (cur) show(cur.j); });
  const q = new URLSearchParams(location.search);
  if (q.get('o') && q.get('c')) { $id('oid').value = q.get('o'); $id('ocode').value = q.get('c'); check(q.get('o').toUpperCase(), q.get('c').toUpperCase()); }
  I18N.apply(); recent();
})();
