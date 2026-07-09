from django.core.management.base import BaseCommand, CommandError

from apps.mediafiles.backup import BackupError, backup_media_file, pending_backup_queryset
from apps.mediafiles.models import BackupTarget, MediaFile


class Command(BaseCommand):
    help = "Upload pending FLAC originals to the configured backup target."

    def add_arguments(self, parser):
        parser.add_argument("--media-file-id", action="append", dest="media_file_ids", type=int)
        parser.add_argument("--target-id", dest="target_id", type=int)

    def handle(self, *args, **options):
        target = None
        if options["target_id"]:
            target = BackupTarget.objects.filter(id=options["target_id"], is_active=True).first()
            if target is None:
                raise CommandError("Backup target nao encontrado ou inativo.")

        queryset = pending_backup_queryset()
        if options["media_file_ids"]:
            queryset = queryset.filter(id__in=options["media_file_ids"])

        processed = 0
        failed = 0
        for media_file in queryset.select_related("backup_target", "track"):
            try:
                backup_media_file(media_file=media_file, target=target)
                processed += 1
            except BackupError as exc:
                media_file.original_backup_status = MediaFile.BackupStatus.FAILED
                media_file.save(update_fields=["original_backup_status", "updated_at"])
                failed += 1
                self.stderr.write(self.style.ERROR(f"{media_file.id}: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"Backups processed: {processed}, failed: {failed}."))
