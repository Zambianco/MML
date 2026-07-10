import base64
import json
from datetime import datetime
from datetime import timedelta
from functools import lru_cache
import mimetypes
import time
from threading import Thread
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from celery import current_app
from celery.states import PENDING, READY_STATES
from django.contrib import messages
from django.db import close_old_connections
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django.conf import settings
from django.utils import timezone
from urllib.error import URLError

try:
    from mutagen.flac import Picture
except ImportError:  # pragma: no cover - optional dependency during local bootstrap
    Picture = None

from apps.core.audio import stream_audio_file
from apps.mediafiles.models import MediaFile
from apps.mediafiles.services import preferred_media_paths
from apps.playback.models import FavoriteTrack
from apps.scanner.services import AUDIO_EXTENSIONS, MutagenFile

from .forms import TrackImportUploadForm
from .models import TrackImport, TrackImportItem
from .services import (
    create_track_import,
    enqueue_best_available_source,
    refresh_auto_item_search_query,
    recover_stuck_searches,
    sync_item_search_query,
    search_slskd_sources,
    skip_item_download,
    update_download_statuses,
)
from .tasks import process_download_round_task, run_process_download_round

ITEMS_PER_PAGE = 25
PROCESSING_STALE_AFTER = timedelta(hours=1)
PROCESSING_NO_SEARCH_GRACE = timedelta(seconds=30)
LOCAL_TASK_PREFIX = "local-download-round-"
LOCAL_PROCESSING_TASKS: set[str] = set()
LOCAL_COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp", "folder.jpg", "folder.jpeg", "folder.png", "album.jpg", "album.jpeg", "album.png")
MOCK_AUDIO_DATA_URL = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="
DOWNLOADED_FILES_CACHE_SECONDS = 20
_DOWNLOADED_FILES_CACHE: dict[tuple[str, ...], tuple[float, list[dict]]] = {}


def _import_detail_url(track_import: TrackImport, *, page: str | None = None, querystring: str = "") -> str:
    url = reverse("downloads-import-detail", args=[track_import.pk])
    params = []
    if querystring:
        params.append(querystring)
    if page:
        params.append(f"page={page}")
    return f"{url}?{'&'.join(params)}" if params else url


