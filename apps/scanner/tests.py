from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from apps.mediafiles.models import MediaFile, MonitoredDirectory


class ScanMediaCommandTests(TestCase):
    def test_scan_media_registers_audio_files_only(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_path = root / "track.mp3"
            ignored_path = root / "cover.txt"
            audio_path.write_bytes(b"audio")
            ignored_path.write_text("not audio", encoding="utf-8")
            directory = MonitoredDirectory.objects.create(name="Library", path=str(root))

            call_command("scan_media", directory_ids=[directory.id])

        self.assertEqual(MediaFile.objects.count(), 1)
        media_file = MediaFile.objects.get()
        self.assertEqual(media_file.path, str(audio_path))
        self.assertEqual(media_file.size_bytes, 5)
