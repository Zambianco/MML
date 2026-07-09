from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import mimetypes
from pathlib import Path
import re
import shutil
from uuid import UUID

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.library.models import Album, Artist, Track
from apps.mediafiles.models import MediaFile, MonitoredDirectory

try:
    import acoustid
except ImportError:  # pragma: no cover - optional dependency during local bootstrap
    acoustid = None

try:
    from mutagen import File as MutagenFile
except ImportError:  # pragma: no cover - optional dependency during local bootstrap
    MutagenFile = None


AUDIO_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}


@dataclass(frozen=True)
class ScanResult:
    directories_scanned: int = 0
    files_seen: int = 0
    files_created: int = 0
    files_updated: int = 0
    missing_directories: int = 0


@dataclass(frozen=True)
class TrackMetadata:
    title: str = ""
    artist_name: str = ""
    album_title: str = ""
    disc_number: int = 1
    track_number: int | None = None
    duration_ms: int | None = None
    bitrate_kbps: int | None = None
    mime_type: str = ""
    isrc: str = ""
    musicbrainz_id: UUID | None = None
    acoustic_fingerprint: str = ""
    acoustic_fingerprint_hash: str = ""


@dataclass(frozen=True)
class DuplicateDecision:
    track: Track | None = None
    duplicate_of: MediaFile | None = None
    confidence: int = 0
    reason: str = ""
    needs_review: bool = False


def scan_monitored_directories(directory_ids: list[int] | None = None) -> ScanResult:
    queryset = MonitoredDirectory.objects.filter(is_active=True)
    if directory_ids:
        queryset = queryset.filter(id__in=directory_ids)

    totals = {
        "directories_scanned": 0,
        "files_seen": 0,
        "files_created": 0,
        "files_updated": 0,
        "missing_directories": 0,
    }

    for directory in queryset:
        result = scan_directory(directory)
        totals["directories_scanned"] += result.directories_scanned
        totals["files_seen"] += result.files_seen
        totals["files_created"] += result.files_created
        totals["files_updated"] += result.files_updated
        totals["missing_directories"] += result.missing_directories

    return ScanResult(**totals)


