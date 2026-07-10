from celery import shared_task
from django.conf import settings
from django.utils import timezone

import uuid

from .backup import BackupError, backup_media_file, claim_backup_control, get_backup_control, pending_backup_queryset, release_backup_control
from .models import MediaFile
from .services import cleanup_ready_queryset
from .transcoding import TranscodeError, transcode_media_file_to_opus


@shared_task(bind=True)
def transcode_media_file_task(self, media_file_id: int) -> None:
    media_file = MediaFile.objects.get(id=media_file_id)
    media_file.transcode_status = MediaFile.TranscodeStatus.PROCESSING
    media_file.transcode_error = ""
    media_file.save(update_fields=["transcode_status", "transcode_error", "updated_at"])
    try:
        transcode_media_file_to_opus(media_file=media_file)
    except Exception as exc:
        media_file.transcode_status = MediaFile.TranscodeStatus.FAILED
        media_file.transcode_error = str(exc)
        media_file.save(update_fields=["transcode_status", "transcode_error", "updated_at"])
        raise


@shared_task(bind=True)
def backup_media_file_task(self, media_file_id: int) -> None:
    media_file = MediaFile.objects.get(id=media_file_id)
    try:
        backup_media_file(media_file=media_file)
    except BackupError:
        raise


@shared_task(bind=True)
def process_backup_batch_task(self, media_file_ids: list[int] | None = None) -> dict[str, int]:
    control = get_backup_control()
    if control.active_task_id != self.request.id:
        return {"processed": 0, "failed": 0, "stopped": 0}

    if media_file_ids:
        queryset = MediaFile.objects.filter(id__in=media_file_ids)
    else:
        queryset = pending_backup_queryset()

    processed = 0
    failed = 0
    stopped = 0
    try:
        for media_file in queryset.select_related("backup_target", "track").order_by("discovered_at", "id"):
            control.refresh_from_db(fields=["is_paused", "active_task_id"])
            if control.active_task_id != self.request.id:
                stopped = 1
                break
            if control.is_paused:
                stopped = 1
                break
            try:
                backup_media_file(media_file=media_file)
                processed += 1
            except BackupError:
                media_file.original_backup_status = MediaFile.BackupStatus.FAILED
                media_file.save(update_fields=["original_backup_status", "updated_at"])
                failed += 1
    finally:
        release_backup_control(task_id=self.request.id)

    return {"processed": processed, "failed": failed, "stopped": stopped}


@shared_task(bind=True)
def delete_local_flac_task(self, media_file_id: int) -> None:
    media_file = cleanup_ready_queryset().filter(id=media_file_id).first()
    if media_file is None:
        return
    target_path = media_file.path or media_file.source_path
    if target_path:
        from pathlib import Path

        path = Path(target_path)
        if path.is_file():
            path.unlink()
    media_file.local_deleted_at = timezone.now()
    media_file.save(update_fields=["local_deleted_at", "updated_at"])


def enqueue_pending_transcodes(limit: int | None = None) -> int:
    if settings.TESTING:
        return 0

    queryset = (
        MediaFile.objects.filter(origin_type=MediaFile.OriginType.ORIGINAL, audio_format="flac")
        .exclude(transcode_status=MediaFile.TranscodeStatus.COMPLETED)
        .exclude(transcode_status=MediaFile.TranscodeStatus.PROCESSING)
        .order_by("discovered_at")
    )
    if limit is not None:
        queryset = queryset[:limit]

    queued = 0
    for media_file in queryset:
        media_file.transcode_status = MediaFile.TranscodeStatus.PROCESSING
        media_file.transcode_error = ""
        media_file.save(update_fields=["transcode_status", "transcode_error", "updated_at"])
        try:
            transcode_media_file_task.delay(media_file.id)
            queued += 1
        except Exception:
            media_file.transcode_status = MediaFile.TranscodeStatus.PENDING
            media_file.save(update_fields=["transcode_status", "updated_at"])
    return queued

def start_backup_batch(*, media_file_ids: list[int] | None = None) -> str | None:
    control = get_backup_control()
    if control.is_running:
        control.is_paused = False
        control.save(update_fields=["is_paused", "updated_at"])
        return None

    task_id = uuid.uuid4().hex
    claim_backup_control(task_id=task_id)
    process_backup_batch_task.apply_async(kwargs={"media_file_ids": media_file_ids}, task_id=task_id)
    return task_id