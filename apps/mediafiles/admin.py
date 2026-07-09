from django.contrib import admin

from .models import BackupTarget, MediaFile, MonitoredDirectory


@admin.register(MonitoredDirectory)
class MonitoredDirectoryAdmin(admin.ModelAdmin):
    list_display = ("name", "path", "is_active", "last_scan_at")
    list_filter = ("is_active",)
    search_fields = ("name", "path")


@admin.register(MediaFile)
class MediaFileAdmin(admin.ModelAdmin):
    list_display = ("path", "import_status", "original_backup_status", "backup_target", "directory", "track", "size_bytes", "mime_type", "duplicate_confidence", "duplicate_reason")
    list_filter = ("import_status", "original_backup_status", "mime_type", "needs_review", "duplicate_reason")
    search_fields = ("path", "source_path", "storage_path", "sha256", "checksum", "track__title", "track__artist__name", "backup_target__name")


@admin.register(BackupTarget)
class BackupTargetAdmin(admin.ModelAdmin):
    list_display = ("name", "backend_type", "is_active", "is_default")
    list_filter = ("backend_type", "is_active", "is_default")
    search_fields = ("name", "aws_bucket", "sftp_host", "sftp_username")
