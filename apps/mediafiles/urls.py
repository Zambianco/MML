from django.urls import path

from .views import create_backup_target, create_directory, library_dashboard, media_file_stream, queue_backups, queue_cleanup, scan_directories

urlpatterns = [
    path("", library_dashboard, name="library-dashboard"),
    path("directories/", create_directory, name="create-directory"),
    path("backup-targets/", create_backup_target, name="create-backup-target"),
    path("backup/run/", queue_backups, name="queue-backups"),
    path("cleanup/run/", queue_cleanup, name="queue-cleanup"),
    path("scan/", scan_directories, name="scan-directories"),
    path("media/<int:pk>/tocar/", media_file_stream, name="media-file-stream"),
]