def scan_directory(directory: MonitoredDirectory) -> ScanResult:
    root = Path(directory.path)
    if not root.is_dir():
        return ScanResult(missing_directories=1)

    files_seen = 0
    files_created = 0
    files_updated = 0

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue

        sha256 = calculate_sha256(path)
        metadata = extract_audio_metadata(path)
        decision = classify_duplicate(path=path, metadata=metadata, sha256=sha256)
        track = decision.track or find_or_create_track(path=path, metadata=metadata)
        import_status = determine_import_status(decision)
        source_path = str(path)
        storage_path = ""
        current_path = path
        if import_status == MediaFile.ImportStatus.IMPORTED:
            current_path, storage_path = move_to_library_storage(path=path, sha256=sha256)
        size_bytes = current_path.stat().st_size
        files_seen += 1
        media_file, created = MediaFile.objects.update_or_create(
            path=str(current_path),
            defaults={
                "directory": directory,
                "track": track,
                "duplicate_of": decision.duplicate_of,
                "source_path": source_path,
                "storage_path": storage_path,
                "size_bytes": size_bytes,
                "mime_type": metadata.mime_type,
                "checksum": sha256,
                "sha256": sha256,
                "duration_ms": metadata.duration_ms,
                "bitrate_kbps": metadata.bitrate_kbps,
                "acoustic_fingerprint": metadata.acoustic_fingerprint,
                "acoustic_fingerprint_hash": metadata.acoustic_fingerprint_hash,
                "duplicate_confidence": decision.confidence,
                "duplicate_reason": decision.reason,
                "needs_review": decision.needs_review,
                "import_status": import_status,
            },
        )
        if created:
            files_created += 1
        elif media_file.size_bytes == size_bytes:
            files_updated += 1

    directory.last_scan_at = timezone.now()
    directory.save(update_fields=["last_scan_at", "updated_at"])

    return ScanResult(
        directories_scanned=1,
        files_seen=files_seen,
        files_created=files_created,
        files_updated=files_updated,
    )


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as audio_file:
        for chunk in iter(lambda: audio_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def determine_import_status(decision: DuplicateDecision) -> str:
    if decision.needs_review:
        return MediaFile.ImportStatus.REVIEW
    if decision.confidence >= 99:
        return MediaFile.ImportStatus.DUPLICATE
    return MediaFile.ImportStatus.IMPORTED


def move_to_library_storage(*, path: Path, sha256: str) -> tuple[Path, str]:
    relative_path = canonical_library_path(sha256=sha256, suffix=path.suffix)
    target_path = Path(settings.MUSIC_STORAGE_ROOT) / relative_path
    if path.resolve() == target_path.resolve():
        return target_path, relative_path.as_posix()

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not target_path.exists():
        shutil.move(str(path), str(target_path))
        return target_path, relative_path.as_posix()

    return path, ""


def canonical_library_path(*, sha256: str, suffix: str) -> Path:
    extension = suffix.lower() or ".bin"
    return Path("library") / sha256[:2] / f"{sha256}{extension}"


def extract_audio_metadata(path: Path) -> TrackMetadata:
    mime_type = mimetypes.guess_type(path.name)[0] or ""
    acoustic_fingerprint, fingerprint_duration_ms = calculate_acoustic_fingerprint(path)
    if MutagenFile is None:
        return TrackMetadata(
            title=path.stem,
            duration_ms=fingerprint_duration_ms,
            mime_type=mime_type,
            acoustic_fingerprint=acoustic_fingerprint,
            acoustic_fingerprint_hash=hash_identity(acoustic_fingerprint),
        )

    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        audio = None

    if audio is None:
        return TrackMetadata(
            title=path.stem,
            duration_ms=fingerprint_duration_ms,
            mime_type=mime_type,
            acoustic_fingerprint=acoustic_fingerprint,
            acoustic_fingerprint_hash=hash_identity(acoustic_fingerprint),
        )

    tags = getattr(audio, "tags", None) or {}
    info = getattr(audio, "info", None)
    duration_ms = int(info.length * 1000) if getattr(info, "length", None) else fingerprint_duration_ms
    bitrate_kbps = int(info.bitrate / 1000) if getattr(info, "bitrate", None) else None

    return TrackMetadata(
        title=_first_tag(tags, "title") or path.stem,
        artist_name=_first_tag(tags, "artist", "albumartist"),
        album_title=_first_tag(tags, "album"),
        disc_number=_parse_number(_first_tag(tags, "discnumber")) or 1,
        track_number=_parse_number(_first_tag(tags, "tracknumber")),
        duration_ms=duration_ms,
        bitrate_kbps=bitrate_kbps,
        mime_type=mime_type,
        isrc=_clean_isrc(_first_tag(tags, "isrc")),
        musicbrainz_id=_parse_uuid(_first_tag(tags, "musicbrainz_trackid", "musicbrainz_releasetrackid")),
        acoustic_fingerprint=acoustic_fingerprint,
        acoustic_fingerprint_hash=hash_identity(acoustic_fingerprint),
    )


def calculate_acoustic_fingerprint(path: Path) -> tuple[str, int | None]:
    if acoustid is None:
        return "", None

    try:
        duration, fingerprint = acoustid.fingerprint_file(str(path), force_fpcalc=True)
    except acoustid.FingerprintGenerationError:
        return "", None

    duration_ms = int(float(duration) * 1000) if duration else None
    return str(fingerprint or ""), duration_ms


def hash_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def classify_duplicate(*, path: Path, metadata: TrackMetadata, sha256: str) -> DuplicateDecision:
    exact_file = (
        MediaFile.objects.filter(Q(sha256=sha256) | Q(checksum=sha256), track__isnull=False)
        .exclude(path=str(path))
        .select_related("track")
        .first()
    )
    if exact_file:
        return DuplicateDecision(track=exact_file.track, duplicate_of=exact_file, confidence=100, reason="sha256")

    track = find_track_by_strong_identity(metadata)
    if track:
        duplicate_of = track.media_files.order_by("discovered_at").first()
        reason = "acoustic_fingerprint" if metadata.acoustic_fingerprint_hash else "recording_id"
        return DuplicateDecision(track=track, duplicate_of=duplicate_of, confidence=99, reason=reason)

    candidate = find_metadata_candidate(metadata)
    if candidate:
        duplicate_of = candidate.track.media_files.order_by("discovered_at").first()
        return DuplicateDecision(
            track=candidate.track,
            duplicate_of=duplicate_of,
            confidence=candidate.confidence,
            reason=candidate.reason,
            needs_review=True,
        )

    return DuplicateDecision()


def find_or_create_track(*, path: Path, metadata: TrackMetadata) -> Track:
    track = find_track_by_exact_metadata(metadata)
    if track:
        return track

    artist_name = metadata.artist_name or "Unknown Artist"
    artist, _ = Artist.objects.get_or_create(name=artist_name, defaults={"sort_name": artist_name})
    album = None
    if metadata.album_title:
        album, _ = Album.objects.get_or_create(artist=artist, title=metadata.album_title)

    return Track.objects.create(
        title=metadata.title or path.stem,
        artist=artist,
        album=album,
        disc_number=metadata.disc_number,
        track_number=metadata.track_number,
        duration_ms=metadata.duration_ms,
        isrc=metadata.isrc,
        musicbrainz_id=metadata.musicbrainz_id,
        acoustic_fingerprint=metadata.acoustic_fingerprint,
        acoustic_fingerprint_hash=metadata.acoustic_fingerprint_hash,
    )


def find_track_by_strong_identity(metadata: TrackMetadata) -> Track | None:
    if metadata.acoustic_fingerprint_hash:
        track = Track.objects.filter(acoustic_fingerprint_hash=metadata.acoustic_fingerprint_hash).first()
        if track:
            return track

    if metadata.musicbrainz_id:
        track = Track.objects.filter(musicbrainz_id=metadata.musicbrainz_id).first()
        if track:
            return track

    if metadata.isrc:
        track = Track.objects.filter(isrc=metadata.isrc).first()
        if track:
            return track

    return None


def find_track_by_exact_metadata(metadata: TrackMetadata) -> Track | None:
    if metadata.album_title and metadata.track_number:
        track = Track.objects.filter(
            album__title=metadata.album_title,
            album__artist__name=metadata.artist_name or "Unknown Artist",
            disc_number=metadata.disc_number,
            track_number=metadata.track_number,
        ).first()
        if track:
            return track

    if metadata.artist_name and metadata.title and metadata.album_title:
        return Track.objects.filter(
            artist__name=metadata.artist_name,
            title=metadata.title,
            album__title=metadata.album_title,
        ).first()

    return None


@dataclass(frozen=True)
class MetadataCandidate:
    track: Track
    confidence: int
    reason: str


def find_metadata_candidate(metadata: TrackMetadata) -> MetadataCandidate | None:
    if not metadata.artist_name or not metadata.title:
        return None

    candidates = Track.objects.select_related("artist", "album").filter(artist__name__iexact=metadata.artist_name)[:50]
    best: MetadataCandidate | None = None

    for track in candidates:
        title_score = text_similarity(metadata.title, track.title)
        artist_score = text_similarity(metadata.artist_name, track.artist.name)
        album_score = text_similarity(metadata.album_title, track.album.title) if metadata.album_title and track.album else 0
        duration_close = durations_close(metadata.duration_ms, track.duration_ms)

        confidence = 0
        reason = ""
        if title_score >= 0.80 and artist_score >= 0.92 and album_score >= 0.85 and duration_close:
            confidence = 95
            reason = "metadata_duration"
        elif title_score >= 0.78 and artist_score >= 0.88:
            confidence = 80
            reason = "metadata_similarity"

        if confidence and (best is None or confidence > best.confidence):
            best = MetadataCandidate(track=track, confidence=confidence, reason=reason)

    return best


def text_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def durations_close(left: int | None, right: int | None) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= 2000


def _first_tag(tags, *names: str) -> str:
    for name in names:
        value = tags.get(name)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if value:
            return str(value).strip()
    return ""


def _parse_number(value: str) -> int | None:
    if not value:
        return None
    number = value.split("/", 1)[0].strip()
    return int(number) if number.isdigit() else None


def _parse_uuid(value: str) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _clean_isrc(value: str) -> str:
    return value.replace(" ", "").replace("-", "").upper()[:12]
