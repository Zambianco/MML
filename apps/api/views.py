from django.http import HttpRequest, JsonResponse
from rest_framework import viewsets

from apps.library.models import Album, Artist, Track
from apps.mediafiles.models import MediaFile, MonitoredDirectory

from .serializers import (
    AlbumSerializer,
    ArtistSerializer,
    MediaFileSerializer,
    MonitoredDirectorySerializer,
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
                "media_files": request.build_absolute_uri("media-files/"),
            },
        }
    )


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


class MediaFileViewSet(viewsets.ModelViewSet):
    queryset = MediaFile.objects.select_related("directory", "track", "track__artist")
    serializer_class = MediaFileSerializer
    search_fields = ["path", "checksum", "track__title", "track__artist__name"]
