from rest_framework import serializers
from django.urls import reverse
from urllib.parse import urlencode

from apps.library.models import Album, Artist, Track
from apps.mediafiles.models import BackupTarget, MediaFile, MonitoredDirectory
from apps.downloads.views import _resolve_local_download_path
from apps.playback.models import FavoriteTrack


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
            "fingerprint_version",
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


class BackupTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackupTarget
        fields = [
            "id",
            "name",
            "backend_type",
            "is_active",
            "is_default",
            "base_path",
            "aws_bucket",
            "aws_region",
            "aws_access_key_id",
            "aws_secret_access_key",
            "aws_endpoint_url",
            "aws_prefix",
            "sftp_host",
            "sftp_port",
            "sftp_username",
            "sftp_password",
            "sftp_private_key",
            "sftp_remote_path",
            "created_at",
            "updated_at",
        ]
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
            "backup_target",
            "path",
            "source_path",
            "storage_path",
            "audio_format",
            "origin_type",
            "is_master",
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


class PlayerTrackSerializer(serializers.Serializer):
    id = serializers.CharField()
    path = serializers.CharField()
    relative_path = serializers.CharField()
    name = serializers.CharField()
    title = serializers.CharField()
    artist = serializers.CharField(allow_blank=True)
    album = serializers.CharField(allow_blank=True)
    year = serializers.CharField(allow_blank=True)
    subtitle = serializers.CharField(allow_blank=True)
    cover_url = serializers.SerializerMethodField()
    stream_url = serializers.SerializerMethodField()
    size = serializers.IntegerField()
    modified_at = serializers.DateTimeField()
    is_favorite = serializers.BooleanField()

    def get_cover_url(self, obj: dict) -> str:
        if not obj.get("cover_url"):
            return ""
        return self._absolute_url(f"{reverse('api-player-cover')}?{urlencode({'path': obj['relative_path']})}")

    def get_stream_url(self, obj: dict) -> str:
        return self._absolute_url(f"{reverse('api-player-stream')}?{urlencode({'path': obj['relative_path']})}")

    def _absolute_url(self, url: str) -> str:
        request = self.context.get("request")
        if not url or request is None:
            return url
        return request.build_absolute_uri(url)


class FavoriteToggleSerializer(serializers.Serializer):
    path = serializers.CharField()
    is_favorite = serializers.BooleanField(required=False, default=True)

    def validate_path(self, value: str) -> str:
        file_path = _resolve_local_download_path(value)
        if file_path is None:
            raise serializers.ValidationError("Arquivo nao encontrado.")
        return str(file_path)

    def save(self, **kwargs) -> FavoriteTrack:
        user = self.context["request"].user
        file_path = self.validated_data["path"]
        should_favorite = self.validated_data["is_favorite"]
        if should_favorite:
            favorite, _ = FavoriteTrack.objects.get_or_create(user=user, file_path=file_path)
            return favorite
        FavoriteTrack.objects.filter(user=user, file_path=file_path).delete()
        return FavoriteTrack(user=user, file_path=file_path)
