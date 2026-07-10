from celery import shared_task

from .services import scan_monitored_directories


@shared_task(bind=True)
def scan_monitored_directories_task(self, directory_ids: list[int] | None = None) -> dict[str, int]:
    result = scan_monitored_directories(directory_ids=directory_ids)
    return {
        "directories_scanned": result.directories_scanned,
        "files_seen": result.files_seen,
        "files_created": result.files_created,
        "files_updated": result.files_updated,
        "missing_directories": result.missing_directories,
    }
