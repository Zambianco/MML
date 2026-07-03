from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    context = {
        "project_name": "Marco Zero",
        "project_summary": "Nova base modular para o servidor de biblioteca musical.",
    }
    return render(request, "core/home.html", context)


def healthcheck(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})
