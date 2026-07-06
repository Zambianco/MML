from django.contrib import admin

from .models import MediaFile, MonitoredDirectory


@admin.register(MonitoredDirectory)
class MonitoredDirectoryAdmin(admin.ModelAdmin):
    list_display = ("name", "path", "is_active", "last_scan_at")
    list_filter = ("is_active",)
    search_fields = ("name", "path")


@admin.register(MediaFile)
class MediaFileAdmin(admin.ModelAdmin):
    list_display = ("path", "directory", "track", "size_bytes", "mime_type", "checksum")
    list_filter = ("mime_type",)
    search_fields = ("path", "checksum", "track__title", "track__artist__name")
