from django.core.management.base import BaseCommand

from apps.mediafiles.models import MediaFile
from apps.mediafiles.transcoding import TranscodeError, transcode_media_file_to_opus


class Command(BaseCommand):
    help = "Transcode FLAC originals to Opus."

    def add_arguments(self, parser):
        parser.add_argument("--media-file-id", action="append", dest="media_file_ids", type=int)

    def handle(self, *args, **options):
        queryset = MediaFile.objects.filter(origin_type=MediaFile.OriginType.ORIGINAL, audio_format="flac")
        if options["media_file_ids"]:
            queryset = queryset.filter(id__in=options["media_file_ids"])

        processed = 0
        failed = 0
        for media_file in queryset:
            try:
                transcode_media_file_to_opus(media_file=media_file)
                processed += 1
            except TranscodeError as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"{media_file.id}: {exc}"))
        self.stdout.write(self.style.SUCCESS(f"Transcodes processed: {processed}, failed: {failed}."))
