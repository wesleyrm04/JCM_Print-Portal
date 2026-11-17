/* Customer pages: shows the souvenirs gallery and the repair price list from /api/catalog (edited by staff in the
   dashboard). The original markup stays in the page as a fallback and is only replaced when the catalog loads. */
(async function () {
  'use strict';
  const sv = document.getElementById('catSouv'), rp = document.getElementById('catRepair');
  if (!sv && !rp) return;
  let data;
  try {
    const r = await fetch('/api/catalog', { cache: 'no-cache' });
    if (!r.ok) throw new Error('catalog');
    data = await r.json();
  } catch (e) { return; }
  const thumb = u => u.indexOf('/media/') === 0 ? u.replace('.jpg', '_t.jpg') : u;
  const T = (k, d) => { try { const v = I18N.t(k); return v === k ? d : v; } catch (e) { return d; } };

  function setOptions(sel, list) {
    if (!sel || !list || !list.length) return;
    const keep = sel.value;
    sel.innerHTML = list.map(o => '<option>' + esc(o) + '</option>').join('');
    if (list.indexOf(keep) >= 0) sel.value = keep;
  }

  // ---------- picture viewer (all pictures of one product)
  let box = null, cur = { imgs: [], i: 0, name: '' };
  function viewer() {
    if (box) return box;
    box = document.createElement('div');
    box.className = 'lightbox hidden'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
    box.innerHTML = '<button type="button" class="lb-x" aria-label="Close">&times;</button><button type="button" class="lb-n lb-prev" aria-label="Previous">&#8249;</button>' +
      '<img alt=""><button type="button" class="lb-n lb-next" aria-label="Next">&#8250;</button><div class="lb-cap"></div>';
    document.body.appendChild(box);
    box.addEventListener('click', e => {
      if (e.target.closest('.lb-prev')) show(cur.i - 1);
      else if (e.target.closest('.lb-next')) show(cur.i + 1);
      else if (e.target.closest('.lb-x') || e.target === box) close();
    });
    document.addEventListener('keydown', e => {
      if (box.classList.contains('hidden')) return;
      if (e.key === 'Escape') close(); else if (e.key === 'ArrowLeft') show(cur.i - 1); else if (e.key === 'ArrowRight') show(cur.i + 1);
    });
    return box;
  }
  function show(i) {
    const n = cur.imgs.length; cur.i = (i + n) % n;
    const b = viewer(); b.querySelector('img').src = cur.imgs[cur.i]; b.querySelector('img').alt = cur.name;
    b.querySelector('.lb-cap').textContent = cur.name + (n > 1 ? '  (' + (cur.i + 1) + '/' + n + ')' : '');
    b.querySelectorAll('.lb-n').forEach(x => x.classList.toggle('hidden', n < 2));
  }
  function close() { viewer().classList.add('hidden'); document.body.style.overflow = ''; }
  function open(imgs, name) {
    if (!imgs.length) return;
    cur = { imgs, i: 0, name }; viewer().classList.remove('hidden'); document.body.style.overflow = 'hidden'; show(0);
  }

  // ---------- souvenirs
  if (sv && data.souvenirs) {
    const cats = data.souvenirs.categories || [];
    sv.innerHTML = cats.length ? cats.map((c, ci) => '<section class="card"><h2>' + esc(c.title) + '</h2><div class="gallery">' +
      c.items.map((it, ii) => {
        const first = it.images[0] ? thumb(it.images[0]) : '';
        const more = it.images.length > 1 ? '<span class="more">+' + (it.images.length - 1) + '</span>' : '';
        const out = it.in_stock ? '' : '<span class="oos">' + esc(T('sv.out', 'Out of stock')) + '</span>';
        return '<figure class="' + (it.in_stock ? '' : 'soldout') + '">' +
          (first ? '<button type="button" class="pic" data-ci="' + ci + '" data-ii="' + ii + '" aria-label="' + esc(it.name) + '"><img src="' + esc(first) + '" alt="' + esc(it.name) + '" loading="lazy" width="300" height="300">' + more + out + '</button>'
                 : '<div class="pic noimg">' + out + '</div>') +
          '<figcaption>' + esc(it.name) + (it.price ? '<br><b>' + esc(it.price) + '</b>' : '') + '</figcaption></figure>';
      }).join('') + '</div></section>').join('')
      : '<section class="card"><p class="muted">' + esc(T('sv.none', 'Message us on Facebook to see what we can make for you.')) + '</p></section>';
    sv.addEventListener('click', e => {
      const b = e.target.closest('.pic[data-ci]'); if (!b) return;
      const it = cats[+b.dataset.ci].items[+b.dataset.ii];
      open(it.images, it.name);
    });
    setOptions(document.getElementById('d_item'), data.souvenirs.order_options);
  }

  // ---------- repair services
  if (rp && data.repair) {
    const secs = data.repair.sections || [];
    rp.innerHTML = secs.length ? secs.map(s => '<section class="card"><h2>' + (s.icon ? esc(s.icon) + ' ' : '') + esc(s.title) + '</h2><div class="tablewrap"><table>' +
      '<thead><tr><th data-i18n="rp.col.service">Service</th><th data-i18n="rp.col.price">Estimated Price</th><th data-i18n="rp.col.notes">Notes / Details</th></tr></thead><tbody>' +
      s.services.map(v => '<tr><td>' + esc(v.name) + '</td><td>' + esc(v.price) + '</td><td>' + esc(v.notes) + '</td></tr>').join('') +
      '</tbody></table></div></section>').join('')
      : '<section class="card"><p class="muted">' + esc(T('rp.none', 'Message us to ask about repairs.')) + '</p></section>';
    setOptions(document.getElementById('d_device_type'), data.repair.device_types);
  }
  try { I18N.apply(); } catch (e) { /* translations are optional */ }
})();
