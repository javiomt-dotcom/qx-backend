// Guarda la app para que abra al instante; los datos siempre se piden en vivo a Supabase.
const V = "qx-v3", SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "apple-touch-icon.png", "vendor/supabase.js"];
self.addEventListener("install", e => { e.waitUntil(caches.open(V).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.hostname.endsWith("supabase.co")) return;
  // primero la red (para recibir versiones nuevas), y la copia guardada si no hay señal
  e.respondWith(fetch(e.request).then(r => { const c = r.clone(); caches.open(V).then(x => x.put(e.request, c)); return r; })
    .catch(() => caches.match(e.request)));
});
