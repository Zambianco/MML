from pathlib import Path

from django.conf import settings
from django.db.models import Exists, OuterRef, Q, QuerySet

from .models import MediaFile


def preferred_media_file_for_track(track_id: int | None) -> MediaFile | None:
    if track_id is None:
        return None
    preferred = (
        MediaFile.objects.filter(track_id=track_id, origin_type=MediaFile.OriginType.DERIVED, audio_format="opus")
        .order_by("-updated_at")
        .first()
    )
    if preferred is not None:
        return preferred
    return (
        MediaFile.objects.filter(track_id=track_id)
        .order_by("-is_master", "-updated_at")
        .first()
    )


def pending_transcode_queryset() -> QuerySet[MediaFile]:
    return MediaFile.objects.filter(
        origin_type=MediaFile.OriginType.ORIGINAL,
        audio_format="flac",
    ).exclude(transcode_status=MediaFile.TranscodeStatus.COMPLETED)


def cleanup_ready_queryset() -> QuerySet[MediaFile]:
    derived_opus = MediaFile.objects.filter(
        track_id=OuterRef("track_id"),
        origin_type=MediaFile.OriginType.DERIVED,
        audio_format="opus",
    )
    return (
        MediaFile.objects.filter(
            origin_type=MediaFile.OriginType.ORIGINAL,
            audio_format="flac",
            original_backup_status=MediaFile.BackupStatus.CONFIRMED,
        )
        .annotate(has_opus=Exists(derived_opus))
        .filter(has_opus=True)
    )


def media_file_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(settings.MUSIC_STORAGE_ROOT).resolve()).as_posix()
    except ValueError:
        return ""


def preferred_media_paths(paths: list[Path]) -> dict[str, str]:
    if not paths:
        return {}
    path_strings = [str(path) for path in paths]
    media_files = MediaFile.objects.filter(
        Q(path__in=path_strings) | Q(source_path__in=path_strings) | Q(storage_path__in=path_strings),
        track__isnull=False,
    ).select_related("track")
    result: dict[str, str] = {}
    track_ids = {media_file.track_id for media_file in media_files if media_file.track_id}
    for track_id in track_ids:
        preferred = preferred_media_file_for_track(track_id)
        if preferred is None:
            continue
        preferred_path = preferred.path or preferred.source_path
        if not preferred_path:
            continue
        for media_file in media_files:
            if media_file.track_id == track_id:
                for candidate in (media_file.path, media_file.source_path, media_file.storage_path):
                    if candidate and candidate in path_strings:
                        result[candidate] = preferred_path
    return result
