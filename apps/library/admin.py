from django.contrib import admin

from .models import Album, Artist, Track


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_name", "musicbrainz_id")
    search_fields = ("name", "sort_name", "musicbrainz_id")


@admin.register(Album)
class AlbumAdmin(admin.ModelAdmin):
    list_display = ("title", "artist", "release_date", "musicbrainz_id")
    list_filter = ("release_date",)
    search_fields = ("title", "artist__name", "musicbrainz_id")


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ("title", "artist", "album", "disc_number", "track_number", "duration_ms", "isrc")
    list_filter = ("disc_number",)
    search_fields = ("title", "artist__name", "album__title", "isrc", "acoustic_fingerprint_hash", "musicbrainz_id")
