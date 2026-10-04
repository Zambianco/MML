from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.views import serve as serve_static
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.core.urls")),
    path("downloads/", include("apps.downloads.urls")),
    path("library/", include("apps.mediafiles.urls")),
    path("api/", include("apps.api.urls")),
]

if settings.DEBUG:
    urlpatterns += [path("static/<path:path>", serve_static, {"insecure": True})]
