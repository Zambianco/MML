from django.http import HttpRequest, JsonResponse


def api_index(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "name": "my-music-library",
            "stage": "initial",
            "status": "ok",
        }
    )
