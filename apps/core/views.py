from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    context = {
        "project_name": "My Music Library",
        "project_summary": "Base modular para o servidor da sua biblioteca musical.",
    }
    return render(request, "core/home.html", context)


def healthcheck(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})
