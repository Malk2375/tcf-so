{% autoescape off %}// Service worker de TCF SO (SPEC 14), généré par le serveur.
// Habillage en cache ; pages HTML en « réseau d'abord » avec repli sur la page hors ligne.
// Jamais interceptés : requêtes autres que GET, autres origines, {{ bypass }}.
const CACHE = "tcf-so-{{ version }}";
const PRECACHE = {{ precache }};
const OFFLINE_URL = {{ offline_url }};
const STATIC_PREFIX = {{ static_prefix }};
const BYPASS = {{ bypass }};

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function intercepted(request) {
  if (request.method !== "GET") return false;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return false;
  if (BYPASS.some((prefix) => url.pathname.startsWith(prefix))) return false;
  return request.mode === "navigate" || url.pathname.startsWith(STATIC_PREFIX);
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (!intercepted(request)) return; // le navigateur traite la requête normalement
  if (request.mode === "navigate") {
    // Réseau d'abord. Les pages ne sont pas mises en cache : elles contiennent des données
    // personnelles et un jeton CSRF. Sans réseau : la page hors ligne.
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  // Fichiers statiques : le cache d'abord, mis à jour en arrière-plan.
  event.respondWith(
    caches.open(CACHE).then((cache) =>
      cache.match(request).then((cached) => {
        const network = fetch(request)
          .then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          })
          .catch(() => cached);
        return cached || network;
      }),
    ),
  );
});
{% endautoescape %}
