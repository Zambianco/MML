from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse


def home(request: HttpRequest) -> HttpResponse:
    return redirect(reverse("downloads-player"))


def healthcheck(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})
