import csv
from datetime import timedelta
import io
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import TrackImport, TrackImportItem, TrackImportItemSource

REQUIRED_HEADERS = {"name", "artists"}
HEADER_ALIASES = {
    "nome": "name",
    "artista": "artists",
    "artistas": "artists",
    "album": "album",
    "ano": "year",
    "isrc": "isrc",
}
ISRC_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
MAX_SOURCES_PER_ITEM = 10
SEARCH_STATUS_INTERVAL_SECONDS = 5
SEARCH_TIMEOUT_SECONDS = 90
ROUND_IDLE_SLEEP_SECONDS = 10
DOWNLOAD_STALE_TIMEOUT = 6 * 60 * 60


@dataclass
class ParsedImportRow:
    row_number: int
    name: str
    artists: str
    album: str
    year: int | None
    isrc: str
    search_query: str


@dataclass
class ParsedImportFile:
    delimiter: str
    rows: list[ParsedImportRow]


def _normalize_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return normalized.strip().lower()


def _detect_delimiter(content: str) -> str:
    header_line = content.splitlines()[0] if content.splitlines() else ""
    return ";" if header_line.count(";") >= header_line.count(",") else ","


def _quote_term(value: str) -> str:
    return f'"{value}"' if any(char.isspace() for char in value) or "," in value else value


def build_slskd_search_query(*, name: str, artists: str, album: str = "", year: int | None = None, isrc: str = "") -> str:
    terms: list[str] = []
    for value in (artists.strip(), name.strip(), album.strip()):
        if value and value not in terms:
            terms.append(value)
    query_parts = [_quote_term(value) for value in terms]
    if year is not None:
        query_parts.append(str(year))
    return " ".join(query_parts)


