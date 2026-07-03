from django.urls import path

from .views import healthcheck, home

urlpatterns = [
    path("", home, name="home"),
    path("health/", healthcheck, name="healthcheck"),
]
