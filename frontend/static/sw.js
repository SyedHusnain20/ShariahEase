/*
 * ShariahEase service worker. Served from /sw.js (see app/routers/pages.py) so it can control the whole site.
 *
 * What it does:  keeps our OWN static files and an offline page so the installed app opens, and shows the offline
 *                page when a page cannot be fetched.
 * What it never does:  touch /api/ or /auth/ (no expense data, tokens or cookies are ever cached; writes need the
 *                      network), or cache anything from other sites (Tailwind, Alpine and fonts load from their CDNs,
 *                      so a fully offline page is the plain offline page, not the app).
 * To ship a change to a cached file, bump VERSION.
 */
const VERSION = 'v1';
const CACHE = 'se-shell-' + VERSION;
const OFFLINE_URL = '/static/offline.html';
const PRECACHE = [
  OFFLINE_URL,
  '/static/css/main.css',
  '/static/js/main.js',
  '/static/js/expenses.js',
  '/static/vendor/chartjs/chart.umd.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];
const NEVER_CACHE = ['/api/', '/auth/'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('se-shell-') && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;                       // writes always go to the network, untouched
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;        // other sites: not ours to cache
  if (NEVER_CACHE.some((p) => url.pathname.startsWith(p))) return;
  if (req.headers.has('authorization')) return;

  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (url.pathname.startsWith('/static/')) {              // our files: answer from cache, refresh in the background
    event.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const hit = await cache.match(req);
        const fresh = fetch(req).then((res) => {
          if (res.ok) cache.put(req, res.clone());
          return res;
        }).catch(() => hit);
        return hit || fresh;
      })
    );
  }
});
