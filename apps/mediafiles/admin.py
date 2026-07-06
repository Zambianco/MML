from django.contrib import admin

from .models import MediaFile, MonitoredDirectory


@admin.register(MonitoredDirectory)
class MonitoredDirectoryAdmin(admin.ModelAdmin):
    list_display = ("name", "path", "is_active", "last_scan_at")
    list_filter = ("is_active",)
    search_fields = ("name", "path")


@admin.register(MediaFile)
class MediaFileAdmin(admin.ModelAdmin):
    list_display = ("path", "import_status", "directory", "track", "size_bytes", "mime_type", "duplicate_confidence", "duplicate_reason")
    list_filter = ("import_status", "mime_type", "needs_review", "duplicate_reason")
    search_fields = ("path", "source_path", "storage_path", "sha256", "checksum", "track__title", "track__artist__name")
