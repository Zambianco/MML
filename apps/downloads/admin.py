from django.contrib import admin

from .models import TrackImport, TrackImportItem, TrackImportItemSource


class TrackImportItemInline(admin.TabularInline):
    model = TrackImportItem
    extra = 0
    fields = ("row_number", "artists", "name", "album", "year", "isrc", "status", "download_progress", "download_path")
    readonly_fields = fields
    can_delete = False


@admin.register(TrackImport)
class TrackImportAdmin(admin.ModelAdmin):
    list_display = ("source_name", "delimiter", "item_count", "created_at")
    search_fields = ("source_name",)
    inlines = [TrackImportItemInline]


@admin.register(TrackImportItem)
class TrackImportItemAdmin(admin.ModelAdmin):
    list_display = ("track_import", "row_number", "artists", "name", "album", "year", "isrc", "status", "download_progress")
    list_filter = ("status", "year")
    search_fields = ("artists", "name", "album", "isrc", "search_query")


@admin.register(TrackImportItemSource)
class TrackImportItemSourceAdmin(admin.ModelAdmin):
    list_display = ("item", "rank", "username", "extension", "score", "queue_length", "download_state")
    list_filter = ("extension", "download_state")
    search_fields = ("username", "remote_filename", "item__name", "item__artists")