def _item_page_url(track_import: TrackImport, item: TrackImportItem) -> str:
    page = None
    if item.row_number > 0:
        page = str(((item.row_number - 1) // ITEMS_PER_PAGE) + 1)
    return _import_detail_url(track_import, page=page)


def _mark_processing_finished(track_import: TrackImport, *, error: str = "") -> None:
    update_fields: list[str] = []
    if track_import.processing_finished_at is None:
        track_import.processing_finished_at = timezone.now()
        update_fields.append("processing_finished_at")
    if track_import.processing_task_id:
        track_import.processing_task_id = ""
        update_fields.append("processing_task_id")
    if track_import.cancel_requested_at is not None:
        track_import.cancel_requested_at = None
        update_fields.append("cancel_requested_at")
    if track_import.processing_last_error != error:
        track_import.processing_last_error = error
        update_fields.append("processing_last_error")
    if update_fields:
        track_import.save(update_fields=update_fields)


def _celery_workers_available() -> bool:
    try:
        replies = current_app.control.inspect(timeout=1).ping() or {}
    except Exception:
        return False
    return bool(replies)


def _run_local_process_round(track_import_id: int, limit: int, task_id: str) -> None:
    LOCAL_PROCESSING_TASKS.add(task_id)
    close_old_connections()
    try:
        run_process_download_round(track_import_id, limit=limit, task_id=task_id)
    finally:
        LOCAL_PROCESSING_TASKS.discard(task_id)
        close_old_connections()


def _start_local_process_round(track_import: TrackImport, limit: int) -> None:
    task_id = f"{LOCAL_TASK_PREFIX}{uuid4().hex}"
    track_import.processing_task_id = task_id
    track_import.save(update_fields=["processing_task_id"])
    LOCAL_PROCESSING_TASKS.add(task_id)
    thread = Thread(
        target=_run_local_process_round,
        args=(track_import.pk, limit, task_id),
        name=f"download-round-{track_import.pk}",
        daemon=True,
    )
    thread.start()


def _start_process_round_background(track_import: TrackImport, limit: int) -> None:
    if _celery_workers_available():
        try:
            task = process_download_round_task.delay(track_import.pk, limit=limit)
        except Exception:
            pass
        else:
            track_import.processing_task_id = task.id
            track_import.save(update_fields=["processing_task_id"])
            return
    _start_local_process_round(track_import, limit)


def _has_active_search(track_import: TrackImport) -> bool:
    return track_import.items.filter(status=TrackImportItem.STATUS_SEARCHING).exists()


def _refresh_processing_state(track_import: TrackImport) -> None:
    if not track_import.is_processing:
        return

    started_at = track_import.processing_started_at or timezone.now()
    task_id = track_import.processing_task_id
    has_active = _has_active_search(track_import)

    if not task_id:
        if timezone.now() - started_at >= PROCESSING_STALE_AFTER:
            _mark_processing_finished(track_import)
        return

    if task_id.startswith(LOCAL_TASK_PREFIX):
        if task_id not in LOCAL_PROCESSING_TASKS:
            if not has_active and timezone.now() - started_at >= PROCESSING_NO_SEARCH_GRACE:
                _mark_processing_finished(track_import)
            elif has_active and timezone.now() - started_at >= PROCESSING_STALE_AFTER:
                _mark_processing_finished(track_import)
        else:
            if timezone.now() - started_at >= PROCESSING_STALE_AFTER:
                _mark_processing_finished(track_import)
        return

    if not has_active and timezone.now() - started_at < PROCESSING_NO_SEARCH_GRACE:
        return

    try:
        task_state = current_app.AsyncResult(task_id).state
    except Exception:
        if timezone.now() - started_at >= PROCESSING_STALE_AFTER:
            _mark_processing_finished(track_import)
        return

    if task_state in READY_STATES:
        error = ""
        if task_state != "SUCCESS":
            error = f"Rodada anterior encerrada com estado {task_state.lower()}."
        _mark_processing_finished(track_import, error=error)
        return

    if task_state == PENDING:
        if not _celery_workers_available():
            _mark_processing_finished(track_import, error="Nenhum worker Celery disponivel.")
            return
        if not has_active and timezone.now() - started_at >= PROCESSING_NO_SEARCH_GRACE * 2:
            _mark_processing_finished(track_import, error="A tarefa na fila do Celery demorou muito para iniciar.")
            return

    if timezone.now() - started_at >= PROCESSING_STALE_AFTER:
        _mark_processing_finished(track_import)


def _download_roots() -> list[Path]:
    roots: list[Path] = []
    for raw_root in (settings.SLSKD_DOWNLOADS_DIR, settings.MUSIC_STORAGE_ROOT):
        root = Path(raw_root)
        if not root.exists():
            continue
        resolved = root.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _downloaded_files_cache_seconds() -> float:
    return float(getattr(settings, "DOWNLOADED_FILES_CACHE_SECONDS", DOWNLOADED_FILES_CACHE_SECONDS) or 0)


def _copy_file_rows(files: list[dict]) -> list[dict]:
    return [file.copy() for file in files]


def _downloaded_files() -> list[dict]:
    roots = _download_roots()
    cache_key = tuple(str(root) for root in roots)
    cache_seconds = _downloaded_files_cache_seconds()
    now = time.monotonic()
    if cache_seconds > 0:
        cached = _DOWNLOADED_FILES_CACHE.get(cache_key)
        if cached and now - cached[0] <= cache_seconds:
            return _copy_file_rows(cached[1])

    files = _scan_downloaded_files(roots)
    if cache_seconds > 0:
        _DOWNLOADED_FILES_CACHE.clear()
        _DOWNLOADED_FILES_CACHE[cache_key] = (now, _copy_file_rows(files))
    return files


def _scan_downloaded_files(roots: list[Path]) -> list[dict]:
    files: list[dict] = []
    discovered_files: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for root in roots:
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            discovered_files.append((root, resolved))
    persisted_metadata = _persisted_file_metadata([resolved for _, resolved in discovered_files])
    preferred_paths = preferred_media_paths([resolved for _, resolved in discovered_files])
    for root, resolved in discovered_files:
        preferred_path = preferred_paths.get(str(resolved))
        if preferred_path and preferred_path != str(resolved):
            continue
        stat = resolved.stat()
        relative_path = resolved.relative_to(root).as_posix()
        is_audio = resolved.suffix.lower() in AUDIO_EXTENSIONS
        audio_metadata = _cached_audio_display_metadata(str(resolved), stat.st_mtime_ns, stat.st_size) if is_audio else {
            "title": resolved.stem,
            "artist": "",
            "album": "",
            "year": "",
            "missing_metadata_fields": [],
        }
        metadata = persisted_metadata.get(str(resolved)) or audio_metadata
        files.append(
            {
                "absolute_path": str(resolved),
                "root": root,
                "relative_path": relative_path,
                "name": resolved.name,
                "title": metadata["title"],
                "artist": metadata["artist"],
                "album": metadata["album"],
                "year": metadata["year"],
                "subtitle": _build_file_subtitle(metadata, relative_path),
                "cover_url": _build_cover_url(relative_path) if is_audio else "",
                "missing_metadata_fields": audio_metadata["missing_metadata_fields"],
                "has_missing_metadata": bool(audio_metadata["missing_metadata_fields"]),
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone()),
                "stream_url": f"{reverse('downloads-file-stream')}?{urlencode({'path': relative_path})}",
            }
        )
    files.sort(key=lambda item: item["relative_path"].casefold())
    return files


def _favorite_file_paths(request: HttpRequest, files: list[dict]) -> set[str]:
    if not request.user.is_authenticated or not files:
        return set()
    return set(
        FavoriteTrack.objects.filter(
            user=request.user,
            file_path__in=[file["absolute_path"] for file in files],
        ).values_list("file_path", flat=True)
    )


def _filter_downloaded_files(
    files: list[dict],
    *,
    query: str = "",
    extension: str = "",
    root: str = "",
    artist: str = "",
    album: str = "",
    missing_metadata: bool = False,
) -> list[dict]:
    query = query.strip().casefold()
    extension = extension.strip().casefold()
    root = root.strip().casefold()
    artist = artist.strip().casefold()
    album = album.strip().casefold()
    filtered: list[dict] = []
    for file in files:
        if query and all(
            query not in str(file[field]).casefold()
            for field in ("relative_path", "name", "title", "artist", "album", "year", "subtitle")
        ):
            continue
        if extension and file["name"].rpartition(".")[2].casefold() != extension.lstrip("."):
            continue
        if root and str(file["root"]).casefold() != root:
            continue
        if artist and file["artist"].casefold() != artist:
            continue
        if album and file["album"].casefold() != album:
            continue
        if missing_metadata and not file["has_missing_metadata"]:
            continue
        filtered.append(file)
    return filtered


def _mock_player_files() -> list[dict]:
    now = timezone.now()
    return [
        {
            "absolute_path": "__mock__/aurora-drive.wav",
            "root": "__mock__",
            "relative_path": "__mock__/aurora-drive.wav",
            "name": "aurora-drive.wav",
            "title": "Aurora Drive",
            "artist": "Mock Ensemble",
            "album": "UI Test Sessions",
            "year": "2026",
            "subtitle": "Mock Ensemble • UI Test Sessions • 2026",
            "cover_url": "",
            "size": 0,
            "modified_at": now,
            "extension": "wav",
            "stream_url": MOCK_AUDIO_DATA_URL,
            "is_mock": True,
        },
        {
            "absolute_path": "__mock__/night-shift.wav",
            "root": "__mock__",
            "relative_path": "__mock__/night-shift.wav",
            "name": "night-shift.wav",
            "title": "Night Shift",
            "artist": "Mock Ensemble",
            "album": "UI Test Sessions",
            "year": "2026",
            "subtitle": "Mock Ensemble • UI Test Sessions • 2026",
            "cover_url": "",
            "size": 0,
            "modified_at": now,
            "extension": "wav",
            "stream_url": MOCK_AUDIO_DATA_URL,
            "is_mock": True,
        },
        {
            "absolute_path": "__mock__/glass-horizon.wav",
            "root": "__mock__",
            "relative_path": "__mock__/glass-horizon.wav",
            "name": "glass-horizon.wav",
            "title": "Glass Horizon",
            "artist": "Mock Ensemble",
            "album": "UI Test Sessions",
            "year": "2026",
            "subtitle": "Mock Ensemble • UI Test Sessions • 2026",
            "cover_url": "",
            "size": 0,
            "modified_at": now,
            "extension": "wav",
            "stream_url": MOCK_AUDIO_DATA_URL,
            "is_mock": True,
        },
    ]


def _persisted_file_metadata(paths: list[Path]) -> dict[str, dict[str, str]]:
    if not paths:
        return {}
    path_strings = [str(path) for path in paths]
    path_lookup = set(path_strings)
    media_files = (
        MediaFile.objects.filter(
            Q(path__in=path_strings) | Q(source_path__in=path_strings) | Q(storage_path__in=path_strings),
            track__isnull=False,
        )
        .select_related("track__artist", "track__album")
    )
    metadata_by_path: dict[str, dict[str, str]] = {}
    for media_file in media_files:
        track = media_file.track
        if track is None:
            continue
        metadata = {
            "title": track.title,
            "artist": track.artist.name,
            "album": track.album.title if track.album else "Single",
            "year": str(track.album.release_date.year) if track.album and track.album.release_date else "",
        }
        for candidate in (media_file.path, media_file.source_path, media_file.storage_path):
            if candidate and candidate in path_lookup:
                metadata_by_path[candidate] = metadata
    return metadata_by_path


@lru_cache(maxsize=4096)
def _cached_audio_display_metadata(path: str, modified_ns: int, size: int) -> dict:
    return _read_audio_display_metadata(Path(path))


def _read_audio_display_metadata(path: Path) -> dict:
    fallback = {
        "title": path.stem,
        "artist": "Artista desconhecido",
        "album": "Single",
        "year": "",
    }
    if MutagenFile is None:
        return {**fallback, "missing_metadata_fields": ["titulo", "artista", "album", "ano"]}

    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return {**fallback, "missing_metadata_fields": ["titulo", "artista", "album", "ano"]}

    tags = getattr(audio, "tags", None) or {}
    if not tags:
        return {**fallback, "missing_metadata_fields": ["titulo", "artista", "album", "ano"]}

    title = _first_tag(tags, "title")
    artist = _first_tag(tags, "artist", "albumartist")
    album = _first_tag(tags, "album")
    year = _extract_year(tags)

    return {
        "title": title or fallback["title"],
        "artist": artist or fallback["artist"],
        "album": album or fallback["album"],
        "year": year,
        "missing_metadata_fields": _missing_metadata_fields(title=title, artist=artist, album=album, year=year),
    }


def _missing_metadata_fields(*, title: str, artist: str, album: str, year: str) -> list[str]:
    missing_fields: list[str] = []
    if not title:
        missing_fields.append("titulo")
    if not artist:
        missing_fields.append("artista")
    if not album:
        missing_fields.append("album")
    if not year:
        missing_fields.append("ano")
    return missing_fields


def _first_tag(tags: dict, *names: str) -> str:
    for name in names:
        value = tags.get(name)
        if isinstance(value, list):
            for item in value:
                text = str(item).strip()
                if text:
                    return text
        elif value is not None:
            text = str(value).strip()
            if text:
                return text
    return ""


def _extract_year(tags: dict) -> str:
    raw_value = _first_tag(tags, "date", "year", "originaldate")
    if not raw_value:
        return ""
    return raw_value[:4] if len(raw_value) >= 4 else raw_value


def _build_file_subtitle(metadata: dict[str, str], relative_path: str) -> str:
    parts = [metadata["artist"], metadata["album"]]
    if metadata["year"]:
        parts.append(metadata["year"])
    subtitle = " • ".join(part for part in parts if part)
    return subtitle or relative_path


def _build_cover_url(relative_path: str) -> str:
    return f"{reverse('downloads-file-cover')}?{urlencode({'path': relative_path})}"


def _local_cover_candidates(path: Path) -> list[Path]:
    return [path.with_name(name) for name in LOCAL_COVER_NAMES]


def _cover_cache_token(path: Path) -> tuple[tuple[str, int], ...]:
    token = [("audio", path.stat().st_mtime_ns)]
    for candidate in _local_cover_candidates(path):
        if candidate.is_file():
            token.append((candidate.name.casefold(), candidate.stat().st_mtime_ns))
    return tuple(token)


def _image_content_type(data: bytes, fallback: str = "image/jpeg") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def _embedded_cover_asset(path: Path) -> tuple[bytes, str] | None:
    if MutagenFile is None:
        return None
    try:
        audio = MutagenFile(path)
    except Exception:
        return None
    if audio is None:
        return None

    tags = getattr(audio, "tags", None)
    if tags is not None and hasattr(tags, "getall"):
        for frame in tags.getall("APIC"):
            data = getattr(frame, "data", None)
            if data:
                return data, _image_content_type(data, getattr(frame, "mime", "") or "image/jpeg")
        for frame in tags.getall("covr"):
            data = bytes(frame)
            if data:
                return data, _image_content_type(data, "image/jpeg")

    pictures = getattr(audio, "pictures", None) or []
    if pictures:
        picture = pictures[0]
        data = getattr(picture, "data", None)
        if data:
            return data, _image_content_type(data, getattr(picture, "mime", "") or "image/jpeg")

    metadata_block_picture = _metadata_block_picture_cover(tags)
    if metadata_block_picture is not None:
        return metadata_block_picture

    if tags is not None:
        covr = getattr(tags, "get", lambda _key, _default=None: None)("covr")
        if covr:
            data = bytes(covr[0] if isinstance(covr, list) else covr)
            if data:
                return data, _image_content_type(data, "image/jpeg")

    return None


def _metadata_block_picture_cover(tags) -> tuple[bytes, str] | None:
    if Picture is None or tags is None or not hasattr(tags, "get"):
        return None
    values = tags.get("metadata_block_picture") or tags.get("METADATA_BLOCK_PICTURE")
    if not values:
        return None
    if isinstance(values, (str, bytes)):
        values = [values]
    for value in values:
        try:
            picture = Picture(base64.b64decode(value))
        except Exception:
            continue
        data = getattr(picture, "data", None)
        if data:
            return data, _image_content_type(data, getattr(picture, "mime", "") or "image/jpeg")
    return None


def _cover_asset(path: Path) -> tuple[bytes, str] | None:
    for candidate in _local_cover_candidates(path):
        if not candidate.is_file():
            continue
        data = candidate.read_bytes()
        return data, mimetypes.guess_type(candidate.name)[0] or _image_content_type(data)
    return _embedded_cover_asset(path)


@lru_cache(maxsize=256)
def _cached_cover_asset(path_str: str, token: tuple[tuple[str, int], ...]) -> tuple[bytes, str] | None:
    return _cover_asset(Path(path_str))


def _build_import_filters(request: HttpRequest) -> dict[str, str]:
    return {
        "q": str(request.GET.get("q") or "").strip(),
        "status": str(request.GET.get("status") or "").strip(),
        "downloaded": str(request.GET.get("downloaded") or "").strip(),
        "search_attempts": str(request.GET.get("search_attempts") or "").strip(),
    }


def _preserved_import_querystring(request: HttpRequest) -> str:
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def _filter_import_items(items, *, query: str = "", status: str = "", downloaded: str = "", search_attempts: str = ""):
    if query:
        items = items.filter(
            Q(name__icontains=query)
            | Q(artists__icontains=query)
            | Q(album__icontains=query)
            | Q(isrc__icontains=query)
            | Q(search_query__icontains=query)
            | Q(download_path__icontains=query)
        )
    if status:
        items = items.filter(status=status)
    if downloaded == "yes":
        items = items.filter(download_path__gt="")
    elif downloaded == "no":
        items = items.filter(download_path="")
    if search_attempts.isdigit():
        items = items.filter(search_attempts=int(search_attempts))
    return items


def _resolve_local_download_path(download_path: str) -> Path | None:
    if not download_path.strip():
        return None
    normalized_download_path = download_path.strip().replace("\\", "/")
    path = Path(normalized_download_path)
    roots = [Path(settings.SLSKD_DOWNLOADS_DIR), Path(settings.MUSIC_STORAGE_ROOT)]
    if path.is_absolute():
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            resolved = None
        else:
            if resolved.is_file():
                return resolved
        candidates = []
    else:
        candidates = [root / path for root in roots]
        path_parts = path.parts
        if path_parts:
            leading_segment = path_parts[0].casefold()
            trimmed_path = Path(*path_parts[1:]) if len(path_parts) > 1 else None
            if trimmed_path is not None:
                for root in roots:
                    if leading_segment == root.name.casefold():
                        candidates.append(root / trimmed_path)
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            continue
        if not resolved.is_file():
            continue
        for root in roots:
            resolved_root = root.resolve()
            if resolved_root in resolved.parents or resolved == resolved_root:
                return resolved
    filename = Path(normalized_download_path.rsplit("/", 1)[-1]).name
    for root in roots:
        if not root.exists():
            continue
        for candidate in root.rglob(filename):
            if candidate.is_file():
                return candidate.resolve()
    return None


def _import_detail_context(
    track_import: TrackImport,
    *,
    request: HttpRequest | None = None,
    page: str | None = None,
    refresh_status: bool = False,
    filters: dict[str, str] | None = None,
) -> dict:
    track_import = TrackImport.objects.get(pk=track_import.pk)
    if refresh_status:
        try:
            recover_stuck_searches(track_import)
            update_download_statuses(track_import, enqueue_next=False)
        except (URLError, TimeoutError) as exc:
            if request is not None:
                messages.error(request, f"Nao foi possivel conectar ao slskd: {exc.reason}")
        else:
            track_import = TrackImport.objects.get(pk=track_import.pk)
    status_counts = dict(track_import.items.values_list("status").annotate(total=Count("id")))
    queue_remaining_count = (
        status_counts.get(TrackImportItem.STATUS_PENDING, 0)
        + status_counts.get(TrackImportItem.STATUS_SEARCHING, 0)
        + status_counts.get(TrackImportItem.STATUS_ERROR, 0)
    )
    active_search_items = list(
        track_import.items.filter(status=TrackImportItem.STATUS_SEARCHING)
        .order_by("search_started_at", "row_number", "id")[:5]
    )
    active_download_items = list(
        track_import.items.filter(status=TrackImportItem.STATUS_DOWNLOADING)
        .prefetch_related("sources")
        .order_by("download_started_at", "row_number", "id")[:8]
    )
    should_poll = track_import.is_processing or bool(active_search_items) or bool(active_download_items)
    filters = filters or {"q": "", "status": "", "downloaded": "", "search_attempts": ""}
    items = _filter_import_items(
        track_import.items.all(),
        query=filters["q"],
        status=filters["status"],
        downloaded=filters["downloaded"],
        search_attempts=filters["search_attempts"],
    )
    page_obj = Paginator(items, ITEMS_PER_PAGE).get_page(page)
    page_obj.object_list = list(page_obj.object_list.prefetch_related("sources"))
    query_params = {key: value for key, value in filters.items() if value}
    querystring = urlencode(query_params)
    return {
        "track_import": track_import,
        "items": page_obj,
        "page_obj": page_obj,
        "status_counts": status_counts,
        "queue_remaining_count": queue_remaining_count,
        "active_search_items": active_search_items,
        "active_download_items": active_download_items,
        "should_poll": should_poll,
        "query_params": query_params,
        "querystring": querystring,
        "filters": filters,
    }


@require_http_methods(["GET", "POST"])
def import_list(request: HttpRequest) -> HttpResponse:
    form = TrackImportUploadForm()
    if request.method == "POST":
        form = TrackImportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                track_import = create_track_import(form.cleaned_data["file"])
            except ValidationError as exc:
                form.add_error("file", exc.message)
            else:
                messages.success(request, "CSV importado e fila de busca criada.")
                return redirect(reverse("downloads-import-detail", args=[track_import.pk]))

    imports = TrackImport.objects.all()[:20]
    context = {
        "form": form,
        "imports": imports,
    }
    return render(request, "downloads/import_list.html", context)


def download_files(request: HttpRequest) -> HttpResponse:
    roots = _download_roots()
    all_files = _downloaded_files()
    query = str(request.GET.get("q") or "")
    extension = str(request.GET.get("ext") or "")
    root = str(request.GET.get("root") or "")
    missing_metadata = str(request.GET.get("missing_metadata") or "").strip().lower() in {"1", "true", "on"}
    files = _filter_downloaded_files(
        all_files,
        query=query,
        extension=extension,
        root=root,
        missing_metadata=missing_metadata,
    )
    extensions = sorted({f".{file['name'].rpartition('.')[2].casefold()}" for file in all_files if file["name"].rpartition(".")[2]})
    return render(
        request,
        "downloads/download_files.html",
        {
            "download_roots": roots,
            "files": files,
            "file_count": len(files),
            "all_file_count": len(all_files),
            "missing_metadata_count": sum(1 for file in all_files if file["has_missing_metadata"]),
            "extensions": extensions,
            "query": query,
            "selected_missing_metadata": missing_metadata,
            "selected_extension": extension,
            "selected_root": root,
        },
    )


def music_player(request: HttpRequest) -> HttpResponse:
    all_files = _downloaded_files()
    query = str(request.GET.get("q") or "")
    extension = str(request.GET.get("ext") or "")
    root = str(request.GET.get("root") or "")
    artist = str(request.GET.get("artist") or "")
    album = str(request.GET.get("album") or "")
    files = _filter_downloaded_files(all_files, query=query, extension=extension, root=root, artist=artist, album=album)
    if settings.DEBUG and not any((query, extension, root, artist, album)):
        files = files + _mock_player_files()
    elif not files and not any((query, extension, root, artist, album)):
        files = _mock_player_files()
    favorite_paths = _favorite_file_paths(request, files)
    for file in files:
        file["is_favorite"] = file["absolute_path"] in favorite_paths
        file.setdefault("is_mock", False)
    extensions = sorted({f".{file['name'].rpartition('.')[2].casefold()}" for file in all_files if file["name"].rpartition(".")[2]})
    artists = sorted({file["artist"] for file in all_files if file["artist"]}, key=str.casefold)
    albums = sorted({file["album"] for file in all_files if file["album"]}, key=str.casefold)
    return render(
        request,
        "downloads/music_player.html",
        {
            "download_roots": _download_roots(),
            "files": files,
            "file_count": len(files),
            "all_file_count": len(all_files),
            "extensions": extensions,
            "artists": artists,
            "albums": albums,
            "query": query,
            "selected_artist": artist,
            "selected_album": album,
            "selected_extension": extension,
            "selected_root": root,
        },
    )


@require_POST
def toggle_favorite(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    requested_path = str(payload.get("path") or "")
    file_path = _resolve_local_download_path(requested_path)
    if file_path is None:
        return JsonResponse({"error": "file_not_found"}, status=404)
    favorite, created = FavoriteTrack.objects.get_or_create(
        user=request.user,
        file_path=str(file_path),
    )
    if created:
        is_favorite = True
    else:
        favorite.delete()
        is_favorite = False
    return JsonResponse({"is_favorite": is_favorite})


def import_detail(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    _refresh_processing_state(track_import)
    return render(
        request,
        "downloads/import_detail.html",
        _import_detail_context(
            track_import,
            request=request,
            page=request.GET.get("page"),
            refresh_status=True,
            filters=_build_import_filters(request),
        ),
    )


def import_detail_fragment(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    _refresh_processing_state(track_import)
    return render(
        request,
        "downloads/import_detail_fragment.html",
        _import_detail_context(
            track_import,
            request=request,
            page=request.GET.get("page"),
            refresh_status=True,
            filters=_build_import_filters(request),
        ),
    )


@require_http_methods(["POST"])
def process_round(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    _refresh_processing_state(track_import)
    track_import.refresh_from_db()
    if track_import.is_processing and not _has_active_search(track_import):
        _mark_processing_finished(track_import)
        track_import.refresh_from_db()
    limit = int(request.POST.get("limit") or 0)
    if track_import.is_processing:
        messages.warning(request, "Ja existe uma rodada em processamento para esta importacao.")
    else:
        track_import.processing_started_at = timezone.now()
        track_import.processing_finished_at = None
        track_import.cancel_requested_at = None
        track_import.processing_last_error = ""
        track_import.save(
            update_fields=[
                "processing_started_at",
                "processing_finished_at",
                "cancel_requested_at",
                "processing_last_error",
            ]
        )
        _start_process_round_background(track_import, limit)
        messages.success(request, "Rodada enviada para processamento em background.")
    return redirect(_import_detail_url(track_import, page=request.GET.get("page"), querystring=_preserved_import_querystring(request)))


@require_http_methods(["POST"])
def cancel_round(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    _refresh_processing_state(track_import)
    if not track_import.is_processing:
        messages.info(request, "Nao ha rodada em processamento para cancelar.")
    elif track_import.cancel_requested_at is not None:
        messages.info(request, "O cancelamento desta rodada ja foi solicitado.")
    else:
        track_import.cancel_requested_at = timezone.now()
        track_import.save(update_fields=["cancel_requested_at"])
        messages.warning(request, "Cancelamento solicitado. A rodada vai parar no proximo item.")
    return redirect(_import_detail_url(track_import, page=request.GET.get("page"), querystring=_preserved_import_querystring(request)))


@require_http_methods(["POST"])
def refresh_status(request: HttpRequest, pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    try:
        summary = update_download_statuses(track_import)
    except (URLError, TimeoutError) as exc:
        messages.error(request, f"Nao foi possivel conectar ao slskd: {exc.reason}")
    else:
        messages.success(
            request,
            f"Status atualizado: {summary['updated']} item(ns), {summary['done']} concluido(s), {summary['queued_next']} proxima(s) fonte(s).",
        )
    return redirect(_import_detail_url(track_import, page=request.GET.get("page"), querystring=_preserved_import_querystring(request)))


@require_http_methods(["POST"])
def item_search(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    try:
        search_slskd_sources(item)
    except (URLError, TimeoutError) as exc:
        messages.error(request, f"Nao foi possivel conectar ao slskd: {exc.reason}")
    except Exception:
        messages.error(request, "Falha ao buscar fontes no slskd.")
    else:
        messages.success(request, f"Busca concluida para a linha {item.row_number}.")
    return redirect(_import_detail_url(track_import, page=request.GET.get("page"), querystring=_preserved_import_querystring(request)))


@require_http_methods(["POST"])
def item_query(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    action = str(request.POST.get("query_action") or "save")
    if action == "regenerate":
        refresh_auto_item_search_query(item)
        messages.success(request, f"Query regenerada na linha {item.row_number}.")
    else:
        search_query = str(request.POST.get("search_query") or "").strip()
        if not search_query:
            messages.error(request, "Informe uma query.")
        else:
            item.search_query_mode = TrackImportItem.SEARCH_QUERY_MANUAL
            item.search_query = search_query
            item.save(update_fields=["search_query", "search_query_mode", "updated_at"])
            messages.success(request, f"Query atualizada na linha {item.row_number}.")
    return redirect(_import_detail_url(track_import, page=request.GET.get("page"), querystring=_preserved_import_querystring(request)))


@require_http_methods(["POST"])
def item_transfer(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    try:
        source = enqueue_best_available_source(item)
    except (URLError, TimeoutError) as exc:
        messages.error(request, f"Nao foi possivel conectar ao slskd: {exc.reason}")
    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception:
        messages.error(request, "Falha ao enfileirar download no slskd.")
    else:
        messages.success(request, f"Download enfileirado para a linha {item.row_number}: {source.username}.")
    page = request.GET.get("page")
    if not page and item.row_number > 0:
        page = str(((item.row_number - 1) // ITEMS_PER_PAGE) + 1)
    return redirect(_import_detail_url(track_import, page=page, querystring=_preserved_import_querystring(request)))


@require_http_methods(["POST"])
def item_skip_source(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    try:
        source = skip_item_download(item)
    except (URLError, TimeoutError) as exc:
        messages.error(request, f"Nao foi possivel conectar ao slskd: {exc.reason}")
    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception:
        messages.error(request, "Falha ao pular a fonte atual no slskd.")
    else:
        if source is None:
            messages.warning(request, f"Download interrompido na linha {item.row_number}; nao ha proxima fonte ja conhecida.")
        else:
            messages.warning(request, f"Fonte atual pulada na linha {item.row_number}; proxima fonte enfileirada: {source.username}.")
    page = request.GET.get("page")
    if not page and item.row_number > 0:
        page = str(((item.row_number - 1) // ITEMS_PER_PAGE) + 1)
    return redirect(_import_detail_url(track_import, page=page, querystring=_preserved_import_querystring(request)))


def item_download(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    download_reference = item.download_path.strip()
    if not download_reference and item.status == TrackImportItem.STATUS_DONE:
        source = item.sources.order_by("rank", "-score").first()
        download_reference = source.remote_filename if source else ""
    file_path = _resolve_local_download_path(download_reference)
    if file_path is None:
        messages.error(request, "Arquivo ainda nao esta disponivel no disco do sistema.")
        return redirect(_import_detail_url(track_import, page=request.GET.get("page"), querystring=_preserved_import_querystring(request)))
    return FileResponse(file_path.open("rb"), as_attachment=True, filename=file_path.name)


def item_stream(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    track_import = get_object_or_404(TrackImport, pk=pk)
    item = get_object_or_404(TrackImportItem, pk=item_pk, track_import=track_import)
    download_reference = item.download_path.strip()
    if not download_reference and item.status == TrackImportItem.STATUS_DONE:
        source = item.sources.order_by("rank", "-score").first()
        download_reference = source.remote_filename if source else ""
    file_path = _resolve_local_download_path(download_reference)
    if file_path is None:
        return HttpResponse(status=404)
    return stream_audio_file(request, file_path)


def file_stream(request: HttpRequest) -> HttpResponse:
    file_path = _resolve_local_download_path(str(request.GET.get("path") or ""))
    if file_path is None:
        return HttpResponse(status=404)
    return stream_audio_file(request, file_path)


def file_cover(request: HttpRequest) -> HttpResponse:
    file_path = _resolve_local_download_path(str(request.GET.get("path") or ""))
    if file_path is None:
        return HttpResponse(status=404)
    cover_asset = _cached_cover_asset(str(file_path), _cover_cache_token(file_path))
    if cover_asset is None:
        return HttpResponse(status=404)
    data, content_type = cover_asset
    return HttpResponse(data, content_type=content_type)
