from dataclasses import dataclass
from pathlib import Path

from django.utils import timezone

from apps.mediafiles.models import MediaFile, MonitoredDirectory


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

        files_seen += 1
        media_file, created = MediaFile.objects.update_or_create(
            path=str(path),
            defaults={
                "directory": directory,
                "size_bytes": path.stat().st_size,
            },
        )
        if created:
            files_created += 1
        elif media_file.size_bytes == path.stat().st_size:
            files_updated += 1

    directory.last_scan_at = timezone.now()
    directory.save(update_fields=["last_scan_at", "updated_at"])

    return ScanResult(
        directories_scanned=1,
        files_seen=files_seen,
        files_created=files_created,
        files_updated=files_updated,
    )
