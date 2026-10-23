(async function () {
  'use strict';
  document.getElementById('langBtn').addEventListener('click', () => I18N.toggle());
  let cfg = null;
  try { cfg = await getConfig(); fillShop(cfg); } catch (e) { /* page still works without prices */ }

  function facts() {
    if (!cfg) return;
    const r = cfg.rates, rush = cfg.rush_id.packages;
    const minPhoto = Math.min.apply(null, Object.keys(cfg.photo_paper).map(k => Math.min.apply(null, Object.values(cfg.photo_paper[k].rates))));
    document.getElementById('printFacts').innerHTML =
      '<li>' + esc(I18N.t('hub.f.docs', { bw: peso(r.doc_bw), c: peso(r.doc_color) })) + '</li>' +
      '<li>' + esc(I18N.t('hub.f.photo', { p: peso(minPhoto) })) + '</li>' +
      '<li>' + esc(I18N.t('hub.f.rush', { n: rush.length })) + '</li>' +
      '<li>' + esc(I18N.t('hub.f.typing', { p: peso(r.typing_fee) })) + '</li>';
  }

  function recent() {
    const list = store.get('jcm_orders', []);
    document.getElementById('recentCard').hidden = !list.length;
    document.getElementById('recentList').innerHTML = list.slice(0, 5).map(o =>
      '<li><div class="meta"><div class="nm">' + esc(o.id) + '</div><div class="sub">' + esc(o.what || '') + '</div></div>' +
      '<a class="btn small" href="/status?o=' + encodeURIComponent(o.id) + '&c=' + encodeURIComponent(o.code) + '">' + esc(I18N.t('hub.check')) + '</a></li>').join('');
  }

  // slideshow: runs on a timer so it works on touch screens too
  const imgs = Array.from(document.querySelectorAll('#slide img'));
  let i = 0;
  if (imgs.length > 1 && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    setInterval(() => { imgs[i].classList.remove('on'); i = (i + 1) % imgs.length; imgs[i].classList.add('on'); }, 3500);
  }

  I18N.onChange(() => { facts(); recent(); });
  I18N.apply(); facts(); recent();
})();
