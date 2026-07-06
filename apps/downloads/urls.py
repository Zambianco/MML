from django.urls import path

from .views import import_detail, import_list

urlpatterns = [
    path("", import_list, name="downloads-import-list"),
    path("<int:pk>/", import_detail, name="downloads-import-detail"),
]
