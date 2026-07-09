from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import MediaFile, MonitoredDirectory


class LibraryDashboardTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="tester", password="secret123")
        self.client.force_login(user)

    def test_dashboard_loads(self):
        response = self.client.get(reverse("library-dashboard"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Biblioteca")

    def test_create_directory(self):
        response = self.client.post(
            reverse("create-directory"),
            {"name": "Local", "path": "/music", "is_active": "on"},
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("library-dashboard"))
        self.assertTrue(MonitoredDirectory.objects.filter(name="Local", path="/music").exists())

    def test_scan_directories_registers_audio_file(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_path = root / "song.flac"
            audio_path.write_bytes(b"audio")
            MonitoredDirectory.objects.create(name="Local", path=str(root))

            response = self.client.post(reverse("scan-directories"), HTTP_HOST="localhost")

        self.assertRedirects(response, reverse("library-dashboard"))
        self.assertTrue(MediaFile.objects.filter(source_path=str(audio_path)).exists())

    def test_media_file_stream_serves_audio_inline(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_path = root / "song.mp3"
            audio_path.write_bytes(b"audio")
            directory = MonitoredDirectory.objects.create(name="Local", path=str(root))
            media_file = MediaFile.objects.create(
                directory=directory,
                path=str(audio_path),
                source_path=str(audio_path),
                mime_type="audio/mpeg",
            )

            response = self.client.get(reverse("media-file-stream", args=[media_file.pk]), HTTP_HOST="localhost")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"audio")
            self.assertEqual(response.headers["Content-Type"], "audio/mpeg")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")

    def test_media_file_allows_original_backup_metadata(self):
        directory = MonitoredDirectory.objects.create(name="Local", path="/music")
        backed_up_at = timezone.now()

        media_file = MediaFile.objects.create(
            directory=directory,
            path="/music/library/song.flac",
            source_path="/imports/song.flac",
            storage_path="library/song.flac",
            original_backup_path="s3://archive/song.flac",
            original_backup_status="confirmed",
            original_backup_sha256="a" * 64,
            original_backed_up_at=backed_up_at,
        )

        self.assertEqual(media_file.original_backup_path, "s3://archive/song.flac")
        self.assertEqual(media_file.original_backup_status, "confirmed")
        self.assertEqual(media_file.original_backup_sha256, "a" * 64)
        self.assertEqual(media_file.original_backed_up_at, backed_up_at)
