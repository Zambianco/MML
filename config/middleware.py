from django.conf import settings
from django.urls import get_script_prefix, set_script_prefix


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

        try:
            return self.get_response(request)
        finally:
            set_script_prefix(previous_prefix)
