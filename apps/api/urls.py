from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AlbumViewSet,
    ArtistViewSet,
    BackupTargetViewSet,
    AndroidTokenView,
    FavoriteListView,
    MediaFileViewSet,
    MonitoredDirectoryViewSet,
    PlayerCoverView,
    PlayerStreamView,
    PlayerTrackListView,
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
    path("auth/token/", AndroidTokenView.as_view(), name="api-auth-token"),
    path("player/tracks/", PlayerTrackListView.as_view(), name="api-player-tracks"),
    path("player/favorites/", FavoriteListView.as_view(), name="api-player-favorites"),
    path("player/stream/", PlayerStreamView.as_view(), name="api-player-stream"),
    path("player/cover/", PlayerCoverView.as_view(), name="api-player-cover"),
    path("", include(router.urls)),
]
