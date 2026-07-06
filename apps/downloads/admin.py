from django.contrib import admin

from .models import TrackImport, TrackImportItem


class TrackImportItemInline(admin.TabularInline):
    model = TrackImportItem
    extra = 0
    fields = ("row_number", "artists", "name", "album", "year", "isrc", "status")
    readonly_fields = fields
    can_delete = False


@admin.register(TrackImport)
class TrackImportAdmin(admin.ModelAdmin):
    list_display = ("source_name", "delimiter", "item_count", "created_at")
    search_fields = ("source_name",)
    inlines = [TrackImportItemInline]


@admin.register(TrackImportItem)
class TrackImportItemAdmin(admin.ModelAdmin):
    list_display = ("track_import", "row_number", "artists", "name", "album", "year", "isrc", "status")
    list_filter = ("status", "year")
    search_fields = ("artists", "name", "album", "isrc", "search_query")
