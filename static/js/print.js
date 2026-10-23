/* Print portal page. Works offline: no external scripts, fonts or images. */
(async function () {
  'use strict';
  const T = (k, v) => I18N.t(k, v);
  const L = window.JCMLayout;
  let cfg;
  try { cfg = await getConfig(); } catch (e) {
    document.querySelector('main').innerHTML = '<div class="card"><div class="alert bad">' + esc(T('err.noserver')) + '</div></div>';
    return;
  }
  fillShop(cfg);

  const IMG_EXT = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tif', 'tiff', 'heic', 'heif'];
  const ALLOWED = IMG_EXT.concat(['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp', 'rtf', 'txt', 'csv']);
  const lim = cfg.limits;
  const MAXB = lim.max_file_mb * 1024 * 1024;
  const $id = id => document.getElementById(id);
  const extOf = n => (n.lastIndexOf('.') > -1 ? n.slice(n.lastIndexOf('.') + 1).toLowerCase() : '');
  const clampInt = (v, lo, hi) => Math.max(lo, Math.min(hi, parseInt(v, 10) || lo));

  // ------------------------------------------------------------------ state
  const S = {
    files: [], seq: 0, preset: 'document', userPickedDocument: false, autoOpened: false,
    doc: { color_mode: 'auto', copies: 1, paper: 'A4', duplex: false },
    photo: { layout: '10x15', paper_size: 'A4', paper_type: 'plain', copies: 1, fit: true, rotate: true, pdf: null, pages: 0, sig: null },
    rush: { pdf: null, pages: 0, sig: null, idx: 0 },
    sending: false,
  };
  const estCache = new Map();
  const isPhoto = () => S.preset === 'photo';
  const isRush = () => S.preset.startsWith('rush:');
  const rushPkg = () => cfg.rush_id.packages.find(p => 'rush:' + p.key === S.preset);
  const imgFiles = () => S.files.filter(f => f.isImage);
  const okImgs = () => S.files.filter(f => f.isImage && f.img && !f.broken);

  // ------------------------------------------------------------------ static UI
  function buildPresets() {
    const sel = $id('preset');
    sel.innerHTML = '';
    const add = (v, t) => { const o = document.createElement('option'); o.value = v; o.textContent = t; sel.appendChild(o); };
    add('document', T('print.type.document'));
    add('photo', T('print.type.photo'));
    cfg.rush_id.packages.forEach(p => add('rush:' + p.key, p.label + ' - ' + peso(p.price)));
    sel.value = S.preset;
  }
  function buildRates() {
    const r = cfg.rates;
    let h = '<table><tbody>';
    h += '<tr><td>' + esc(T('rate.bw')) + '</td><td><b>' + peso(r.doc_bw) + '</b> ' + esc(T('rate.perpage')) + '</td></tr>';
    h += '<tr><td>' + esc(T('rate.color')) + '</td><td><b>' + peso(r.doc_color) + '</b> ' + esc(T('rate.perpage')) + '</td></tr>';
    Object.keys(cfg.photo_paper).forEach(k => {
      const p = cfg.photo_paper[k];
      h += '<tr><td>' + esc(p.label) + '</td><td>' + Object.keys(p.rates).map(s => s + ' <b>' + peso(p.rates[s]) + '</b>').join(' &middot; ') + ' ' + esc(T('rate.persheet')) + '</td></tr>';
    });
    cfg.rush_id.packages.forEach(p => { h += '<tr><td>' + esc(p.label) + '</td><td><b>' + peso(p.price) + '</b> ' + esc(T('rate.perset')) + '</td></tr>'; });
    h += '<tr><td>' + esc(T('rate.typing')) + '</td><td><b>' + peso(r.typing_fee) + '</b> + ' + esc(T('rate.printing')) + '</td></tr></tbody></table>';
    $id('ratesBody').innerHTML = h;
  }
  function fillSelect(sel, obj, labelFn, val) {
    sel.innerHTML = '';
    Object.keys(obj).forEach(k => { const o = document.createElement('option'); o.value = k; o.textContent = labelFn(obj[k]); sel.appendChild(o); });
    sel.value = val;
  }
  fillSelect($id('paperSize'), cfg.paper_sizes, p => p.label, 'A4');
  fillSelect($id('phPaperSize'), cfg.paper_sizes, p => p.label, 'A4');
  fillSelect($id('phPaperType'), cfg.photo_paper, p => p.label, 'plain');
  const me = store.get('jcm_me', {});
  $id('custName').value = me.name || '';
  $id('custPhone').value = me.phone || '';

  function buildConsent() {
    $id('consent').textContent = cfg.retention_days > 0 ? T('print.consent', { days: cfg.retention_days }) : T('print.consent0');
  }

  // ------------------------------------------------------------------ files
  function addFiles(list) {
    const msgs = [];
    for (const file of Array.from(list)) {
      const ext = extOf(file.name);
      if (ALLOWED.indexOf(ext) < 0) { msgs.push(T('f.type', { name: file.name })); continue; }
      if (file.size === 0) { msgs.push(T('f.empty', { name: file.name })); continue; }
      if (file.size > MAXB) { msgs.push(T('f.big', { name: file.name, mb: lim.max_file_mb })); continue; }
      if (S.files.length >= lim.max_files) { msgs.push(T('f.many', { n: lim.max_files })); break; }
      if (S.files.some(f => f.file.name === file.name && f.file.size === file.size && f.file.lastModified === file.lastModified)) continue;
      const isImage = IMG_EXT.indexOf(ext) >= 0 || (file.type || '').startsWith('image/');
      S.files.push({ id: ++S.seq, file, isImage, url: isImage ? URL.createObjectURL(file) : null, est: null, broken: false, img: null, imgP: null, thumb: null, crop: { zoom: 1, cx: 0.5, cy: 0.5 } });
    }
    const box = $id('fileMsg');
    box.classList.toggle('hidden', !msgs.length);
    box.innerHTML = msgs.map(esc).join('<br>');
    onFilesChanged();
  }
  function removeFile(id) {
    const i = S.files.findIndex(f => f.id === id);
    if (i < 0) return;
    if (S.files[i].url) URL.revokeObjectURL(S.files[i].url);
    S.files.splice(i, 1);
    onFilesChanged();
  }
  function onFilesChanged() {
    // photos only -> suggest the photo workflow, but never fight the customer's own choice
    if (S.preset === 'document' && S.files.length && S.files.every(f => f.isImage) && !S.userPickedDocument) {
      S.preset = 'photo'; $id('preset').value = 'photo'; toast(T('f.autophoto'));
    }
    if (!S.files.length) S.autoOpened = false;
    render();
    ensureEstimates();
    maybeAutoOpen();
  }
  function maybeAutoOpen() {
    if ((isPhoto() || isRush()) && okImgsPossible() && !S.autoOpened && !S.sending) {
      S.autoOpened = true;
      setTimeout(openStudio, 150);
    }
  }
  const okImgsPossible = () => imgFiles().length > 0 && S.files.every(f => f.isImage);

  // per-file page/colour check by the server (documents only)
  let estRunning = false;
  async function ensureEstimates() {
    if (estRunning || S.preset !== 'document') return;
    estRunning = true;
    try {
      for (;;) {
        const f = S.files.find(x => x.est === null);
        if (!f) break;
        const key = f.file.name + '|' + f.file.size + '|' + f.file.lastModified;
        if (estCache.has(key)) { f.est = estCache.get(key); continue; }
        f.est = 'loading'; render();
        try {
          const fd = new FormData(); fd.append('file', f.file, f.file.name);
          const r = await fetch('/api/estimate', { method: 'POST', body: fd });
          const j = await r.json();
          if (!r.ok) throw new Error(j.detail || 'x');
          const res = j.files[0];
          if (res.accepted === false) f.est = { error: true, reason: res.reason };
          else { f.est = res; estCache.set(key, res); }
        } catch (e) { f.est = { error: true, reason: '' }; }
        render();
      }
    } finally { estRunning = false; }
  }

  // ------------------------------------------------------------------ rendering
  function fileIcon(f) {
    const e = extOf(f.file.name);
    return e === 'pdf' ? '&#128196;' : ['doc', 'docx', 'rtf', 'odt', 'txt'].indexOf(e) >= 0 ? '&#128221;' : ['xls', 'xlsx', 'csv', 'ods'].indexOf(e) >= 0 ? '&#128202;' : ['ppt', 'pptx', 'odp'].indexOf(e) >= 0 ? '&#128253;' : '&#128444;';
  }
  function renderFiles() {
    const doc = S.preset === 'document';
    $id('fileList').innerHTML = S.files.map(f => {
      let sub = fmtBytes(f.file.size), warn = false;
      if (doc) {
        if (!f.est || f.est === 'loading') sub += ' &middot; ' + esc(T('f.checking'));
        else if (f.est.error) { sub += ' &middot; &#9888; ' + esc(f.est.reason || T('f.noest')); warn = true; }
        else {
          sub += ' &middot; ' + esc(T('f.pages', { n: f.est.pages })) + ' &middot; ' + esc(T('f.cb', { c: f.est.color_pages, b: f.est.bw_pages }));
          if (f.est.needs_review) { sub += ' &middot; &#9888; ' + esc(T('f.confirm')); warn = true; }
        }
      } else if (!f.isImage) { sub += ' &middot; &#9888; ' + esc(T('f.notphoto')); warn = true; }
      else if (f.broken) { sub += ' &middot; &#9888; ' + esc(T('f.broken')); warn = true; }
      const th = f.isImage && !f.broken ? '<img class="thumb" src="' + f.url + '" alt="">' : '<span class="thumb" aria-hidden="true">' + fileIcon(f) + '</span>';
      return '<li>' + th + '<div class="meta"><div class="nm">' + esc(f.file.name) + '</div><div class="sub"' + (warn ? ' style="color:#9a6700"' : '') + '>' + sub + '</div></div>' +
        '<button type="button" class="x" data-rm="' + f.id + '" aria-label="' + esc(T('f.remove')) + '">&#10005;</button></li>';
    }).join('');
  }

  function docEstimate() {
    const r = cfg.rates; let bw = 0, col = 0, pending = false, review = false;
    S.files.forEach(f => {
      const e = f.est;
      if (!e || e === 'loading') { pending = true; return; }
      if (e.error) { bw += 1; review = true; return; }
      bw += e.bw_pages; col += e.color_pages; if (e.needs_review) review = true;
    });
    const pages = bw + col;
    if (S.doc.color_mode === 'force_bw') { bw = pages; col = 0; } else if (S.doc.color_mode === 'force_color') { bw = 0; col = pages; }
    const sur = Number((r.doc_paper_surcharge || {})[S.doc.paper] || 0);
    const total = (bw * r.doc_bw + col * r.doc_color + pages * sur) * S.doc.copies;
    const lines = [];
    if (bw) lines.push(T('est.bw', { n: bw, p: peso(r.doc_bw) }));
    if (col) lines.push(T('est.color', { n: col, p: peso(r.doc_color) }));
    if (sur) lines.push(T('est.sur', { p: peso(sur) }));
    if (S.doc.copies > 1) lines.push(T('est.copies', { n: S.doc.copies }));
    return { total, lines, pending, review };
  }
  function photoGeom() {
    const lay = cfg.layouts.find(l => l.key === S.photo.layout), ps = cfg.paper_sizes[S.photo.paper_size];
    return { lay, ps, rects: L.layoutRects(lay, ps.w, ps.h, cfg.page.margin_mm, cfg.page.gap_mm).rects };
  }
  function photoSheetsFor(n) { return Math.max(1, Math.ceil(n * S.photo.copies / photoGeom().rects.length)); }
  const photoRate = () => Number(cfg.photo_paper[S.photo.paper_type].rates[S.photo.paper_size]);
  const photoSig = () => JSON.stringify([imgFiles().map(f => f.id), S.photo.layout, S.photo.paper_size, S.photo.paper_type, S.photo.copies, S.photo.fit, S.photo.rotate]);
  const rushSig = () => JSON.stringify([S.preset, imgFiles().map(f => [f.id, f.crop.zoom.toFixed(3), f.crop.cx.toFixed(4), f.crop.cy.toFixed(4)])]);
  const photoReady = () => !!S.photo.pdf && S.photo.sig === photoSig();
  const rushReady = () => !!S.rush.pdf && S.rush.sig === rushSig();

  function computeEstimate() {
    if (isPhoto()) {
      const n = imgFiles().length, sheets = photoReady() ? S.photo.pages : photoSheetsFor(n);
      return { total: sheets * photoRate(), lines: [T('est.sheets', { n: sheets, p: peso(photoRate()) })], pending: false, review: false };
    }
    if (isRush()) {
      const p = rushPkg(), n = imgFiles().length;
      return { total: n * p.price, lines: [T('est.sets', { n: n, p: peso(p.price) })], pending: false, review: false };
    }
    return docEstimate();
  }

  function render() {
    const doc = S.preset === 'document', st = isPhoto() || isRush();
    $id('docOpts').classList.toggle('hidden', !doc);
    $id('studioBox').classList.toggle('hidden', !st || !S.files.length);
    $id('sendOrigWrap').classList.toggle('hidden', !isPhoto());
    renderFiles();
    if (st && S.files.length) {
      const ready = isPhoto() ? photoReady() : rushReady();
      let sum;
      if (!ready) sum = T(isPhoto() ? 'st.photo.need' : 'st.id.need');
      else if (isPhoto()) sum = T('st.photo.ready', { layout: photoGeom().lay.name, sheets: S.photo.pages, size: S.photo.paper_size });
      else sum = T('st.id.ready', { n: imgFiles().length, sheets: S.rush.pages });
      $id('studioSummary').textContent = sum;
      $id('studioSummary').className = 'alert ' + (ready ? 'ok' : 'info');
      $id('studioBtn').textContent = ready ? T('st.edit') : T(isPhoto() ? 'st.photo.btn' : 'st.id.btn');
    }
    const box = $id('estimate');
    if (!S.files.length) { box.classList.add('hidden'); return; }
    const E = computeEstimate();
    box.classList.remove('hidden');
    $id('estTotal').textContent = E.pending ? '...' : peso(E.total);
    $id('estLines').innerHTML = E.lines.map(l => '<li>' + esc(l) + '</li>').join('');
    $id('estNote').textContent = E.review ? T('est.review') : T('est.note');
  }

  // ------------------------------------------------------------------ images + canvas helpers
  function loadImg(f) {
    if (f.imgP) return f.imgP;
    f.imgP = new Promise(res => {
      const im = new Image();
      const to = setTimeout(() => { f.broken = true; res(null); }, 25000);
      im.onload = () => {
        clearTimeout(to);
        if (!im.naturalWidth) { f.broken = true; res(null); return; }
        f.img = im;
        const k = Math.min(1, 900 / Math.max(im.naturalWidth, im.naturalHeight));
        const c = document.createElement('canvas');
        c.width = Math.max(1, Math.round(im.naturalWidth * k)); c.height = Math.max(1, Math.round(im.naturalHeight * k));
        c.getContext('2d').drawImage(im, 0, 0, c.width, c.height);
        f.thumb = c; res(im);
      };
      im.onerror = () => { clearTimeout(to); f.broken = true; res(null); };
      im.src = f.url;
    });
    return f.imgP;
  }
  const dims = s => [s.naturalWidth || s.width, s.naturalHeight || s.height];

  function drawCell(ctx, src, x, y, w, h, fill, rotate) {
    const [iw, ih] = dims(src);
    ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
    ctx.translate(x + w / 2, y + h / 2);
    let cw = w, ch = h;
    if (rotate && iw !== ih && ((iw > ih) !== (w > h))) { ctx.rotate(Math.PI / 2); cw = h; ch = w; }
    const sc = fill ? Math.max(cw / iw, ch / ih) : Math.min(cw / iw, ch / ih);
    ctx.drawImage(src, -iw * sc / 2, -ih * sc / 2, iw * sc, ih * sc);
    ctx.restore();
  }

  async function makePdf(pageCount, drawFn, pw, ph, onProgress) {
    const limit = MAXB * 0.95;
    const tries = [[300, 0.92], [220, 0.88], [150, 0.82]];
    let lastErr;
    for (let t = 0; t < tries.length; t++) {
      const dpi = tries[t][0], q = tries[t][1];
      try {
        const pages = [];
        for (let i = 0; i < pageCount; i++) {
          const cv = document.createElement('canvas');
          drawFn(cv, i, dpi / 25.4);
          const blob = await new Promise(r => cv.toBlob(r, 'image/jpeg', q));
          if (!blob) throw new Error('canvas');
          pages.push({ jpeg: new Uint8Array(await blob.arrayBuffer()), w: cv.width, h: cv.height });
          cv.width = cv.height = 0;
          if (onProgress) onProgress((i + 1) / pageCount);
          await new Promise(r => setTimeout(r, 0));
        }
        const bytes = window.JCMPdf.buildJpegPdf(pages, pw, ph);
        if (bytes.length <= limit || t === tries.length - 1) return new Blob([bytes], { type: 'application/pdf' });
      } catch (e) { lastErr = e; }
    }
    throw lastErr || new Error('pdf');
  }
  const setBar = (id, f) => { $id(id).style.width = Math.round(f * 100) + '%'; };

  // ------------------------------------------------------------------ PHOTO STUDIO
  let phIdx = 0, phSnap = null;
  const photoInstances = () => {
    const out = [];
    okImgs().forEach(f => { for (let i = 0; i < S.photo.copies; i++) out.push(f); });
    return out;
  };
  function drawPhotoSheet(cv, page, px, full, guides) {
    const g = photoGeom(), inst = photoInstances(), per = g.rects.length;
    cv.width = Math.round(g.ps.w * px); cv.height = Math.round(g.ps.h * px);
    const ctx = cv.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
    for (let k = 0; k < per; k++) {
      const [x, y, w, h] = g.rects[k];
      if (guides) { ctx.strokeStyle = '#c3c9d8'; ctx.setLineDash([5, 4]); ctx.strokeRect(x * px, y * px, w * px, h * px); ctx.setLineDash([]); }
      const f = inst[page * per + k];
      if (!f) continue;
      drawCell(ctx, full ? f.img : (f.thumb || f.img), x * px, y * px, w * px, h * px, S.photo.fit, S.photo.rotate);
    }
  }
  function renderPhotoLayouts() {
    const ps = cfg.paper_sizes[S.photo.paper_size];
    $id('phLayouts').innerHTML = cfg.layouts.map(l => {
      const n = L.layoutRects(l, ps.w, ps.h, cfg.page.margin_mm, cfg.page.gap_mm).rects.length;
      return '<button type="button" data-lay="' + esc(l.key) + '" aria-pressed="' + (l.key === S.photo.layout) + '"><b>' + esc(l.name) + '</b><span>' + esc(T('ph.perpage', { n: n })) + '</span></button>';
    }).join('');
  }
  function phRedraw() {
    const inst = photoInstances(), per = photoGeom().rects.length;
    const sheets = Math.max(1, Math.ceil(inst.length / per));
    phIdx = Math.max(0, Math.min(phIdx, sheets - 1));
    drawPhotoSheet($id('phCanvas'), phIdx, 2.2, false, true);
    $id('phPage').textContent = T('ph.sheet', { i: phIdx + 1, n: sheets });
    $id('phPrev').disabled = phIdx === 0; $id('phNext').disabled = phIdx >= sheets - 1;
    $id('phTotal').textContent = peso(sheets * photoRate());
    $id('phSheets').textContent = T('est.sheets', { n: sheets, p: peso(photoRate()) });
    renderPhotoLayouts();
  }
  async function openPhotoStudio() {
    phSnap = Object.assign({}, S.photo);
    $id('phProg').classList.add('hidden');
    openDialog($id('photoDlg'));
    $id('phPaperSize').value = S.photo.paper_size; $id('phPaperType').value = S.photo.paper_type;
    $id('phCopies').value = S.photo.copies; $id('phFit').checked = S.photo.fit; $id('phRotate').checked = S.photo.rotate;
    await Promise.all(imgFiles().map(loadImg));
    const bad = imgFiles().filter(f => f.broken);
    const bb = $id('phBroken');
    bb.classList.toggle('hidden', !bad.length);
    bb.textContent = bad.length ? T('ph.broken', { names: bad.map(f => f.file.name).join(', ') }) : '';
    render(); phIdx = 0; phRedraw();
  }
  function phCancelStudio() { if (phSnap) Object.assign(S.photo, phSnap); closeDialog($id('photoDlg')); render(); }
  $id('phLayouts').addEventListener('click', e => { const b = e.target.closest('[data-lay]'); if (b) { S.photo.layout = b.dataset.lay; phRedraw(); } });
  $id('phPaperSize').addEventListener('change', e => { S.photo.paper_size = e.target.value; phRedraw(); });
  $id('phPaperType').addEventListener('change', e => { S.photo.paper_type = e.target.value; phRedraw(); });
  $id('phCopies').addEventListener('input', e => { S.photo.copies = clampInt(e.target.value, 1, lim.max_copies); phRedraw(); });
  $id('phFit').addEventListener('change', e => { S.photo.fit = e.target.checked; phRedraw(); });
  $id('phRotate').addEventListener('change', e => { S.photo.rotate = e.target.checked; phRedraw(); });
  $id('phPrev').addEventListener('click', () => { phIdx--; phRedraw(); });
  $id('phNext').addEventListener('click', () => { phIdx++; phRedraw(); });
  $id('phCancel').addEventListener('click', phCancelStudio);
  $id('phClose').addEventListener('click', phCancelStudio);
  $id('photoDlg').addEventListener('cancel', e => { e.preventDefault(); phCancelStudio(); });
  $id('phConfirm').addEventListener('click', async () => {
    const inst = photoInstances();
    if (!inst.length) { toast(T('err.addphotos'), 'bad'); return; }
    const g = photoGeom(), pages = Math.ceil(inst.length / g.rects.length);
    $id('phConfirm').disabled = $id('phCancel').disabled = true;
    $id('phProg').classList.remove('hidden'); setBar('phBar', 0);
    try {
      const blob = await makePdf(pages, (cv, i, px) => drawPhotoSheet(cv, i, px, true, false), g.ps.w, g.ps.h, p => setBar('phBar', p));
      S.photo.pdf = blob; S.photo.pages = pages; S.photo.sig = photoSig();
      closeDialog($id('photoDlg')); render(); toast(T('ph.done'), 'ok');
    } catch (e) { toast(T('err.pdf'), 'bad', 7000); }
    finally { $id('phConfirm').disabled = $id('phCancel').disabled = false; $id('phProg').classList.add('hidden'); }
  });

  // ------------------------------------------------------------------ ID PHOTO STUDIO
  let idStep = 'crop', idSheetIdx = 0, idSheets = [];
  const curId = () => imgFiles()[S.rush.idx];
  function cropRect(f) {
    const iw = f.img.naturalWidth, ih = f.img.naturalHeight, side = Math.min(iw, ih) / f.crop.zoom;
    const sx = Math.max(0, Math.min(iw - side, f.crop.cx * iw - side / 2)), sy = Math.max(0, Math.min(ih - side, f.crop.cy * ih - side / 2));
    return { sx, sy, side };
  }
  function clampCrop(f) {
    const iw = f.img.naturalWidth, ih = f.img.naturalHeight, side = Math.min(iw, ih) / f.crop.zoom;
    f.crop.cx = Math.max(side / 2 / iw, Math.min(1 - side / 2 / iw, f.crop.cx));
    f.crop.cy = Math.max(side / 2 / ih, Math.min(1 - side / 2 / ih, f.crop.cy));
  }
  function drawCrop() {
    const cv = $id('idCrop'), ctx = cv.getContext('2d'), n = imgFiles().length, f = curId();
    ctx.clearRect(0, 0, cv.width, cv.height);
    $id('idInfo').textContent = T('id.photo', { i: S.rush.idx + 1, n: n });
    $id('idPrev').disabled = S.rush.idx === 0; $id('idNext').disabled = S.rush.idx >= n - 1;
    if (!f || !f.img) { ctx.fillStyle = '#eee'; ctx.fillRect(0, 0, cv.width, cv.height); return; }
    clampCrop(f);
    const r = cropRect(f);
    ctx.drawImage(f.img, r.sx, r.sy, r.side, r.side, 0, 0, cv.width, cv.height);
    const W = cv.width, cx = W / 2, cy = W * 0.46, rx = W * 0.27, ry = W * 0.36;
    ctx.save(); ctx.fillStyle = 'rgba(0,0,0,.38)'; ctx.beginPath(); ctx.rect(0, 0, W, W); ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2); ctx.fill('evenodd'); ctx.restore();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 3; ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([8, 6]); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(W * 0.12, W * 0.38); ctx.lineTo(W * 0.88, W * 0.38); ctx.stroke(); ctx.setLineDash([]);
    $id('idZoom').value = f.crop.zoom;
  }
  function idLayout() {
    const sh = cfg.rush_id.sheet, pkg = rushPkg();
    const sizes = pkg.pieces.slice().sort((a, b) => b.size_in - a.size_in);
    const pages = []; let cur = null;
    okImgs().forEach(f => {
      const set = [];
      sizes.forEach(pc => { for (let i = 0; i < pc.count; i++) set.push({ w: pc.size_in * 25.4, h: pc.size_in * 25.4, f }); });
      let placed = false;
      if (cur) { const all = cur.pieces.concat(set), pos = L.packPieces(all, sh.w, sh.h, sh.margin, sh.gap); if (pos) { cur.pieces = all; cur.pos = pos; placed = true; } }
      if (!placed) {
        const pos = L.packPieces(set, sh.w, sh.h, sh.margin, sh.gap);
        if (!pos) throw new Error('sheet too small');
        cur = { pieces: set, pos }; pages.push(cur);
      }
    });
    return pages.map(p => p.pieces.map((pc, i) => ({ f: pc.f, x: p.pos[i].x, y: p.pos[i].y, w: pc.w, h: pc.h })));
  }
  function drawIdSheet(cv, items, px) {
    const sh = cfg.rush_id.sheet;
    cv.width = Math.round(sh.w * px); cv.height = Math.round(sh.h * px);
    const ctx = cv.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.strokeStyle = '#8a8f9c'; ctx.lineWidth = Math.max(1, 0.12 * px);
    items.forEach(it => {
      const r = cropRect(it.f);
      ctx.drawImage(it.f.img, r.sx, r.sy, r.side, r.side, it.x * px, it.y * px, it.w * px, it.h * px);
      ctx.strokeRect(it.x * px, it.y * px, it.w * px, it.h * px);
    });
  }
  function idShowStep(step) {
    idStep = step;
    $id('idCropPane').classList.toggle('hidden', step !== 'crop');
    $id('idSheetPane').classList.toggle('hidden', step !== 'sheet');
    $id('idBack').classList.toggle('hidden', step !== 'sheet');
    $id('idConfirm').classList.toggle('hidden', step !== 'sheet');
    $id('idNextStep').classList.toggle('hidden', step !== 'crop');
  }
  function idRedrawSheet() {
    idSheetIdx = Math.max(0, Math.min(idSheetIdx, idSheets.length - 1));
    drawIdSheet($id('idSheet'), idSheets[idSheetIdx], 2.6);
    $id('idSInfo').textContent = T('ph.sheet', { i: idSheetIdx + 1, n: idSheets.length });
    $id('idSPrev').disabled = idSheetIdx === 0; $id('idSNext').disabled = idSheetIdx >= idSheets.length - 1;
  }
  async function openIdStudio() {
    $id('idProg').classList.add('hidden'); idShowStep('crop'); S.rush.idx = 0;
    $id('idTitle').textContent = rushPkg().label;
    openDialog($id('idDlg'));
    await Promise.all(imgFiles().map(loadImg));
    const bad = imgFiles().filter(f => f.broken), bb = $id('idBroken');
    bb.classList.toggle('hidden', !bad.length);
    bb.textContent = bad.length ? T('id.broken', { names: bad.map(f => f.file.name).join(', ') }) : '';
    render(); drawCrop();
  }
  (function cropPointer() {
    const cv = $id('idCrop'); let drag = null;
    cv.addEventListener('pointerdown', e => { cv.setPointerCapture(e.pointerId); drag = { x: e.clientX, y: e.clientY }; });
    cv.addEventListener('pointermove', e => {
      const f = curId(); if (!drag || !f || !f.img) return;
      const k = cv.width / cv.getBoundingClientRect().width, dx = (e.clientX - drag.x) * k, dy = (e.clientY - drag.y) * k;
      drag = { x: e.clientX, y: e.clientY };
      const side = cropRect(f).side / cv.width;
      f.crop.cx -= dx * side / f.img.naturalWidth; f.crop.cy -= dy * side / f.img.naturalHeight;
      drawCrop();
    });
    const end = () => { drag = null; };
    cv.addEventListener('pointerup', end); cv.addEventListener('pointercancel', end);
    cv.addEventListener('wheel', e => { const f = curId(); if (!f) return; e.preventDefault(); f.crop.zoom = Math.max(1, Math.min(4, f.crop.zoom - e.deltaY * 0.002)); drawCrop(); }, { passive: false });
  })();
  $id('idZoom').addEventListener('input', e => { const f = curId(); if (f) { f.crop.zoom = parseFloat(e.target.value) || 1; drawCrop(); } });
  $id('idReset').addEventListener('click', () => { const f = curId(); if (f) { f.crop = { zoom: 1, cx: 0.5, cy: 0.5 }; drawCrop(); } });
  $id('idPrev').addEventListener('click', () => { S.rush.idx--; drawCrop(); });
  $id('idNext').addEventListener('click', () => { S.rush.idx++; drawCrop(); });
  $id('idSPrev').addEventListener('click', () => { idSheetIdx--; idRedrawSheet(); });
  $id('idSNext').addEventListener('click', () => { idSheetIdx++; idRedrawSheet(); });
  $id('idBack').addEventListener('click', () => { idShowStep('crop'); drawCrop(); });
  const idClose = () => { closeDialog($id('idDlg')); render(); };
  $id('idCancel').addEventListener('click', idClose); $id('idClose').addEventListener('click', idClose);
  $id('idDlg').addEventListener('cancel', e => { e.preventDefault(); idClose(); });
  $id('idNextStep').addEventListener('click', () => {
    if (imgFiles().some(f => f.broken)) { toast(T('id.brokenblock'), 'bad'); return; }
    try { idSheets = idLayout(); } catch (e) { toast(T('err.sheet'), 'bad', 7000); return; }
    const p = rushPkg();
    $id('idSummary').textContent = T('id.summary', { n: imgFiles().length, sheets: idSheets.length, label: p.label });
    $id('idTotal').textContent = peso(imgFiles().length * p.price);
    idShowStep('sheet'); idSheetIdx = 0; idRedrawSheet();
  });
  $id('idConfirm').addEventListener('click', async () => {
    const sh = cfg.rush_id.sheet;
    $id('idConfirm').disabled = $id('idBack').disabled = $id('idCancel').disabled = true;
    $id('idProg').classList.remove('hidden'); setBar('idBar', 0);
    try {
      const blob = await makePdf(idSheets.length, (cv, i, px) => drawIdSheet(cv, idSheets[i], px), sh.w, sh.h, p => setBar('idBar', p));
      S.rush.pdf = blob; S.rush.pages = idSheets.length; S.rush.sig = rushSig();
      closeDialog($id('idDlg')); render(); toast(T('ph.done'), 'ok');
    } catch (e) { toast(T('err.pdf'), 'bad', 7000); }
    finally { $id('idConfirm').disabled = $id('idBack').disabled = $id('idCancel').disabled = false; $id('idProg').classList.add('hidden'); }
  });

  function openStudio() { if (isPhoto()) openPhotoStudio(); else if (isRush()) openIdStudio(); }

  // ------------------------------------------------------------------ sending an order
  function showErr(msg) { const b = $id('formErr'); b.textContent = msg; b.classList.remove('hidden'); b.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
  const clearErr = () => $id('formErr').classList.add('hidden');
  function setProgress(frac, text) {
    $id('upProgress').classList.remove('hidden');
    $id('upBar').style.width = Math.round(frac * 100) + '%';
    $id('upText').textContent = text || T('up.sending', { p: Math.round(frac * 100) });
  }

  // opts: {kind, label, meta, files:[{blob,name}], extra:{k:v}, clientTotal, name, onProgress, onRetry}
  async function sendOrder(opts) {
    const phone = $id('custPhone').value.trim(), notes = $id('notes').value.trim();
    const meta = Object.assign({ kind: opts.kind, client_total: opts.clientTotal }, opts.meta);
    const build = () => {
      const fd = new FormData();
      fd.append('customer_name', opts.name); fd.append('phone', phone); fd.append('instructions', notes);
      fd.append('preset_request', opts.label); fd.append('order_meta', JSON.stringify(meta));
      Object.keys(opts.extra || {}).forEach(k => fd.append(k, opts.extra[k]));
      opts.files.forEach(f => fd.append('files', f.blob, f.name));
      return fd;
    };
    return uploadWithRetry('/api/submit_order', build, opts.onProgress, opts.onRetry);
  }
  function failText(res) {
    if (res.json && res.json.detail) return res.json.detail;
    if (res.status === 0) return T('err.network');
    if (res.status === 413) return T('err.toobig');
    return T('err.generic');
  }

  function showResult(j, label) {
    $id('orderForm').classList.add('hidden'); $id('introCard').classList.add('hidden');
    $id('result').classList.remove('hidden');
    $id('resId').textContent = j.order_id; $id('resCode').textContent = j.access_code;
    $id('resTotal').innerHTML = j.total > 0 ? '<b>' + esc(T(j.needs_review ? 'res.estimated' : 'res.total', { p: peso(j.total) })) + '</b>' + (j.needs_review ? '<div class="small muted">' + esc(T('est.review')) + '</div>' : '') : '';
    const pay = cfg.payment; let t = pay.note || '';
    if (pay.gcash_number) t += ' GCash: ' + pay.gcash_number + (pay.gcash_name ? ' (' + pay.gcash_name + ')' : '');
    $id('resPay').textContent = t; $id('resPay').classList.toggle('hidden', !t);
    $id('resStatus').href = '/status?o=' + encodeURIComponent(j.order_id) + '&c=' + encodeURIComponent(j.access_code);
    rememberOrder({ id: j.order_id, code: j.access_code, what: label });
    window.scrollTo(0, 0);
  }

  function resetAll() {
    S.files.forEach(f => { if (f.url) URL.revokeObjectURL(f.url); });
    S.files = []; S.autoOpened = false; S.userPickedDocument = false;
    S.photo.pdf = null; S.photo.sig = null; S.rush.pdf = null; S.rush.sig = null;
    S.doc = { color_mode: 'auto', copies: 1, paper: 'A4', duplex: false };
    $id('colorMode').value = 'auto'; $id('copies').value = 1; $id('paperSize').value = 'A4'; $id('duplex').checked = false;
    $id('notes').value = ''; $id('fileMsg').classList.add('hidden'); clearErr();
    $id('upProgress').classList.add('hidden');
    $id('result').classList.add('hidden'); $id('orderForm').classList.remove('hidden'); $id('introCard').classList.remove('hidden');
    S.preset = 'document'; $id('preset').value = 'document';
    render();
  }

  $id('orderForm').addEventListener('submit', async e => {
    e.preventDefault();
    if (S.sending) return;
    clearErr();
    const name = $id('custName').value.trim();
    if (!name) { showErr(T('err.name')); $id('custName').focus(); return; }
    if (!S.files.length) { showErr(T('err.nofiles')); return; }
    const st = isPhoto() || isRush();
    if (st) {
      if (S.files.some(f => !f.isImage)) { showErr(T('err.notphotos')); return; }
      if (!(isPhoto() ? photoReady() : rushReady())) { toast(T('err.layoutfirst')); openStudio(); return; }
    } else if (S.files.some(f => f.est && f.est.error && f.est.reason)) {
      showErr(T('err.badfile')); return;
    }
    const E = computeEstimate();
    let o;
    if (isPhoto()) {
      // unreadable photos are always sent as originals - the counter has to place those by hand
      const send = imgFiles().filter(f => $id('sendOrig').checked || f.broken);
      const files = send.map(f => ({ blob: f.file, name: f.file.name })).concat([{ blob: S.photo.pdf, name: 'Generated_Photo_Layout.pdf' }]);
      o = { kind: 'photo', label: T('print.type.photo') + ' - ' + photoGeom().lay.name, files,
        meta: { layout: S.photo.layout, paper_size: S.photo.paper_size, paper_type: S.photo.paper_type, copies: S.photo.copies, fit: S.photo.fit, rotate: S.photo.rotate, skipped: imgFiles().filter(f => f.broken).map(f => f.file.name) } };
    } else if (isRush()) {
      const files = imgFiles().map(f => ({ blob: f.file, name: f.file.name })).concat([{ blob: S.rush.pdf, name: 'Generated_RushID_Layout.pdf' }]);
      o = { kind: 'rushid', label: rushPkg().label, files, meta: { package: rushPkg().key } };
    } else {
      o = { kind: 'document', label: T('print.type.document'), files: S.files.map(f => ({ blob: f.file, name: f.file.name })),
        meta: { color_mode: S.doc.color_mode, copies: S.doc.copies, paper: S.doc.paper, duplex: S.doc.duplex } };
    }
    o.name = name; o.clientTotal = E.total;
    o.onProgress = p => setProgress(p); o.onRetry = n => setProgress(0, T('up.retry', { n: n }));
    S.sending = true; $id('submitBtn').disabled = true; $id('typedBtn').disabled = true; setProgress(0);
    const res = await sendOrder(o);
    S.sending = false; $id('submitBtn').disabled = false; $id('typedBtn').disabled = false;
    if (res.status === 200 && res.json && res.json.order_id) {
      store.set('jcm_me', { name, phone: $id('custPhone').value.trim() });
      showResult(res.json, o.label);
    } else { $id('upProgress').classList.add('hidden'); showErr(failText(res)); }
  });

  // ------------------------------------------------------------------ typed document
  let tdTimer = null, tdPages = 0;
  async function tdEstimate() {
    const text = $id('tdText').value, copies = clampInt($id('tdCopies').value, 1, lim.max_copies);
    try {
      const r = await fetch('/api/typed/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, copies }) });
      const j = await r.json();
      tdPages = j.pages;
      $id('tdTotal').textContent = peso(j.total);
      $id('tdInfo').textContent = T('td.info', { c: text.length, n: j.pages });
    } catch (e) { /* server unreachable: keep last numbers */ }
  }
  const tdSoon = () => { clearTimeout(tdTimer); tdTimer = setTimeout(tdEstimate, 350); };
  $id('typedBtn').addEventListener('click', () => { $id('tdName').value = $id('custName').value; $id('tdErr').classList.add('hidden'); openDialog($id('typedDlg')); tdEstimate(); });
  $id('tdText').addEventListener('input', tdSoon); $id('tdCopies').addEventListener('input', tdSoon);
  $id('tdName').addEventListener('input', e => { $id('custName').value = e.target.value; });
  const tdClose = () => closeDialog($id('typedDlg'));
  $id('tdCancel').addEventListener('click', tdClose); $id('tdClose').addEventListener('click', tdClose);
  $id('tdSend').addEventListener('click', async () => {
    const name = $id('tdName').value.trim(), text = $id('tdText').value;
    const err = m => { const b = $id('tdErr'); b.textContent = m; b.classList.remove('hidden'); };
    if (!name) { err(T('err.name')); return; }
    if (!text.trim()) { err(T('err.notext')); return; }
    if (text.length > lim.max_text_chars) { err(T('err.longtext', { n: lim.max_text_chars })); return; }
    $id('custName').value = name;
    $id('tdSend').disabled = true; $id('tdProg').classList.remove('hidden');
    const res = await sendOrder({ kind: 'typed', label: T('td.title'), name, files: [], extra: { typed_text: text },
      meta: { copies: clampInt($id('tdCopies').value, 1, lim.max_copies) },
      clientTotal: parseFloat(($id('tdTotal').textContent || '0').replace(/[^0-9.]/g, '')) || 0,
      onProgress: p => { $id('tdBar').style.width = Math.round(p * 100) + '%'; } });
    $id('tdSend').disabled = false; $id('tdProg').classList.add('hidden');
    if (res.status === 200 && res.json && res.json.order_id) {
      store.set('jcm_me', { name, phone: $id('custPhone').value.trim() });
      closeDialog($id('typedDlg')); showResult(res.json, T('td.title'));
    } else err(failText(res));
  });

  // ------------------------------------------------------------------ wiring
  $id('fileInput').addEventListener('change', e => { addFiles(e.target.files); e.target.value = ''; });
  const drop = $id('drop');
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => { if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files); });
  $id('fileList').addEventListener('click', e => { const b = e.target.closest('[data-rm]'); if (b) removeFile(+b.dataset.rm); });
  $id('preset').addEventListener('change', e => {
    S.preset = e.target.value; S.userPickedDocument = S.preset === 'document';
    S.autoOpened = false; render(); ensureEstimates(); maybeAutoOpen();
  });
  $id('colorMode').addEventListener('change', e => { S.doc.color_mode = e.target.value; render(); });
  $id('copies').addEventListener('input', e => { S.doc.copies = clampInt(e.target.value, 1, lim.max_copies); render(); });
  $id('paperSize').addEventListener('change', e => { S.doc.paper = e.target.value; render(); });
  $id('duplex').addEventListener('change', e => { S.doc.duplex = e.target.checked; });
  $id('studioBtn').addEventListener('click', openStudio);
  $id('againBtn').addEventListener('click', resetAll);
  $id('langBtn').addEventListener('click', () => I18N.toggle());
  window.addEventListener('beforeunload', e => { if (S.sending) { e.preventDefault(); e.returnValue = ''; } });

  function refreshText() { buildPresets(); buildRates(); buildConsent(); render(); }
  I18N.onChange(refreshText);
  I18N.apply();
  refreshText();
})();
