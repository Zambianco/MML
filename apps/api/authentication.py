from rest_framework.authentication import TokenAuthentication


class AppTokenAuthentication(TokenAuthentication):
    keyword = "Token"

    def authenticate(self, request):
        auth = request.META.get("HTTP_AUTHORIZATION", b"")
        if isinstance(auth, str) and auth.startswith("Bearer "):
            request.META["HTTP_AUTHORIZATION"] = f"Token {auth[7:]}"
        return super().authenticate(request)