def _slskd_request(method: str, path: str, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    urls = [f"{settings.SLSKD_BASE_URL}{path}"]

    last_error = None
    for url in urls:
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "X-API-Key": settings.SLSKD_API_KEY,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=settings.SLSKD_REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read()
                return json.loads(body.decode("utf-8")) if body else None
        except (URLError, TimeoutError) as exc:
            last_error = exc

    raise last_error


def _clean_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()


def score_slskd_source(response: dict, file_data: dict, item: TrackImportItem) -> float:
    extension = str(file_data.get("extension") or "").lower()
    filename = str(file_data.get("filename") or "")
    title_similarity = SequenceMatcher(None, _clean_text(item.name), _clean_text(filename)).ratio()
    score = title_similarity * 5000

    if extension == "flac":
        score += 10000
    elif extension == "wav":
        score += 9000
    elif extension == "mp3":
        score += 5000

    score += (file_data.get("bitDepth") or 0) * 10
    score += (file_data.get("sampleRate") or 0) / 100
    score += (response.get("uploadSpeed") or 0) / 1_000_000
    score -= response.get("queueLength") or 0

    if response.get("hasFreeUploadSlot"):
        score += 100
    if file_data.get("isLocked"):
        score -= 10000
    if title_similarity < 0.35:
        score -= 2500

    filename_clean = _clean_text(filename)
    for penalty in ("live", "instrumental", "karaoke", "tribute", "cover", "remix", "demo", "radio edit"):
        if penalty in filename_clean:
            score -= 500

    return round(score, 2)


def get_item_search_query(item: TrackImportItem) -> str:
    if item.search_query_mode == TrackImportItem.SEARCH_QUERY_MANUAL and item.search_query.strip():
        return item.search_query
    return build_slskd_search_query(
        name=item.name,
        artists=item.artists,
        album=item.album,
        year=item.year,
    )


def refresh_auto_item_search_query(item: TrackImportItem) -> str:
    search_query = build_slskd_search_query(
        name=item.name,
        artists=item.artists,
        album=item.album,
        year=item.year,
    )
    if item.search_query != search_query or item.search_query_mode != TrackImportItem.SEARCH_QUERY_AUTO:
        item.search_query = search_query
        item.search_query_mode = TrackImportItem.SEARCH_QUERY_AUTO
        item.save(update_fields=["search_query", "search_query_mode", "updated_at"])
    return search_query


def sync_item_search_query(item: TrackImportItem) -> str:
    if item.search_query_mode == TrackImportItem.SEARCH_QUERY_MANUAL and item.search_query.strip():
        return item.search_query
    return refresh_auto_item_search_query(item)


def search_slskd_sources(
    item: TrackImportItem,
    max_sources: int = MAX_SOURCES_PER_ITEM,
    keep_search: bool = True,
    should_cancel: Callable[[], bool] | None = None,
) -> list[TrackImportItemSource]:
    search_query = sync_item_search_query(item)

    search = _slskd_request("POST", "/api/v0/searches", {"searchText": search_query})
    search_id = search["id"]
    started_at = time.monotonic()
    item.search_attempts += 1
    item.search_slskd_id = str(search_id)
    item.search_state = "Started"
    item.search_response_count = 0
    item.search_started_at = timezone.now()
    item.search_finished_at = None
    item.last_error = ""
    item.save(
        update_fields=[
            "search_attempts",
            "search_slskd_id",
            "search_state",
            "search_response_count",
            "search_started_at",
            "search_finished_at",
            "last_error",
            "updated_at",
        ]
    )

    cancelled = False
    try:
        while True:
            if should_cancel is not None and should_cancel():
                cancelled = True
                break
            status = _slskd_request("GET", f"/api/v0/searches/{search_id}") or {}
            item.search_state = str(status.get("state") or "")
            item.search_response_count = int(status.get("responseCount") or 0)
            item.save(update_fields=["search_state", "search_response_count", "updated_at"])
            if status.get("isComplete") or time.monotonic() - started_at >= SEARCH_TIMEOUT_SECONDS:
                break
            time.sleep(SEARCH_STATUS_INTERVAL_SECONDS)

        if cancelled:
            item.search_state = "Cancelled"
            item.search_finished_at = timezone.now()
            item.save(update_fields=["search_state", "search_finished_at", "updated_at"])
            return []

        responses = _slskd_request("GET", f"/api/v0/searches/{search_id}/responses") or []
        item.search_finished_at = timezone.now()
        item.save(update_fields=["search_finished_at", "updated_at"])
    finally:
        if cancelled or not keep_search:
            _slskd_request("DELETE", f"/api/v0/searches/{search_id}")

    return _save_ranked_sources(item, responses, max_sources=max_sources)


def _save_ranked_sources(item: TrackImportItem, responses: list[dict], max_sources: int = MAX_SOURCES_PER_ITEM) -> list[TrackImportItemSource]:
    ranked: list[tuple[float, dict, dict]] = []
    for response in responses:
        for file_data in response.get("files", []):
            ranked.append((score_slskd_source(response, file_data, item), response, file_data))
    ranked.sort(key=lambda source: source[0], reverse=True)

    sources: list[TrackImportItemSource] = []
    for rank, (score, response, file_data) in enumerate(ranked[:max_sources], start=1):
        source, _ = TrackImportItemSource.objects.update_or_create(
            item=item,
            username=str(response.get("username") or ""),
            remote_filename=str(file_data.get("filename") or ""),
            size_bytes=file_data.get("size"),
            defaults={
                "rank": rank,
                "extension": str(file_data.get("extension") or "").lower(),
                "sample_rate": file_data.get("sampleRate"),
                "bit_depth": file_data.get("bitDepth"),
                "duration_seconds": file_data.get("length"),
                "score": score,
                "queue_length": response.get("queueLength") or 0,
                "upload_speed": response.get("uploadSpeed") or 0,
            },
        )
        sources.append(source)

    return sources


def enqueue_source(source: TrackImportItemSource) -> None:
    now = timezone.now()
    payload = [{"filename": source.remote_filename}]
    if source.size_bytes is not None:
        payload[0]["size"] = source.size_bytes
    _slskd_request("POST", f"/api/v0/transfers/downloads/{quote(source.username, safe='')}", payload)
    source.mark_requested()
    source.item.status = TrackImportItem.STATUS_DOWNLOADING
    source.item.download_progress = 0
    source.item.download_path = source.remote_filename
    source.item.download_started_at = now
    source.item.download_progress_updated_at = now
    source.item.last_error = ""
    source.item.save(
        update_fields=[
            "status",
            "download_progress",
            "download_path",
            "download_started_at",
            "download_progress_updated_at",
            "last_error",
            "updated_at",
        ]
    )


def search_and_enqueue_item(item: TrackImportItem) -> TrackImportItemSource:
    sources = search_slskd_sources(item)
    source = sources[0] if sources else None
    if source is None:
        raise ValueError("Nenhuma fonte encontrada no slskd.")
    enqueue_source(source)
    return source


def enqueue_best_available_source(item: TrackImportItem) -> TrackImportItemSource:
    source = item.sources.filter(download_requested_at__isnull=True).order_by("rank", "-score").first()
    if source is None:
        source = search_and_enqueue_item(item)
        return source
    enqueue_source(source)
    return source


def _is_stuck_search(item: TrackImportItem, now=None) -> bool:
    if item.status != TrackImportItem.STATUS_SEARCHING:
        return False
    if item.search_finished_at is not None:
        return True
    normalized_state = str(item.search_state or "").casefold()
    if "completed" in normalized_state or "timedout" in normalized_state or "responselimitreached" in normalized_state:
        return True
    if item.search_started_at is None:
        return False
    now = now or timezone.now()
    return item.search_started_at <= now - timedelta(seconds=SEARCH_TIMEOUT_SECONDS)


def recover_stuck_search_item(item: TrackImportItem) -> str:
    if not _is_stuck_search(item):
        return "skipped"

    next_source = item.sources.filter(download_requested_at__isnull=True).order_by("rank", "-score").first()
    if next_source is None and item.search_slskd_id:
        search_id = quote(str(item.search_slskd_id), safe="")
        status = _slskd_request("GET", f"/api/v0/searches/{search_id}") or {}
        item.search_state = str(status.get("state") or item.search_state or "")
        item.search_response_count = int(status.get("responseCount") or item.search_response_count or 0)
        item.search_finished_at = item.search_finished_at or timezone.now()
        item.save(update_fields=["search_state", "search_response_count", "search_finished_at", "updated_at"])
        responses = _slskd_request("GET", f"/api/v0/searches/{search_id}/responses") or []
        sources = _save_ranked_sources(item, responses)
        next_source = next((source for source in sources if source.download_requested_at is None), None)

    if next_source is not None:
        enqueue_source(next_source)
        return "queued"

    requested_source = item.sources.filter(download_requested_at__isnull=False).order_by("rank", "-score").first()
    if requested_source is not None:
        now = timezone.now()
        item.status = TrackImportItem.STATUS_DOWNLOADING
        item.download_path = item.download_path or requested_source.remote_filename
        item.download_started_at = item.download_started_at or requested_source.download_requested_at or now
        item.download_progress_updated_at = item.download_progress_updated_at or item.download_started_at
        item.last_error = ""
        item.save(
            update_fields=[
                "status",
                "download_path",
                "download_started_at",
                "download_progress_updated_at",
                "last_error",
                "updated_at",
            ]
        )
        return "downloading"

    item.status = TrackImportItem.STATUS_ERROR
    item.last_error = "Busca finalizada sem fonte enfileiravel no slskd."
    item.search_finished_at = item.search_finished_at or timezone.now()
    item.save(update_fields=["status", "last_error", "search_finished_at", "updated_at"])
    return "failed"


def recover_stuck_searches(track_import: TrackImport, limit: int = 20) -> dict[str, int]:
    summary = {"queued": 0, "downloading": 0, "failed": 0}
    candidates = list(
        track_import.items.filter(status=TrackImportItem.STATUS_SEARCHING)
        .order_by("search_started_at", "row_number", "id")[:limit]
    )
    for item in candidates:
        result = recover_stuck_search_item(item)
        if result in summary:
            summary[result] += 1
    return summary


def skip_item_download(item: TrackImportItem) -> TrackImportItemSource | None:
    if item.status != TrackImportItem.STATUS_DOWNLOADING:
        raise ValueError("Este item nao esta baixando.")

    requested_sources = list(
        item.sources.filter(download_requested_at__isnull=False).order_by("rank", "-score")
    )
    downloads = _download_index()
    skipped_source = None
    skipped_state = ""

    for source in requested_sources:
        transfer = downloads.get((source.username.casefold(), source.remote_filename.casefold()))
        if transfer is None:
            continue
        state = str(transfer.get("stateDescription") or transfer.get("state") or "")
        if _slskd_item_status(state) == TrackImportItem.STATUS_DOWNLOADING:
            _cancel_download_transfer(transfer)
            skipped_source = source
            skipped_state = state
            break

    if skipped_source is None and requested_sources:
        normalized_path = item.download_path.strip().replace("\\", "/")
        skipped_source = next(
            (
                source
                for source in requested_sources
                if source.remote_filename.strip().replace("\\", "/") == normalized_path
                or Path(source.remote_filename).name == Path(normalized_path).name
            ),
            requested_sources[0],
        )
        skipped_state = skipped_source.download_state

    if skipped_source is not None:
        skipped_source.download_state = f"{skipped_state} | skipped-manual".strip(" |")
        skipped_source.save(update_fields=["download_state", "updated_at"])

    next_source = item.sources.filter(download_requested_at__isnull=True).order_by("rank", "-score").first()
    if next_source is not None:
        enqueue_source(next_source)
        return next_source

    item.status = TrackImportItem.STATUS_ERROR
    item.last_error = "Download interrompido manualmente; nao ha proxima fonte ja encontrada."
    item.save(update_fields=["status", "last_error", "updated_at"])
    return None


def _download_index() -> dict[tuple[str, str], dict]:
    index = {}
    for user_data in _slskd_request("GET", "/api/v0/transfers/downloads") or []:
        username = str(user_data.get("username") or "").strip().casefold()
        for directory in user_data.get("directories") or []:
            directory_path = str(
                directory.get("directory")
                or directory.get("path")
                or directory.get("name")
                or ""
            ).strip()
            for file_data in directory.get("files") or []:
                filename = str(file_data.get("filename") or "")
                if username and filename:
                    transfer_data = dict(file_data)
                    transfer_data["username"] = username
                    if directory_path:
                        transfer_data["_directory_path"] = directory_path
                    index[(username, filename.casefold())] = transfer_data
    return index


def _build_download_path_from_transfer(transfer: dict) -> str:
    filename = str(transfer.get("filename") or "").strip().replace("\\", "/")
    directory_path = str(transfer.get("_directory_path") or "").strip().replace("\\", "/")
    if not directory_path:
        return filename
    filename_only = Path(filename).name
    if not filename_only:
        return directory_path
    return str(Path(directory_path) / filename_only).replace("\\", "/")


def _cancel_download_transfer(transfer: dict) -> bool:
    username = str(transfer.get("username") or "").strip()
    transfer_id = transfer.get("id")
    if not username or transfer_id in (None, ""):
        return False
    _slskd_request("DELETE", f"/api/v0/transfers/downloads/{quote(username, safe='')}/{quote(str(transfer_id), safe='')}")
    return True


def _is_stale_download(item: TrackImportItem, status: str, progress: int, now, fallback_at=None) -> bool:
    if status != TrackImportItem.STATUS_DOWNLOADING:
        return False
    if progress != item.download_progress:
        return False
    last_progress_at = item.download_progress_updated_at or item.download_started_at or fallback_at
    if last_progress_at is None:
        return False
    return (now - last_progress_at).total_seconds() >= DOWNLOAD_STALE_TIMEOUT


def _slskd_item_status(state: str) -> str:
    normalized = re.sub(r"[^a-z]+", "", str(state or "").lower())
    if normalized == "completedsucceeded":
        return TrackImportItem.STATUS_DONE
    if "reject" in normalized or "cancel" in normalized or "skip" in normalized:
        return TrackImportItem.STATUS_ERROR
    if normalized.startswith("completed"):
        return TrackImportItem.STATUS_ERROR
    if normalized in {"inprogress", "initializing"} or normalized.startswith("queued") or normalized in {"requested", "none"}:
        return TrackImportItem.STATUS_DOWNLOADING
    return TrackImportItem.STATUS_DOWNLOADING


def update_download_statuses(track_import: TrackImport, enqueue_next: bool = True) -> dict[str, int]:
    summary = {"updated": 0, "done": 0, "failed": 0, "queued_next": 0}
    requested_sources = list(
        TrackImportItemSource.objects.filter(
            item__track_import=track_import,
            download_requested_at__isnull=False,
        )
        .exclude(item__status=TrackImportItem.STATUS_DONE)
        .select_related("item")
        .order_by("item_id", "rank", "-score")
    )
    if not requested_sources:
        return summary

    downloads = _download_index()
    source_updates: dict[int, list[tuple[TrackImportItemSource, dict | None, str, int, bool]]] = {}

    for source in requested_sources:
        transfer = downloads.get((source.username.casefold(), source.remote_filename.casefold()))
        now = timezone.now()
        if transfer:
            state = transfer.get("stateDescription") or transfer.get("state") or ""
            status = _slskd_item_status(state)
            progress = int(float(transfer.get("percentComplete") or 0))
            stale_download = _is_stale_download(
                source.item,
                status,
                progress,
                now,
                fallback_at=source.download_requested_at,
            )
            if stale_download and _cancel_download_transfer(transfer):
                status = TrackImportItem.STATUS_ERROR
                state = f"{state} | stale-cancelled".strip(" |")
        else:
            state = source.download_state
            status = _slskd_item_status(state)
            if status == TrackImportItem.STATUS_DONE:
                progress = 100
            elif status == TrackImportItem.STATUS_ERROR:
                progress = source.item.download_progress
            else:
                progress = source.item.download_progress
                stale_download = _is_stale_download(
                    source.item,
                    status,
                    progress,
                    now,
                    fallback_at=source.download_requested_at,
                )
                if not stale_download:
                    continue
                status = TrackImportItem.STATUS_ERROR
                state = f"{state} | stale-missing".strip(" |")
            stale_download = status == TrackImportItem.STATUS_ERROR and "stale-" in state

        source.download_state = str(state)
        source.save(update_fields=["download_state", "updated_at"])
        source_updates.setdefault(source.item_id, []).append((source, transfer, status, progress, stale_download))

    for entries in source_updates.values():
        done_entry = next((entry for entry in entries if entry[2] == TrackImportItem.STATUS_DONE), None)
        if done_entry is not None:
            source, transfer, status, progress, _stale_download = done_entry
        else:
            active_entries = [entry for entry in entries if entry[2] == TrackImportItem.STATUS_DOWNLOADING]
            if active_entries:
                if active_entries[0][0].item.status == TrackImportItem.STATUS_DONE:
                    continue
                source, transfer, status, progress, _stale_download = sorted(
                    active_entries,
                    key=lambda entry: (-entry[3], entry[0].rank),
                )[0]
            else:
                source, transfer, status, progress, _stale_download = entries[0]

        if source.item.status == TrackImportItem.STATUS_DONE and status != TrackImportItem.STATUS_DONE:
            continue

        now = timezone.now()
        previous_progress = source.item.download_progress
        source.item.status = status
        source.item.download_progress = 100 if status == TrackImportItem.STATUS_DONE else progress
        source.item.last_error = ""
        update_fields = ["status", "download_progress", "last_error", "updated_at"]
        if status == TrackImportItem.STATUS_DOWNLOADING and progress != previous_progress:
            source.item.download_progress_updated_at = now
            update_fields.insert(2, "download_progress_updated_at")
        if status == TrackImportItem.STATUS_DONE:
            resolved_download_path = _build_download_path_from_transfer(transfer) if transfer else source.remote_filename
            if resolved_download_path:
                source.item.download_path = resolved_download_path
                update_fields.insert(2, "download_path")
            source.item.download_progress_updated_at = now
            if "download_progress_updated_at" not in update_fields:
                update_fields.insert(2, "download_progress_updated_at")
        elif status == TrackImportItem.STATUS_ERROR and _stale_download:
            source.item.last_error = "Download interrompido apos 6 horas sem mudanca de progresso."
        source.item.save(update_fields=update_fields)
        summary["updated"] += 1

        if status == TrackImportItem.STATUS_DONE:
            summary["done"] += 1
        elif status == TrackImportItem.STATUS_ERROR:
            summary["failed"] += 1
            next_source = source.item.sources.filter(download_requested_at__isnull=True).order_by("rank", "-score").first()
            if enqueue_next:
                if next_source is None:
                    next_sources = search_slskd_sources(source.item)
                    next_source = next((candidate for candidate in next_sources if candidate.download_requested_at is None), None)
                if next_source is not None:
                    enqueue_source(next_source)
                    summary["queued_next"] += 1

    return summary


def _round_items_queryset(track_import: TrackImport, *, include_errors: bool = True):
    statuses = [
        TrackImportItem.STATUS_PENDING,
        TrackImportItem.STATUS_SEARCHING,
    ]
    if include_errors:
        statuses.append(TrackImportItem.STATUS_ERROR)
    return track_import.items.filter(status__in=statuses).order_by("search_attempts", "row_number", "id")


def _process_round_items(items, should_cancel: Callable[[], bool] | None = None) -> dict[str, int]:
    summary = {"searched": 0, "queued": 0, "without_source": 0, "cancelled": 0}
    for item in items:
        if should_cancel is not None and should_cancel():
            summary["cancelled"] = 1
            break

        item.status = TrackImportItem.STATUS_SEARCHING
        item.save(update_fields=["status", "updated_at"])
        try:
            if should_cancel is None:
                sources = search_slskd_sources(item)
            else:
                sources = search_slskd_sources(item, should_cancel=should_cancel)
        except Exception as exc:
            item.status = TrackImportItem.STATUS_ERROR
            item.last_error = f"Falha ao buscar no slskd: {exc}"
            item.save(update_fields=["status", "last_error", "updated_at"])
            summary["without_source"] += 1
            continue
        summary["searched"] += 1

        if should_cancel is not None and should_cancel():
            item.status = TrackImportItem.STATUS_PENDING
            item.save(update_fields=["status", "updated_at"])
            summary["cancelled"] = 1
            break

        source = sources[0] if sources else None
        if not source:
            item.status = TrackImportItem.STATUS_ERROR
            item.last_error = "Nenhuma fonte encontrada no slskd."
            item.save(update_fields=["status", "last_error", "updated_at"])
            summary["without_source"] += 1
            continue

        try:
            enqueue_source(source)
        except Exception as exc:
            item.status = TrackImportItem.STATUS_ERROR
            item.last_error = f"Falha ao enfileirar download no slskd: {exc}"
            item.save(update_fields=["status", "last_error", "updated_at"])
            summary["without_source"] += 1
            continue
        summary["queued"] += 1

    return summary


def _import_done_count(track_import: TrackImport) -> int:
    return track_import.items.filter(status=TrackImportItem.STATUS_DONE).count()


def process_download_round(track_import: TrackImport, limit: int = 0, should_cancel: Callable[[], bool] | None = None) -> dict[str, int]:
    recovered = recover_stuck_searches(track_import)
    recovered_queued = recovered["queued"] + recovered["downloading"]
    recovered_failed = recovered["failed"]
    if limit > 0:
        summary = _process_round_items(_round_items_queryset(track_import)[:limit], should_cancel=should_cancel)
        summary["queued"] += recovered_queued
        summary["without_source"] += recovered_failed
        return summary

    summary = {"searched": 0, "queued": 0, "without_source": 0, "cancelled": 0}
    summary["queued"] += recovered_queued
    summary["without_source"] += recovered_failed
    total_items = track_import.item_count or track_import.items.count()
    while _import_done_count(track_import) < total_items:
        if should_cancel is not None and should_cancel():
            summary["cancelled"] = 1
            break

        batch_summary = _process_round_items(
            list(_round_items_queryset(track_import, include_errors=True)),
            should_cancel=should_cancel,
        )
        for key, value in batch_summary.items():
            summary[key] += value
        if batch_summary["cancelled"]:
            break
        try:
            update_download_statuses(track_import)
        except Exception:
            pass
        if _import_done_count(track_import) < total_items:
            time.sleep(ROUND_IDLE_SLEEP_SECONDS)

    return summary


def parse_track_import_csv(uploaded_file) -> ParsedImportFile:
    try:
        content = uploaded_file.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("Nao foi possivel ler o CSV em UTF-8.") from exc

    if not content.strip():
        raise ValidationError("O arquivo CSV esta vazio.")

    delimiter = _detect_delimiter(content)
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValidationError("O CSV precisa ter cabecalho.")

    normalized_headers: dict[str, str] = {}
    for header in reader.fieldnames:
        canonical = HEADER_ALIASES.get(_normalize_header(header))
        if canonical:
            normalized_headers[header] = canonical

    missing_headers = REQUIRED_HEADERS.difference(normalized_headers.values())
    if missing_headers:
        raise ValidationError("O CSV precisa conter as colunas Nome e Artista(s).")

    rows: list[ParsedImportRow] = []
    for index, raw_row in enumerate(reader, start=2):
        row = {normalized_headers[key]: (value or "").strip() for key, value in raw_row.items() if key in normalized_headers}
        name = row.get("name", "")
        artists = row.get("artists", "")
        album = row.get("album", "")
        year_value = row.get("year", "")
        isrc = row.get("isrc", "").replace(" ", "").replace("-", "").upper()

        if not name or not artists:
            raise ValidationError(f"Linha {index}: Nome e Artista(s) sao obrigatorios.")

        year: int | None = None
        if year_value:
            if not year_value.isdigit() or len(year_value) != 4:
                raise ValidationError(f"Linha {index}: Ano precisa ter 4 digitos.")
            year = int(year_value)

        if isrc and not ISRC_PATTERN.fullmatch(isrc):
            raise ValidationError(f"Linha {index}: ISRC invalido.")

        rows.append(
            ParsedImportRow(
                row_number=index,
                name=name,
                artists=artists,
                album=album,
                year=year,
                isrc=isrc,
                search_query=build_slskd_search_query(
                    name=name,
                    artists=artists,
                    album=album,
                    year=year,
                    isrc=isrc,
                ),
            )
        )

    if not rows:
        raise ValidationError("O CSV nao possui linhas de musica.")

    return ParsedImportFile(delimiter=delimiter, rows=rows)


@transaction.atomic
def create_track_import(uploaded_file) -> TrackImport:
    parsed_file = parse_track_import_csv(uploaded_file)
    track_import = TrackImport.objects.create(
        source_name=uploaded_file.name,
        delimiter=parsed_file.delimiter,
        item_count=len(parsed_file.rows),
    )
    TrackImportItem.objects.bulk_create(
        [
            TrackImportItem(
                track_import=track_import,
                row_number=row.row_number,
                name=row.name,
                artists=row.artists,
                album=row.album,
                year=row.year,
                isrc=row.isrc,
                search_query=row.search_query,
                search_query_mode=TrackImportItem.SEARCH_QUERY_AUTO,
            )
            for row in parsed_file.rows
        ]
    )
    return track_import
