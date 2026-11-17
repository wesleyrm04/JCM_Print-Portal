/* JCM Print Portal service worker (served from /sw.js so it controls the whole site).
   1. Makes the portal installable and keeps the page shell for a bad connection.
      Pages and scripts are fetched from the network first, so staff changes show up straight away;
      the saved copy is only used when the network fails or is very slow. /api and /admin are never cached.
   2. Receives what the phone's Share button sends (Facebook / Messenger links, photos, PDFs) and
      hands it to /share. Shared files are kept in the browser (Cache Storage) until the order is sent. */
'use strict';

const SHELL = 'jcm-shell-v1';
const SHARE = 'jcm-share-v1';
const PRECACHE = [
  '/', '/print', '/status', '/share', '/repair', '/souvenirs',
  '/static/css/app.css', '/static/js/common.js', '/static/js/i18n.js', '/static/js/hub.js',
  '/static/js/status.js', '/static/js/share.js', '/static/images/logo.jpg',
  '/static/icons/icon-192.png', '/static/icons/icon-512.png',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(SHELL)
      .then(c => Promise.all(PRECACHE.map(u => c.add(u).catch(() => { /* one missing file must not stop the install */ }))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k.startsWith('jcm-shell-') && k !== SHELL).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function timeout(ms) { return new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms)); }

async function networkFirst(request, waitMs) {
  const cache = await caches.open(SHELL);
  try {
    const res = await Promise.race([fetch(request), timeout(waitMs)]);
    if (res && res.ok && request.method === 'GET') {
      const u = new URL(request.url);
      if (!u.search) cache.put(request, res.clone());           // do not fill the cache with ?query variants
    }
    return res;
  } catch (e) {
    const hit = (await cache.match(request, { ignoreSearch: true })) || (request.mode === 'navigate' && await cache.match('/'));
    if (hit) return hit;
    throw e;
  }
}

async function receiveShare(request) {
  try {
    const form = await request.formData();
    const cache = await caches.open(SHARE);
    // start clean: only the latest share is kept
    for (const k of await cache.keys()) await cache.delete(k);
    const meta = {
      title: String(form.get('title') || '').slice(0, 1500),
      text: String(form.get('text') || '').slice(0, 5000),
      url: String(form.get('url') || '').slice(0, 1500),
      files: [], at: Date.now(),
    };
    const files = form.getAll('files').filter(f => f && typeof f === 'object' && 'size' in f);
    let i = 0;
    for (const f of files.slice(0, 60)) {
      const key = '/__share__/file/' + (i++);
      await cache.put(key, new Response(f, { headers: { 'Content-Type': f.type || 'application/octet-stream' } }));
      meta.files.push({ key, name: f.name || ('shared-' + i), type: f.type || '', size: f.size });
    }
    await cache.put('/__share__/meta', new Response(JSON.stringify(meta), { headers: { 'Content-Type': 'application/json' } }));
  } catch (e) {
    /* nothing useful to keep - /share still opens and the customer can paste the link */
  }
  return Response.redirect(new URL('/share?received=1', self.location.origin).href, 303);
}

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.method === 'POST' && url.pathname === '/share') {
    event.respondWith(receiveShare(req));
    return;
  }
  if (req.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin') || url.pathname === '/health' || url.pathname === '/sw.js') return;
  if (req.headers.has('range')) return;

  event.respondWith(networkFirst(req, req.mode === 'navigate' ? 4000 : 6000));
});
