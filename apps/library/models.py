from django.db import models


class Artist(models.Model):
    name = models.CharField(max_length=255, unique=True)
    sort_name = models.CharField(max_length=255, blank=True)
    musicbrainz_id = models.UUIDField(blank=True, null=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_name", "name"]

    def __str__(self) -> str:
        return self.name


class Album(models.Model):
    title = models.CharField(max_length=255)
    artist = models.ForeignKey(Artist, on_delete=models.PROTECT, related_name="albums")
    release_date = models.DateField(blank=True, null=True)
    musicbrainz_id = models.UUIDField(blank=True, null=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["artist__sort_name", "artist__name", "title"]
        constraints = [
            models.UniqueConstraint(fields=["artist", "title"], name="unique_album_per_artist"),
        ]

    def __str__(self) -> str:
        return f"{self.artist} - {self.title}"


class Track(models.Model):
    title = models.CharField(max_length=255)
    artist = models.ForeignKey(Artist, on_delete=models.PROTECT, related_name="tracks")
    album = models.ForeignKey(Album, on_delete=models.PROTECT, related_name="tracks", blank=True, null=True)
    disc_number = models.PositiveSmallIntegerField(default=1)
    track_number = models.PositiveSmallIntegerField(blank=True, null=True)
    duration_ms = models.PositiveIntegerField(blank=True, null=True)
    isrc = models.CharField(max_length=12, blank=True, db_index=True)
    acoustic_fingerprint = models.TextField(blank=True)
    acoustic_fingerprint_hash = models.CharField(max_length=64, blank=True, db_index=True)
    fingerprint_version = models.CharField(max_length=40, blank=True)
    musicbrainz_id = models.UUIDField(blank=True, null=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["album__title", "disc_number", "track_number", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["album", "disc_number", "track_number"],
                name="unique_track_position_per_album",
            ),
            models.UniqueConstraint(
                fields=["isrc"],
                condition=~models.Q(isrc=""),
                name="unique_track_isrc",
            ),
            models.UniqueConstraint(
                fields=["acoustic_fingerprint_hash"],
                condition=~models.Q(acoustic_fingerprint_hash=""),
                name="unique_track_acoustic_fingerprint_hash",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.artist} - {self.title}"
