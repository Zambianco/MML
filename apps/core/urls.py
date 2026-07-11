from django.urls import path

from .views import healthcheck, home, pwa_manifest, service_worker

urlpatterns = [
    path("", home, name="home"),
    path("health/", healthcheck, name="healthcheck"),
    path("manifest.webmanifest", pwa_manifest, name="pwa-manifest"),
    path("service-worker.js", service_worker, name="service-worker"),
]
