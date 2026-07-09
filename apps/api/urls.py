from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AlbumViewSet,
    ArtistViewSet,
    BackupTargetViewSet,
    MediaFileViewSet,
    MonitoredDirectoryViewSet,
    TrackViewSet,
    api_index,
)

router = DefaultRouter()
router.register("artists", ArtistViewSet)
router.register("albums", AlbumViewSet)
router.register("tracks", TrackViewSet)
router.register("directories", MonitoredDirectoryViewSet)
router.register("backup-targets", BackupTargetViewSet)
router.register("media-files", MediaFileViewSet)

urlpatterns = [
    path("", api_index, name="api-index"),
    path("", include(router.urls)),
]
