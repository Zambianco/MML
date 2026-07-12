from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import resolve_url
from django.urls import get_script_prefix, set_script_prefix
from django.utils.http import url_has_allowed_host_and_scheme


class ScriptNamePrefixMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.prefix = (getattr(settings, "URL_PREFIX", "") or "").rstrip("/")

    def __call__(self, request):
        header_prefix = (
            request.META.get("HTTP_X_FORWARDED_PREFIX")
            or request.META.get("HTTP_SCRIPT_NAME")
            or ""
        ).rstrip("/")
        path = request.path_info
        active_prefix = header_prefix
        if not active_prefix and self.prefix and (path == self.prefix or path.startswith(f"{self.prefix}/")):
            active_prefix = self.prefix

        previous_prefix = get_script_prefix()
        if active_prefix:
            set_script_prefix(f"{active_prefix}/")
            request.META["SCRIPT_NAME"] = active_prefix
            if path == active_prefix or path.startswith(f"{active_prefix}/"):
                stripped = path[len(active_prefix):] or "/"
                request.path_info = stripped
                request.path = f"{active_prefix}{stripped}"
                request.META["PATH_INFO"] = stripped
            else:
                request.path = f"{active_prefix}{path}"

        try:
            return self.get_response(request)
        finally:
            set_script_prefix(previous_prefix)


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.public_paths = {
            "/health/",
            "/login/",
            "/logout/",
            "/manifest.webmanifest",
            "/service-worker.js",
        }
        self.public_prefixes = ("/admin/", "/static/")

    def __call__(self, request):
        path = request.path_info or "/"
        if (
            request.user.is_authenticated
            or path in self.public_paths
            or any(path.startswith(prefix) for prefix in self.public_prefixes)
        ):
            return self.get_response(request)

        if path == "/api/auth/token/" or path.startswith("/api/auth/token/"):
            return self.get_response(request)
        auth_header = request.headers.get("Authorization", "")
        if path.startswith("/api/") and (
            auth_header.startswith("Token ") or auth_header.startswith("Bearer ")
        ):
            return self.get_response(request)
        if path.startswith("/api/"):
            return JsonResponse({"detail": "Authentication credentials were not provided."}, status=401)

        next_url = request.get_full_path()
        if not url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = "/"

        return HttpResponseRedirect(f"{resolve_url(settings.LOGIN_URL)}?next={next_url}")
