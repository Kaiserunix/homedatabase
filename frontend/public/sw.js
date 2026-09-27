// 手机端 PWA Service Worker（手写，无依赖）
// 缓存分两类：PRECACHE 存放应用外壳（install 时预缓存），RUNTIME 存放 /assets/ 构建产物。
// 改 CACHE_VERSION 即可在下次发布时整体失效旧缓存。
const CACHE_VERSION = "v1";
const PRECACHE = `hd-precache-${CACHE_VERSION}`;
const RUNTIME = `hd-runtime-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  "/index.html",
  "/manifest.webmanifest",
  "/pwa-icon.svg",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(PRECACHE).then((cache) => cache.addAll(PRECACHE_URLS)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  const keep = [PRECACHE, RUNTIME];
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => !keep.includes(k)).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // 导航请求：network-first，失败回退缓存的应用外壳
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(PRECACHE).then((cache) => cache.put("/index.html", copy));
          return response;
        })
        .catch(() => caches.match("/index.html")),
    );
    return;
  }

  // 构建产物（带 hash，内容不可变）：cache-first
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(RUNTIME).then((cache) => cache.put(request, copy));
            }
            return response;
          }),
      ),
    );
    return;
  }

  // 其余请求：直接走网络
});
