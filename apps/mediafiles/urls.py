from django.urls import path

from .views import create_backup_target, create_directory, library_dashboard, media_file_stream, scan_directories

urlpatterns = [
    path("", library_dashboard, name="library-dashboard"),
    path("directories/", create_directory, name="create-directory"),
    path("backup-targets/", create_backup_target, name="create-backup-target"),
    path("scan/", scan_directories, name="scan-directories"),
    path("media/<int:pk>/tocar/", media_file_stream, name="media-file-stream"),
]
