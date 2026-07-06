from django.urls import path

from .views import create_directory, library_dashboard, scan_directories

urlpatterns = [
    path("", library_dashboard, name="library-dashboard"),
    path("directories/", create_directory, name="create-directory"),
    path("scan/", scan_directories, name="scan-directories"),
]
