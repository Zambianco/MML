from django.http import HttpRequest, JsonResponse
from django.http import HttpResponse
from rest_framework import permissions, status, viewsets
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audio import stream_audio_file
from apps.downloads.views import (
    _cached_cover_asset,
    _cover_cache_token,
    _downloaded_files,
    _favorite_file_paths,
    _filter_downloaded_files,
    _resolve_local_download_path,
)
from apps.library.models import Album, Artist, Track
from apps.mediafiles.models import BackupTarget, MediaFile, MonitoredDirectory

from .serializers import (
    AlbumSerializer,
    ArtistSerializer,
    BackupTargetSerializer,
    MediaFileSerializer,
    MonitoredDirectorySerializer,
    FavoriteToggleSerializer,
    PlayerTrackSerializer,
    TrackSerializer,
)


def api_index(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "name": "my-music-library",
            "stage": "library-api",
            "status": "ok",
            "resources": {
                "artists": request.build_absolute_uri("artists/"),
                "albums": request.build_absolute_uri("albums/"),
                "tracks": request.build_absolute_uri("tracks/"),
                "directories": request.build_absolute_uri("directories/"),
                "backup_targets": request.build_absolute_uri("backup-targets/"),
                "media_files": request.build_absolute_uri("media-files/"),
                "player_tracks": request.build_absolute_uri("player/tracks/"),
                "favorites": request.build_absolute_uri("player/favorites/"),
                "auth_token": request.build_absolute_uri("auth/token/"),
            },
        }
    )


class AndroidTokenView(ObtainAuthToken):
    permission_classes = [permissions.AllowAny]


class PlayerTrackListView(APIView):
    def get(self, request: HttpRequest) -> Response:
        files = _filter_downloaded_files(
            _downloaded_files(),
            query=str(request.GET.get("q") or ""),
            extension=str(request.GET.get("ext") or ""),
            root=str(request.GET.get("root") or ""),
            artist=str(request.GET.get("artist") or ""),
            album=str(request.GET.get("album") or ""),
        )
        favorite_paths = _favorite_file_paths(request, files)
        tracks = []
        for file in files:
            row = file.copy()
            row["id"] = row["relative_path"]
            row["path"] = row["absolute_path"]
            row["is_favorite"] = row["absolute_path"] in favorite_paths
            tracks.append(row)
        serializer = PlayerTrackSerializer(tracks, many=True, context={"request": request})
        return Response(serializer.data)


class FavoriteListView(APIView):
    def get(self, request: HttpRequest) -> Response:
        files = _downloaded_files()
        favorite_paths = _favorite_file_paths(request, files)
        tracks = []
        for file in files:
            if file["absolute_path"] not in favorite_paths:
                continue
            row = file.copy()
            row["id"] = row["relative_path"]
            row["path"] = row["absolute_path"]
            row["is_favorite"] = True
            tracks.append(row)
        serializer = PlayerTrackSerializer(tracks, many=True, context={"request": request})
        return Response(serializer.data)

    def post(self, request: HttpRequest) -> Response:
        serializer = FavoriteToggleSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {
                "path": serializer.validated_data["path"],
                "is_favorite": serializer.validated_data["is_favorite"],
            },
            status=status.HTTP_200_OK,
        )


class PlayerStreamView(APIView):
    def get(self, request: HttpRequest) -> HttpResponse:
        file_path = _resolve_local_download_path(str(request.GET.get("path") or ""))
        if file_path is None:
            return HttpResponse(status=404)
        return stream_audio_file(request, file_path)


class PlayerCoverView(APIView):
    def get(self, request: HttpRequest) -> HttpResponse:
        file_path = _resolve_local_download_path(str(request.GET.get("path") or ""))
        if file_path is None:
            return HttpResponse(status=404)
        cover_asset = _cached_cover_asset(str(file_path), _cover_cache_token(file_path))
        if cover_asset is None:
            return HttpResponse(status=404)
        data, content_type = cover_asset
        return HttpResponse(data, content_type=content_type)


class ArtistViewSet(viewsets.ModelViewSet):
    queryset = Artist.objects.all()
    serializer_class = ArtistSerializer
    search_fields = ["name", "sort_name", "musicbrainz_id"]


class AlbumViewSet(viewsets.ModelViewSet):
    queryset = Album.objects.select_related("artist")
    serializer_class = AlbumSerializer
    search_fields = ["title", "artist__name", "musicbrainz_id"]


class TrackViewSet(viewsets.ModelViewSet):
    queryset = Track.objects.select_related("artist", "album")
    serializer_class = TrackSerializer
    search_fields = ["title", "artist__name", "album__title", "musicbrainz_id"]


class MonitoredDirectoryViewSet(viewsets.ModelViewSet):
    queryset = MonitoredDirectory.objects.all()
    serializer_class = MonitoredDirectorySerializer
    search_fields = ["name", "path"]


class BackupTargetViewSet(viewsets.ModelViewSet):
    queryset = BackupTarget.objects.all()
    serializer_class = BackupTargetSerializer
    search_fields = ["name", "backend_type", "aws_bucket", "sftp_host"]


class MediaFileViewSet(viewsets.ModelViewSet):
    queryset = MediaFile.objects.select_related("directory", "track", "track__artist")
    serializer_class = MediaFileSerializer
    search_fields = ["path", "source_path", "storage_path", "checksum", "sha256", "track__title", "track__artist__name"]
