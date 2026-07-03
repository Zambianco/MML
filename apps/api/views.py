from django.http import HttpRequest, JsonResponse


def api_index(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "name": "music-server",
            "stage": "marco-zero",
            "status": "ok",
        }
    )
