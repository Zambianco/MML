from django.core.management.base import BaseCommand

from apps.scanner.services import scan_monitored_directories


class Command(BaseCommand):
    help = "Scan active monitored directories and register discovered audio files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--directory-id",
            action="append",
            dest="directory_ids",
            type=int,
            help="Scan only a specific monitored directory. Can be used multiple times.",
        )

    def handle(self, *args, **options):
        result = scan_monitored_directories(directory_ids=options["directory_ids"])
        self.stdout.write(
            self.style.SUCCESS(
                "Scanned {directories_scanned} directories, found {files_seen} audio files, "
                "created {files_created}, updated {files_updated}, missing {missing_directories}."
            ).format(
                directories_scanned=result.directories_scanned,
                files_seen=result.files_seen,
                files_created=result.files_created,
                files_updated=result.files_updated,
                missing_directories=result.missing_directories,
            )
        )
