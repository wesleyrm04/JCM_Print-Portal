/* Staff dashboard > Catalog tab: edit the Souvenirs gallery and the Tech-Repair price list that customers see.
   Nothing changes for customers until "Save changes" is pressed. */
(function () {
  'use strict';
  const $id = id => document.getElementById(id);
  let C = null, dirty = false, panel = 'souv', busy = 0;
  const rid = p => p + Math.random().toString(36).slice(2, 8);
  const thumb = u => u.indexOf('/media/') === 0 ? u.replace('.jpg', '_t.jpg') : u;

  async function api(path, opt) {
    const r = await fetch(path, Object.assign({ credentials: 'same-origin', cache: 'no-store' }, opt || {}));
    if (r.status === 401) { location.reload(); throw new Error('Please log in again.'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || ('Error ' + r.status));
    return j;
  }
  function status(msg, bad) { const s = $id('catStatus'); s.textContent = msg || ''; s.style.color = bad ? '#b42318' : ''; }
  function setDirty(v) {
    dirty = v; $id('catSave').disabled = !v || busy > 0; $id('catDiscard').disabled = !v;
    if (v) status(busy ? 'Uploading...' : 'Unsaved changes'); else if (!busy) status('');
  }
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

  // ------------------------------------------------------------ souvenirs
  function renderSouv() {
    const S = C.souvenirs;
    let h = '<h2>Souvenirs gallery</h2>' +
      '<p class="small muted">Add products and pictures customers can check. Untick <b>In stock</b> to take a product off the customer page, or use the red button to delete it for good.</p>' +
      '<label class="check"><input type="checkbox" data-f="showout"' + (S.show_out_of_stock ? ' checked' : '') + '><span>Still show out-of-stock products to customers (greyed out, marked &ldquo;Out of stock&rdquo;). Off = they are hidden.</span></label>';
    h += S.categories.map((c, ci) => {
      const n = c.items.filter(i => i.in_stock).length;
      return '<div class="cat-box' + (c.enabled ? '' : ' cat-off') + '" data-ci="' + ci + '">' +
        '<div class="cat-row"><input type="text" data-f="ctitle" maxlength="80" value="' + esc(c.title) + '" aria-label="Category name">' +
        '<label class="check" style="margin:0"><input type="checkbox" data-f="cenabled"' + (c.enabled ? ' checked' : '') + '><span>Show</span></label>' +
        '<button type="button" class="btn small ghost" data-act="cup" title="Move up">&uarr;</button><button type="button" class="btn small ghost" data-act="cdown" title="Move down">&darr;</button>' +
        '<button type="button" class="btn small outline" data-act="cdel" style="color:#b42318">Delete category</button></div>' +
        '<div class="small muted">' + n + ' of ' + c.items.length + ' products in stock</div>' +
        c.items.map((it, ii) => '<div class="cat-item" data-ii="' + ii + '">' +
          '<div class="cat-imgs">' + it.images.map((u, ji) => '<div class="cat-im' + (ji === 0 ? ' cover' : '') + '" data-ji="' + ji + '"><img src="' + esc(thumb(u)) + '" alt="">' +
            (ji > 0 ? '<button type="button" class="cv" data-act="imgcover" title="Make this the first picture">&#9733;</button>' : '') +
            '<button type="button" data-act="imgdel" title="Remove picture">&times;</button></div>').join('') +
            '<label class="btn small outline" style="align-self:center;cursor:pointer">+ Add pictures<input type="file" class="sr-only" data-act="imgadd" accept="image/*" multiple></label></div>' +
          '<div class="cat-row"><input type="text" data-f="iname" maxlength="80" value="' + esc(it.name) + '" aria-label="Product name" placeholder="Product name">' +
          '<input type="text" data-f="iprice" maxlength="60" value="' + esc(it.price) + '" aria-label="Price note" placeholder="Price note (optional), e.g. from ₱35">' +
          '<label class="check" style="margin:0"><input type="checkbox" data-f="istock"' + (it.in_stock ? ' checked' : '') + '><span>In stock</span></label>' +
          '<button type="button" class="btn small ghost" data-act="iup" title="Move up">&uarr;</button><button type="button" class="btn small ghost" data-act="idown" title="Move down">&darr;</button>' +
          '<button type="button" class="btn small outline" data-act="idel" style="color:#b42318">Delete</button></div>' +
          (it.images.length ? '' : '<div class="small muted">No picture yet - customers will see the name only.</div>') + '</div>').join('') +
        '<button type="button" class="btn small outline" data-act="iadd" style="margin-top:8px">+ Add product</button></div>';
    }).join('');
    h += '<button type="button" class="btn small" data-act="cadd">+ Add category</button>' +
      '<h3 style="margin-top:16px">Choices in the customer order form</h3><p class="small muted">The &ldquo;What do you want?&rdquo; list on the souvenirs page, one per line.</p>' +
      '<textarea data-f="sopts" rows="6" maxlength="1500">' + esc(S.order_options.join('\n')) + '</textarea>';
    $id('catPanelSouv').innerHTML = h;
  }

  // ------------------------------------------------------------ repair
  function renderRepair() {
    const R = C.repair;
    let h = '<h2>Tech repair services</h2>' +
      '<p class="small muted">Add or remove the services customers see. Untick <b>Offered</b> to hide one service for now; untick <b>Show</b> to hide a whole section.</p>';
    h += R.sections.map((s, si) => {
      const hint = s.id === 'mobile' && !s.enabled ? '<div class="alert info small">This Mobile section is <b>off</b> by default. Check the prices, then tick <b>Show</b> and save to publish it.</div>' : '';
      return '<div class="cat-box' + (s.enabled ? '' : ' cat-off') + '" data-si="' + si + '">' +
        '<div class="cat-row"><input type="text" data-f="sicon" maxlength="8" value="' + esc(s.icon) + '" style="max-width:60px;flex:none" aria-label="Icon" placeholder="&#128241;">' +
        '<input type="text" data-f="stitle" maxlength="60" value="' + esc(s.title) + '" aria-label="Section name">' +
        '<label class="check" style="margin:0"><input type="checkbox" data-f="senabled"' + (s.enabled ? ' checked' : '') + '><span>Show</span></label>' +
        '<button type="button" class="btn small ghost" data-act="sup" title="Move up">&uarr;</button><button type="button" class="btn small ghost" data-act="sdown" title="Move down">&darr;</button>' +
        '<button type="button" class="btn small outline" data-act="sdel" style="color:#b42318">Delete section</button></div>' + hint +
        s.services.map((v, vi) => '<div class="cat-item" data-vi="' + vi + '"><div class="cat-row">' +
          '<input type="text" data-f="vname" maxlength="120" value="' + esc(v.name) + '" aria-label="Service" placeholder="Service name">' +
          '<input type="text" data-f="vprice" maxlength="60" value="' + esc(v.price) + '" aria-label="Price" placeholder="Price, e.g. ₱400 - ₱500" style="max-width:190px">' +
          '<input type="text" data-f="vnotes" maxlength="200" value="' + esc(v.notes) + '" aria-label="Notes" placeholder="Notes / details">' +
          '<label class="check" style="margin:0"><input type="checkbox" data-f="vavail"' + (v.available ? ' checked' : '') + '><span>Offered</span></label>' +
          '<button type="button" class="btn small ghost" data-act="vup" title="Move up">&uarr;</button><button type="button" class="btn small ghost" data-act="vdown" title="Move down">&darr;</button>' +
          '<button type="button" class="btn small outline" data-act="vdel" style="color:#b42318" title="Remove service">&times;</button></div></div>').join('') +
        '<button type="button" class="btn small outline" data-act="vadd" style="margin-top:8px">+ Add service</button></div>';
    }).join('');
    h += '<button type="button" class="btn small" data-act="sadd">+ Add section</button>' +
      '<h3 style="margin-top:16px">Device types in the repair request form</h3><p class="small muted">One per line. &ldquo;Mobile phone / Tablet&rdquo; is added automatically while the Mobile section is shown.</p>' +
      '<textarea data-f="devs" rows="5" maxlength="800">' + esc(R.device_types.join('\n')) + '</textarea>';
    $id('catPanelRepair').innerHTML = h;
  }

  function render() {
    const y = window.scrollY;
    renderSouv(); renderRepair();
    $id('catPanelSouv').classList.toggle('hidden', panel !== 'souv'); $id('catPanelRepair').classList.toggle('hidden', panel !== 'repair');
    document.querySelectorAll('[data-cpanel]').forEach(b => b.setAttribute('aria-pressed', b.dataset.cpanel === panel));
    window.scrollTo(0, y);
  }
  const idx = (el, a) => { const e = el.closest('[' + a + ']'); return e ? +e.getAttribute(a) : -1; };
  function move(arr, i, d) { const j = i + d; if (j < 0 || j >= arr.length) return false; const t = arr[i]; arr[i] = arr[j]; arr[j] = t; return true; }
  const lines = v => v.split('\n').map(x => x.trim()).filter(Boolean);

  // ------------------------------------------------------------ text / checkbox edits (no re-render, keeps the cursor)
  function onField(e) {
    const f = e.target.dataset.f; if (!f) return;
    const ci = idx(e.target, 'data-ci'), ii = idx(e.target, 'data-ii'), si = idx(e.target, 'data-si'), vi = idx(e.target, 'data-vi');
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    const S = C.souvenirs, R = C.repair;
    if (f === 'showout') S.show_out_of_stock = v;
    else if (f === 'sopts') S.order_options = lines(v);
    else if (f === 'ctitle') S.categories[ci].title = v;
    else if (f === 'cenabled') { S.categories[ci].enabled = v; e.target.closest('.cat-box').classList.toggle('cat-off', !v); }
    else if (f === 'iname') S.categories[ci].items[ii].name = v;
    else if (f === 'iprice') S.categories[ci].items[ii].price = v;
    else if (f === 'istock') { S.categories[ci].items[ii].in_stock = v; if (e.type === 'change') renderKeep(); }
    else if (f === 'devs') R.device_types = lines(v);
    else if (f === 'sicon') R.sections[si].icon = v;
    else if (f === 'stitle') R.sections[si].title = v;
    else if (f === 'senabled') { R.sections[si].enabled = v; if (e.type === 'change') renderKeep(); }
    else if (f === 'vname') R.sections[si].services[vi].name = v;
    else if (f === 'vprice') R.sections[si].services[vi].price = v;
    else if (f === 'vnotes') R.sections[si].services[vi].notes = v;
    else if (f === 'vavail') R.sections[si].services[vi].available = v;
    setDirty(true);
  }
  function renderKeep() { render(); }

  // ------------------------------------------------------------ buttons
  async function addPictures(input) {
    const ci = idx(input, 'data-ci'), ii = idx(input, 'data-ii');
    const files = Array.from(input.files || []); input.value = '';
    const item = C.souvenirs.categories[ci].items[ii];
    let ok = 0;
    for (const f of files) {
      if (item.images.length >= 12) { status('Up to 12 pictures per product.', true); break; }
      busy++; status('Uploading ' + f.name + '...');
      try {
        const fd = new FormData(); fd.append('file', f, f.name);
        const r = await api('/api/admin/catalog/image', { method: 'POST', body: fd });
        item.images.push(r.url); ok++;
      } catch (err) { status(f.name + ': ' + err.message, true); }
      busy--;
    }
    if (ok) { setDirty(true); render(); status(ok + ' picture(s) added - press Save changes.'); }
  }
  function onClick(e) {
    const b = e.target.closest('[data-act]'); if (!b || b.type === 'file') return;
    const a = b.dataset.act;
    const ci = idx(b, 'data-ci'), ii = idx(b, 'data-ii'), ji = idx(b, 'data-ji'), si = idx(b, 'data-si'), vi = idx(b, 'data-vi');
    const S = C.souvenirs, R = C.repair;
    let changed = true;
    if (a === 'cadd') S.categories.push({ id: rid('c'), title: 'New category', enabled: true, items: [] });
    else if (a === 'cdel') { if (!confirm('Delete the category "' + S.categories[ci].title + '" and all its products?')) return; S.categories.splice(ci, 1); }
    else if (a === 'cup') changed = move(S.categories, ci, -1);
    else if (a === 'cdown') changed = move(S.categories, ci, 1);
    else if (a === 'iadd') S.categories[ci].items.push({ id: rid('p'), name: 'New product', price: '', images: [], in_stock: true });
    else if (a === 'idel') { if (!confirm('Delete "' + S.categories[ci].items[ii].name + '" for good?\n(To only hide it for now, untick "In stock" instead.)')) return; S.categories[ci].items.splice(ii, 1); }
    else if (a === 'iup') changed = move(S.categories[ci].items, ii, -1);
    else if (a === 'idown') changed = move(S.categories[ci].items, ii, 1);
    else if (a === 'imgdel') S.categories[ci].items[ii].images.splice(ji, 1);
    else if (a === 'imgcover') { const im = S.categories[ci].items[ii].images; im.unshift(im.splice(ji, 1)[0]); }
    else if (a === 'sadd') R.sections.push({ id: rid('sec'), title: 'New section', icon: '', enabled: true, services: [] });
    else if (a === 'sdel') { if (!confirm('Delete the section "' + R.sections[si].title + '" and all its services?\n(To only hide it, untick "Show" instead.)')) return; R.sections.splice(si, 1); }
    else if (a === 'sup') changed = move(R.sections, si, -1);
    else if (a === 'sdown') changed = move(R.sections, si, 1);
    else if (a === 'vadd') R.sections[si].services.push({ id: rid('s'), name: '', price: '', notes: '', available: true });
    else if (a === 'vdel') R.sections[si].services.splice(vi, 1);
    else if (a === 'vup') changed = move(R.sections[si].services, vi, -1);
    else if (a === 'vdown') changed = move(R.sections[si].services, vi, 1);
    else return;
    if (changed) { setDirty(true); render(); }
  }

  async function save() {
    if (busy) return;
    $id('catSave').disabled = true; status('Saving...');
    try {
      // services left without a name would be dropped by the server, so drop them here too
      C.repair.sections.forEach(s => { s.services = s.services.filter(v => v.name.trim()); });
      C = await api('/api/admin/catalog', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(C) });
      dirty = false; render(); setDirty(false); status('Saved. Customers see the new catalog now.');
    } catch (err) { status(err.message, true); $id('catSave').disabled = false; }
  }
  async function load() {
    try { C = await api('/api/admin/catalog'); dirty = false; render(); setDirty(false); }
    catch (err) { status(err.message, true); }
  }

  $id('catPanelSouv').addEventListener('click', onClick); $id('catPanelRepair').addEventListener('click', onClick);
  [$id('catPanelSouv'), $id('catPanelRepair')].forEach(p => {
    p.addEventListener('input', e => { if (e.target.type !== 'checkbox') onField(e); });
    p.addEventListener('change', e => { if (e.target.dataset.act === 'imgadd') addPictures(e.target); else if (e.target.type === 'checkbox') onField(e); });
  });
  document.querySelectorAll('[data-cpanel]').forEach(b => b.addEventListener('click', () => { panel = b.dataset.cpanel; render(); }));
  $id('catSave').addEventListener('click', save);
  $id('catDiscard').addEventListener('click', () => { if (confirm('Throw away your unsaved changes?')) load(); });
  window.JCMCatalogAdmin = { open() { if (!C || !dirty) load(); } };
})();
