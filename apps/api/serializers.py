from rest_framework import serializers

from apps.library.models import Album, Artist, Track
from apps.mediafiles.models import MediaFile, MonitoredDirectory


class ArtistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Artist
        fields = ["id", "name", "sort_name", "musicbrainz_id", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class AlbumSerializer(serializers.ModelSerializer):
    artist_name = serializers.CharField(source="artist.name", read_only=True)

    class Meta:
        model = Album
        fields = ["id", "title", "artist", "artist_name", "release_date", "musicbrainz_id", "created_at", "updated_at"]
        read_only_fields = ["id", "artist_name", "created_at", "updated_at"]


class TrackSerializer(serializers.ModelSerializer):
    artist_name = serializers.CharField(source="artist.name", read_only=True)
    album_title = serializers.CharField(source="album.title", read_only=True)

    class Meta:
        model = Track
        fields = [
            "id",
            "title",
            "artist",
            "artist_name",
            "album",
            "album_title",
            "disc_number",
            "track_number",
            "duration_ms",
            "isrc",
            "acoustic_fingerprint",
            "acoustic_fingerprint_hash",
            "musicbrainz_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "artist_name", "album_title", "created_at", "updated_at"]


class MonitoredDirectorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MonitoredDirectory
        fields = ["id", "name", "path", "is_active", "last_scan_at", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class MediaFileSerializer(serializers.ModelSerializer):
    directory_name = serializers.CharField(source="directory.name", read_only=True)
    track_title = serializers.CharField(source="track.title", read_only=True)

    class Meta:
        model = MediaFile
        fields = [
            "id",
            "directory",
            "directory_name",
            "track",
            "track_title",
            "path",
            "source_path",
            "storage_path",
            "original_backup_path",
            "original_backup_status",
            "original_backup_sha256",
            "original_backed_up_at",
            "size_bytes",
            "mime_type",
            "checksum",
            "sha256",
            "duration_ms",
            "bitrate_kbps",
            "acoustic_fingerprint",
            "acoustic_fingerprint_hash",
            "duplicate_of",
            "duplicate_confidence",
            "duplicate_reason",
            "needs_review",
            "import_status",
            "discovered_at",
            "updated_at",
        ]
        read_only_fields = ["id", "directory_name", "track_title", "discovered_at", "updated_at"]
