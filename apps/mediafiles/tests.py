import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from mutagen.flac import Picture

from .backup import S3BackupStorage, build_backup_key, calculate_remote_sha256, ensure_sftp_directory, pending_backup_queryset, reconcile_backup_manifest
from .models import BackupControl, BackupTarget, MediaFile, MonitoredDirectory
from .transcoding import copy_flac_metadata_and_cover_to_opus


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

    def test_create_s3_backup_target(self):
        response = self.client.post(
            reverse("create-backup-target"),
            {
                "name": "AWS",
                "backend_type": "s3",
                "is_active": "on",
                "is_default": "on",
                "base_path": "flac/",
                "aws_bucket": "mml-backup",
                "aws_region": "us-east-1",
                "aws_access_key_id": "abc",
                "aws_secret_access_key": "secret",
                "aws_prefix": "originais/",
            },
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("library-dashboard"))
        self.assertTrue(BackupTarget.objects.filter(name="AWS", backend_type="s3", aws_bucket="mml-backup").exists())

    def test_create_sftp_backup_target_requires_connection_fields(self):
        response = self.client.post(
            reverse("create-backup-target"),
            {
                "name": "Casa",
                "backend_type": "sftp",
                "is_active": "on",
            },
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("library-dashboard"))
        self.assertFalse(BackupTarget.objects.filter(name="Casa").exists())

    def test_build_backup_key_uses_sha256_structure(self):
        directory = MonitoredDirectory.objects.create(name="Local", path="/music")
        media_file = MediaFile.objects.create(
            directory=directory,
            path="/music/library/song.flac",
            sha256="ab" + ("c" * 62),
            audio_format="flac",
            origin_type=MediaFile.OriginType.ORIGINAL,
        )

        key = build_backup_key(media_file=media_file, base_path="originais")

        self.assertEqual(key, f"/originais/ab/{media_file.sha256}.flac")

    def test_scan_directories_registers_audio_file(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_path = root / "song.flac"
            audio_path.write_bytes(b"audio")
            MonitoredDirectory.objects.create(name="Local", path=str(root))

            with patch("apps.mediafiles.views.scan_monitored_directories_task.delay") as delay_mock:
                response = self.client.post(reverse("scan-directories"), HTTP_HOST="localhost")

        self.assertRedirects(response, reverse("library-dashboard"))
        delay_mock.assert_called_once_with()
        self.assertFalse(MediaFile.objects.filter(source_path=str(audio_path)).exists())

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
        backup_target = BackupTarget.objects.create(
            name="AWS",
            backend_type=BackupTarget.BackendType.S3,
            aws_bucket="mml-backup",
            aws_region="us-east-1",
            aws_access_key_id="abc",
            aws_secret_access_key="secret",
        )

        media_file = MediaFile.objects.create(
            directory=directory,
            backup_target=backup_target,
            path="/music/library/song.flac",
            source_path="/imports/song.flac",
            storage_path="library/song.flac",
            audio_format="flac",
            origin_type=MediaFile.OriginType.ORIGINAL,
            is_master=True,
            original_backup_path="s3://archive/song.flac",
            original_backup_status=MediaFile.BackupStatus.CONFIRMED,
            original_backup_sha256="a" * 64,
            original_backed_up_at=backed_up_at,
        )

        self.assertEqual(media_file.backup_target_id, backup_target.id)
        self.assertEqual(media_file.audio_format, "flac")
        self.assertEqual(media_file.origin_type, MediaFile.OriginType.ORIGINAL)
        self.assertTrue(media_file.is_master)
        self.assertEqual(media_file.original_backup_path, "s3://archive/song.flac")
        self.assertEqual(media_file.original_backup_status, "confirmed")
        self.assertEqual(media_file.original_backup_sha256, "a" * 64)
        self.assertEqual(media_file.original_backed_up_at, backed_up_at)

    def test_pending_backup_queryset_returns_only_unconfirmed_flac_originals(self):
        directory = MonitoredDirectory.objects.create(name="Local", path="/music")
        pending = MediaFile.objects.create(
            directory=directory,
            path="/music/pending.flac",
            sha256="1" * 64,
            audio_format="flac",
            origin_type=MediaFile.OriginType.ORIGINAL,
        )
        MediaFile.objects.create(
            directory=directory,
            path="/music/confirmed.flac",
            sha256="2" * 64,
            audio_format="flac",
            origin_type=MediaFile.OriginType.ORIGINAL,
            original_backup_status=MediaFile.BackupStatus.CONFIRMED,
        )
        MediaFile.objects.create(
            directory=directory,
            path="/music/derived.opus",
            sha256="3" * 64,
            audio_format="opus",
            origin_type=MediaFile.OriginType.DERIVED,
        )

        self.assertEqual(list(pending_backup_queryset()), [pending])

    def test_copy_flac_metadata_and_cover_to_opus_embeds_adjacent_cover(self):
        class FakeSource:
            tags = {"title": ["Song"]}
            pictures = []

        class FakeTarget(dict):
            saved = False

            def save(self):
                self.saved = True

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "song.flac"
            output_path = root / "song.opus"
            image_data = b"\x89PNG\r\n\x1a\ncover-bytes"
            source_path.write_bytes(b"flac")
            output_path.write_bytes(b"opus")
            (root / "cover.png").write_bytes(image_data)
            target = FakeTarget()

            with patch("apps.mediafiles.transcoding.FLAC", return_value=FakeSource()), patch(
                "apps.mediafiles.transcoding.OggOpus", return_value=target
            ):
                copy_flac_metadata_and_cover_to_opus(source_path=source_path, output_path=output_path)

        picture = Picture(base64.b64decode(target["metadata_block_picture"][0]))
        self.assertEqual(target["title"], ["Song"])
        self.assertEqual(picture.mime, "image/png")
        self.assertEqual(picture.data, image_data)
        self.assertTrue(target.saved)

    def test_reconcile_backup_manifest_detects_missing_flacs(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kept_path = root / "kept.flac"
            kept_path.write_bytes(b"audio")
            missing_path = root / "missing.flac"
            missing_path.write_bytes(b"audio")
            directory = MonitoredDirectory.objects.create(name="Local", path=str(root))
            kept = MediaFile.objects.create(
                directory=directory,
                path=str(kept_path),
                source_path=str(kept_path),
                sha256="a" * 64,
                audio_format="flac",
                origin_type=MediaFile.OriginType.ORIGINAL,
            )
            missing = MediaFile.objects.create(
                directory=directory,
                path=str(missing_path),
                source_path=str(missing_path),
                sha256="b" * 64,
                audio_format="flac",
                origin_type=MediaFile.OriginType.ORIGINAL,
            )

            result = reconcile_backup_manifest(manifest_text=f"{kept.sha256}\n/backup/{kept.sha256}.flac")

        self.assertEqual(result.manifest_hash_count, 1)
        self.assertEqual(result.missing_media_files, [missing])
        self.assertEqual(result.unavailable_media_files, [])

    @patch("apps.mediafiles.views.start_backup_batch")
    def test_reconcile_backups_queues_missing_files(self, start_backup_batch_mock):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kept_path = root / "kept.flac"
            kept_path.write_bytes(b"audio")
            missing_path = root / "missing.flac"
            missing_path.write_bytes(b"audio")
            directory = MonitoredDirectory.objects.create(name="Local", path=str(root))
            kept = MediaFile.objects.create(
                directory=directory,
                path=str(kept_path),
                source_path=str(kept_path),
                sha256="c" * 64,
                audio_format="flac",
                origin_type=MediaFile.OriginType.ORIGINAL,
            )
            missing = MediaFile.objects.create(
                directory=directory,
                path=str(missing_path),
                source_path=str(missing_path),
                sha256="d" * 64,
                audio_format="flac",
                origin_type=MediaFile.OriginType.ORIGINAL,
            )

            response = self.client.post(
                reverse("reconcile-backups"),
                {"manifest_text": f"{kept.sha256}\n/backup/{kept.sha256}.flac"},
                HTTP_HOST="localhost",
            )

        self.assertRedirects(response, reverse("library-dashboard"))
        start_backup_batch_mock.assert_called_once()
        self.assertEqual(start_backup_batch_mock.call_args.kwargs["media_file_ids"], [missing.id])

    def test_pause_backups_marks_control_paused(self):
        response = self.client.post(reverse("pause-backups"), HTTP_HOST="localhost")

        self.assertRedirects(response, reverse("library-dashboard"))
        self.assertTrue(BackupControl.objects.filter(name="default", is_paused=True).exists())

    def test_queue_backups_resumes_a_paused_batch(self):
        BackupControl.objects.create(name="default", is_running=True, is_paused=True, active_task_id="task-1")

        response = self.client.post(reverse("queue-backups"), HTTP_HOST="localhost")

        self.assertRedirects(response, reverse("library-dashboard"))
        control = BackupControl.objects.get(name="default")
        self.assertFalse(control.is_paused)
        self.assertTrue(control.is_running)

    @patch("apps.mediafiles.views.start_backup_batch")
    def test_queue_backups_starts_a_single_batch(self, start_backup_batch_mock):
        directory = MonitoredDirectory.objects.create(name="Local", path="/music")
        MediaFile.objects.create(
            directory=directory,
            path="/music/pending.flac",
            sha256="1" * 64,
            audio_format="flac",
            origin_type=MediaFile.OriginType.ORIGINAL,
        )

        response = self.client.post(reverse("queue-backups"), HTTP_HOST="localhost")

        self.assertRedirects(response, reverse("library-dashboard"))
        start_backup_batch_mock.assert_called_once_with()

    def test_backup_media_command_updates_media_file_status(self):
        with TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "song.flac"
            audio_path.write_bytes(b"audio")
            directory = MonitoredDirectory.objects.create(name="Local", path=temp_dir)
            target = BackupTarget.objects.create(
                name="AWS",
                backend_type=BackupTarget.BackendType.S3,
                is_default=True,
                aws_bucket="mml-backup",
                aws_region="us-east-1",
                aws_access_key_id="abc",
                aws_secret_access_key="secret",
            )
            media_file = MediaFile.objects.create(
                directory=directory,
                path=str(audio_path),
                source_path=str(audio_path),
                sha256="a" * 64,
                audio_format="flac",
                origin_type=MediaFile.OriginType.ORIGINAL,
            )

            with patch("apps.mediafiles.management.commands.backup_media.backup_media_file") as backup_mock:
                backup_mock.side_effect = lambda **kwargs: MediaFile.objects.filter(id=media_file.id).update(
                    backup_target=target,
                    original_backup_status=MediaFile.BackupStatus.CONFIRMED,
                    original_backup_path="s3://mml-backup/flac/song.flac",
                    original_backup_sha256="a" * 64,
                )
                call_command("backup_media", media_file_id=[media_file.id])

        media_file.refresh_from_db()
        self.assertEqual(media_file.backup_target_id, target.id)
        self.assertEqual(media_file.original_backup_status, MediaFile.BackupStatus.CONFIRMED)

    def test_s3_backup_verify_checks_metadata_and_size(self):
        with TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "song.flac"
            audio_path.write_bytes(b"audio")
            directory = MonitoredDirectory.objects.create(name="Local", path=temp_dir)
            target = BackupTarget.objects.create(
                name="AWS",
                backend_type=BackupTarget.BackendType.S3,
                aws_bucket="mml-backup",
                aws_region="us-east-1",
                aws_access_key_id="abc",
                aws_secret_access_key="secret",
                aws_prefix="originais",
            )
            media_file = MediaFile.objects.create(
                directory=directory,
                path=str(audio_path),
                source_path=str(audio_path),
                sha256="b" * 64,
                audio_format="flac",
                origin_type=MediaFile.OriginType.ORIGINAL,
            )
            result = type("Result", (), {"storage_path": "s3://mml-backup/originais/file.flac", "sha256": media_file.sha256})()

            with patch("apps.mediafiles.backup.boto3", create=True) as boto3_mock:
                client = boto3_mock.client.return_value
                client.head_object.return_value = {
                    "Metadata": {"sha256": media_file.sha256},
                    "ContentLength": audio_path.stat().st_size,
                }
                storage = S3BackupStorage(target)
                storage.verify(media_file=media_file, result=result, source_path=audio_path)

        client.head_object.assert_called_once()

    def test_ensure_sftp_directory_uses_posix_paths(self):
        class FakeSFTP:
            def __init__(self):
                self.created = []

            def stat(self, path: str):
                if path == "/":
                    return object()
                raise OSError

            def mkdir(self, path: str):
                self.created.append(path)

        sftp = FakeSFTP()

        ensure_sftp_directory(sftp, "/backup/music/ab/file.flac")

        self.assertEqual(sftp.created, ["/backup", "/backup/music", "/backup/music/ab"])
    def test_calculate_remote_sha256_reads_remote_file(self):
        class RemoteFile:
            def __init__(self, data: bytes):
                self.data = data
                self.offset = 0

            def read(self, size: int) -> bytes:
                chunk = self.data[self.offset : self.offset + size]
                self.offset += len(chunk)
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeSFTP:
            def open(self, destination: str, mode: str):
                self.destination = destination
                self.mode = mode
                return RemoteFile(b"audio")

        digest = calculate_remote_sha256(sftp=FakeSFTP(), destination="/backup/song.flac")

        self.assertEqual(digest, "6ed8919ce20490a5e3ad8630a4fab69475297abd07db73918dd5f36fcfaeb11b")
