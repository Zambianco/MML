from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse


def home(request: HttpRequest) -> HttpResponse:
    return redirect(reverse("downloads-player"))


def healthcheck(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def pwa_manifest(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "name": "My Music Library Player",
            "short_name": "MML Player",
            "description": "Player da biblioteca My Music Library",
            "start_url": reverse("downloads-player"),
            "scope": reverse("downloads-player"),
            "display": "standalone",
            "background_color": "#0f0a0a",
            "theme_color": "#d4a373",
            "icons": [
                {
                    "src": static("core/pwa-icon.svg"),
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "any maskable",
                }
            ],
        },
        content_type="application/manifest+json",
    )


def service_worker(request: HttpRequest) -> HttpResponse:
    player_url = reverse("downloads-player")
    script = f"""
const CACHE_NAME = "mml-player-v1";
const PLAYER_URL = "{player_url}";
const PRECACHE_URLS = [PLAYER_URL];

self.addEventListener("install", (event) => {{
    event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)));
    self.skipWaiting();
}});

self.addEventListener("activate", (event) => {{
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
    );
    self.clients.claim();
}});

self.addEventListener("fetch", (event) => {{
    const request = event.request;
    const url = new URL(request.url);
    if (request.method !== "GET" || url.origin !== self.location.origin) {{
        return;
    }}
    if (url.pathname.includes("/arquivos/tocar/") || url.pathname.includes("/itens/")) {{
        return;
    }}
    if (request.mode === "navigate") {{
        event.respondWith(
            fetch(request)
                .then((response) => {{
                    const copy = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
                    return response;
                }})
                .catch(() => caches.match(request).then((cached) => cached || caches.match(PLAYER_URL)))
        );
        return;
    }}
    event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
}});
"""
    return HttpResponse(script, content_type="text/javascript")
