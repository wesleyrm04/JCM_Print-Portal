/* Repair / souvenir request form (works offline, sends to the shop like a print order). */
(async function () {
  'use strict';
  const T = (k, v) => I18N.t(k, v), $id = id => document.getElementById(id);
  let cfg = null;
  try { cfg = await getConfig(); fillShop(cfg); } catch (e) { /* ignore */ }
  const form = $id('ticketForm'), kind = form.dataset.kind;
  const IMG = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'heic', 'heif'];
  const maxMb = cfg ? cfg.limits.max_file_mb : 50;
  let files = [];
  const me = store.get('jcm_me', {});
  $id('custName').value = me.name || ''; $id('custPhone').value = me.phone || '';

  function showErr(m) { const b = $id('formErr'); b.textContent = m; b.classList.remove('hidden'); b.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
  $id('refImages').addEventListener('change', e => {
    const bad = [];
    files = Array.from(e.target.files).filter(f => {
      const ext = f.name.split('.').pop().toLowerCase();
      if (IMG.indexOf(ext) < 0 || f.size === 0 || f.size > maxMb * 1048576) { bad.push(f.name); return false; }
      return true;
    }).slice(0, 5);
    $id('refNames').textContent = files.map(f => f.name).join(', ') + (bad.length ? ' | ' + T('tk.skipped', { names: bad.join(', ') }) : '');
  });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    $id('formErr').classList.add('hidden');
    const name = $id('custName').value.trim(), phone = $id('custPhone').value.trim();
    if (!name) { showErr(T('err.name')); $id('custName').focus(); return; }
    if (phone.replace(/\D/g, '').length < 7) { showErr(T('err.phone')); $id('custPhone').focus(); return; }
    const details = {};
    form.querySelectorAll('[data-detail]').forEach(el => { details[el.dataset.detail] = el.value.trim(); });
    if (kind === 'repair' && !details.issue) { showErr(T('err.issue')); $id('d_issue').focus(); return; }
    if (kind === 'souvenir' && !details.theme && !details.quantity) { showErr(T('err.souv')); return; }
    const meta = { kind, details };
    const build = () => {
      const fd = new FormData();
      fd.append('customer_name', name); fd.append('phone', phone); fd.append('instructions', $id('notes').value.trim());
      fd.append('order_meta', JSON.stringify(meta));
      files.forEach(f => fd.append('files', f, f.name));
      return fd;
    };
    $id('submitBtn').disabled = true; $id('upProgress').classList.remove('hidden');
    const res = await uploadWithRetry('/api/submit_order', build, p => { $id('upBar').style.width = Math.round(p * 100) + '%'; });
    $id('submitBtn').disabled = false; $id('upProgress').classList.add('hidden');
    if (res.status === 200 && res.json && res.json.order_id) {
      store.set('jcm_me', { name, phone });
      rememberOrder({ id: res.json.order_id, code: res.json.access_code, what: kind === 'repair' ? T('rp.title') : T('sv.title') });
      form.classList.add('hidden'); $id('result').classList.remove('hidden');
      $id('resId').textContent = res.json.order_id; $id('resCode').textContent = res.json.access_code;
      $id('resStatus').href = '/status?o=' + encodeURIComponent(res.json.order_id) + '&c=' + encodeURIComponent(res.json.access_code);
      window.scrollTo(0, 0);
    } else showErr((res.json && res.json.detail) || (res.status === 0 ? T('err.network') : T('err.generic')));
  });
  $id('langBtn').addEventListener('click', () => I18N.toggle());
  I18N.apply();
})();
