from celery import shared_task

from .backup import BackupError, backup_media_file
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
