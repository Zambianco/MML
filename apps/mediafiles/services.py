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
            local_deleted_at__isnull=True,
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
    path_lookup = set(path_strings)
    media_files = list(
        MediaFile.objects.filter(
            Q(path__in=path_strings) | Q(source_path__in=path_strings) | Q(storage_path__in=path_strings),
            track__isnull=False,
        ).select_related("track")
    )
    result: dict[str, str] = {}
    media_files_by_track: dict[int, list[MediaFile]] = {}
    for media_file in media_files:
        if media_file.track_id:
            media_files_by_track.setdefault(media_file.track_id, []).append(media_file)
    if not media_files_by_track:
        return result

    preferred_by_track: dict[int, MediaFile] = {}
    fallback_by_track: dict[int, MediaFile] = {}
    for candidate in MediaFile.objects.filter(track_id__in=media_files_by_track.keys()).order_by(
        "track_id", "-updated_at"
    ):
        if not candidate.track_id:
            continue
        if candidate.origin_type == MediaFile.OriginType.DERIVED and candidate.audio_format == "opus":
            preferred_by_track.setdefault(candidate.track_id, candidate)
            continue
        fallback = fallback_by_track.get(candidate.track_id)
        if fallback is None or (candidate.is_master and not fallback.is_master):
            fallback_by_track[candidate.track_id] = candidate

    for track_id, track_media_files in media_files_by_track.items():
        preferred = preferred_by_track.get(track_id) or fallback_by_track.get(track_id)
        if preferred is None:
            continue
        preferred_path = preferred.path or preferred.source_path
        if not preferred_path:
            continue
        for media_file in track_media_files:
            for candidate in (media_file.path, media_file.source_path, media_file.storage_path):
                if candidate and candidate in path_lookup:
                    result[candidate] = preferred_path
    return result
