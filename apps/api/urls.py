from django.urls import path

from .views import api_index

urlpatterns = [
    path("", api_index, name="api-index"),
]
