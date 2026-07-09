from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.library.models import Album, Artist, Track
from apps.mediafiles.models import MediaFile, MonitoredDirectory
from apps.scanner.services import TrackMetadata, scan_directory


class ScanMediaCommandTests(TestCase):
    def test_scan_media_registers_audio_files_only(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "imports" / "pendentes"
            storage_root = Path(temp_dir) / "music"
            root.mkdir(parents=True)
            audio_path = root / "track.mp3"
            ignored_path = root / "cover.txt"
            audio_path.write_bytes(b"audio")
            ignored_path.write_text("not audio", encoding="utf-8")
            directory = MonitoredDirectory.objects.create(name="Library", path=str(root))

            with override_settings(MUSIC_STORAGE_ROOT=storage_root):
                call_command("scan_media", directory_ids=[directory.id])

        self.assertEqual(MediaFile.objects.count(), 1)
        media_file = MediaFile.objects.get()
        self.assertEqual(media_file.source_path, str(audio_path))
        self.assertTrue(media_file.storage_path.startswith("library/"))
        self.assertEqual(media_file.path, str(storage_root / media_file.storage_path))
        self.assertEqual(media_file.import_status, MediaFile.ImportStatus.IMPORTED)
        self.assertEqual(media_file.size_bytes, 5)
        self.assertIsNotNone(media_file.track)
        self.assertEqual(media_file.sha256, media_file.checksum)
        self.assertEqual(media_file.track.acoustic_fingerprint, media_file.acoustic_fingerprint)
        self.assertEqual(media_file.track.acoustic_fingerprint_hash, media_file.acoustic_fingerprint_hash)

    def test_scan_links_exact_duplicate_by_sha256(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "imports" / "pendentes"
            storage_root = Path(temp_dir) / "music"
            root.mkdir(parents=True)
            first_path = root / "first.mp3"
            second_path = root / "second.flac"
            first_path.write_bytes(b"same audio")
            second_path.write_bytes(b"same audio")
            directory = MonitoredDirectory.objects.create(name="Library", path=str(root))

            with override_settings(MUSIC_STORAGE_ROOT=storage_root):
                scan_directory(directory)

        self.assertEqual(Track.objects.count(), 1)
        duplicate = MediaFile.objects.get(duplicate_confidence=100)
        self.assertEqual(duplicate.path, str(second_path))
        self.assertEqual(duplicate.storage_path, "")
        self.assertEqual(duplicate.import_status, MediaFile.ImportStatus.DUPLICATE)
        self.assertEqual(duplicate.duplicate_confidence, 100)
        self.assertEqual(duplicate.duplicate_reason, "sha256")
        self.assertIsNotNone(duplicate.duplicate_of)
        self.assertEqual(duplicate.track_id, duplicate.duplicate_of.track_id)

    def test_scan_links_duplicate_by_strong_recording_identity(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "imports" / "pendentes"
            storage_root = Path(temp_dir) / "music"
            root.mkdir(parents=True)
            first_path = root / "first.mp3"
            second_path = root / "second.mp3"
            first_path.write_bytes(b"first audio")
            second_path.write_bytes(b"second audio")
            directory = MonitoredDirectory.objects.create(name="Library", path=str(root))

            metadata = TrackMetadata(
                title="Song",
                artist_name="Artist",
                isrc="USABC1234567",
                duration_ms=180000,
                acoustic_fingerprint="fp-song",
                acoustic_fingerprint_hash="hash-song",
            )
            with patch("apps.scanner.services.extract_audio_metadata", return_value=metadata):
                with override_settings(MUSIC_STORAGE_ROOT=storage_root):
                    scan_directory(directory)

        self.assertEqual(Track.objects.count(), 1)
        duplicate = MediaFile.objects.get(duplicate_confidence=99)
        self.assertEqual(duplicate.path, str(second_path))
        self.assertEqual(duplicate.storage_path, "")
        self.assertEqual(duplicate.import_status, MediaFile.ImportStatus.DUPLICATE)
        self.assertEqual(duplicate.duplicate_confidence, 99)
        self.assertEqual(duplicate.duplicate_reason, "acoustic_fingerprint")
        self.assertFalse(duplicate.needs_review)

    def test_scan_marks_metadata_match_for_review(self):
        artist = Artist.objects.create(name="Iron Maiden", sort_name="Iron Maiden")
        album = Album.objects.create(artist=artist, title="Fear of the Dark")
        track = Track.objects.create(
            title="Fear of the Dark",
            artist=artist,
            album=album,
            duration_ms=420000,
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "imports" / "pendentes"
            storage_root = Path(temp_dir) / "music"
            root.mkdir(parents=True)
            audio_path = root / "candidate.mp3"
            audio_path.write_bytes(b"candidate audio")
            directory = MonitoredDirectory.objects.create(name="Library", path=str(root))

            metadata = TrackMetadata(
                title="Fear Of Dark",
                artist_name="Iron Maiden",
                album_title="Fear of the Dark",
                duration_ms=421000,
                acoustic_fingerprint="fp-fear-of-the-dark",
                acoustic_fingerprint_hash="hash-fear-of-the-dark",
            )
            with patch("apps.scanner.services.extract_audio_metadata", return_value=metadata):
                with override_settings(MUSIC_STORAGE_ROOT=storage_root):
                    scan_directory(directory)

        media_file = MediaFile.objects.get(path=str(audio_path))
        self.assertEqual(media_file.track_id, track.id)
        self.assertEqual(media_file.storage_path, "")
        self.assertEqual(media_file.import_status, MediaFile.ImportStatus.REVIEW)
        self.assertEqual(media_file.duplicate_confidence, 95)
        self.assertEqual(media_file.duplicate_reason, "metadata_duration")
        self.assertTrue(media_file.needs_review)
