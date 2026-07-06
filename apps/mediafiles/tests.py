from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase
from django.urls import reverse

from .models import MediaFile, MonitoredDirectory


class LibraryDashboardTests(TestCase):
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
        self.assertTrue(MediaFile.objects.filter(path=str(audio_path)).exists())
