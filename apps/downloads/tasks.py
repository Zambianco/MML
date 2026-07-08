from celery import shared_task
from django.utils import timezone

from .models import TrackImport
from .services import process_download_round


@shared_task(bind=True)
def process_download_round_task(self, track_import_id: int, limit: int = 0) -> dict[str, int]:
    try:
        track_import = TrackImport.objects.get(pk=track_import_id)
    except TrackImport.DoesNotExist:
        return {"searched": 0, "queued": 0, "without_source": 0, "cancelled": 0}

    if not track_import.processing_task_id:
        track_import.processing_task_id = self.request.id or ""
        track_import.save(update_fields=["processing_task_id"])

    def should_cancel() -> bool:
        return TrackImport.objects.filter(pk=track_import_id, cancel_requested_at__isnull=False).exists()

    update_fields = ["processing_finished_at", "processing_last_error"]
    try:
        return process_download_round(track_import, limit=limit, should_cancel=should_cancel)
    except Exception as exc:
        track_import.processing_last_error = str(exc)
        raise
    finally:
        track_import.processing_finished_at = timezone.now()
        track_import.save(update_fields=update_fields)
