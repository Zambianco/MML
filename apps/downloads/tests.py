import base64
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from urllib.error import URLError
from unittest.mock import patch

from mutagen.flac import Picture

from apps.library.models import Album, Artist, Track
from apps.mediafiles.models import MediaFile, MonitoredDirectory

from .models import TrackImport, TrackImportItem
from .services import _slskd_request, build_slskd_search_query, enqueue_source, process_download_round, recover_stuck_searches, score_slskd_source, search_slskd_sources, skip_item_download, update_download_statuses


class FakeResponse:
    def __init__(self, body=b"{}"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class DownloadImportTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="tester", password="secret")
        self.client.force_login(user)

    def test_import_page_loads(self):
        response = self.client.get(reverse("downloads-import-list"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Importar CSV")

    def test_upload_csv_creates_import_and_items(self):
        content = (
            "Nome;Artistas;Album;Ano;ISRC\n"
            "Frozen;Mortemia, Federica Lanna;Frozen;2022;QZTAU2295243\n"
            "Now & Forever;Xandria;India;2005;DES370500405\n"
        )
        uploaded_file = SimpleUploadedFile("minha-lista.csv", content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("downloads-import-list"),
            {"file": uploaded_file},
            HTTP_HOST="localhost",
        )

        track_import = TrackImport.objects.get()
        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        self.assertEqual(track_import.item_count, 2)
        self.assertEqual(TrackImportItem.objects.count(), 2)
        self.assertTrue(
            TrackImportItem.objects.filter(
                artists="Mortemia, Federica Lanna",
                name="Frozen",
                search_query='"Mortemia, Federica Lanna" Frozen 2022',
            ).exists()
        )

    def test_import_detail_shows_download_tracking(self):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Xandria",
            name="Now & Forever",
            search_query="Xandria Now Forever",
            status=TrackImportItem.STATUS_DOWNLOADING,
            download_progress=45,
        )

        response = self.client.get(reverse("downloads-import-detail", args=[track_import.pk]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Acompanhar downloads")
        self.assertContains(response, "Fila de downloads")
        self.assertContains(response, "45%")

    @patch("apps.downloads.views.update_download_statuses")
    def test_import_detail_polls_active_downloads(self, update_download_statuses):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Xandria",
            name="Now & Forever",
            search_query="Xandria Now Forever",
            status=TrackImportItem.STATUS_DOWNLOADING,
            download_progress=45,
        )

        response = self.client.get(reverse("downloads-import-detail", args=[track_import.pk]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(update_download_statuses.call_count, 1)
        self.assertContains(response, "hx-get=")
        self.assertContains(response, "every 5s")
        self.assertContains(response, "Atividade agora")

    @patch("apps.downloads.views.update_download_statuses")
    def test_import_detail_handles_slskd_connection_error(self, update_download_statuses):
        update_download_statuses.side_effect = URLError("Connection refused")
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Xandria",
            name="Now & Forever",
            search_query="Xandria Now Forever",
            status=TrackImportItem.STATUS_DOWNLOADING,
            download_progress=45,
        )

        response = self.client.get(reverse("downloads-import-detail", args=[track_import.pk]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nao foi possivel conectar ao slskd: Connection refused")
        self.assertContains(response, "Fila de downloads")

    def test_import_detail_shows_round_actions(self):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
        )

        response = self.client.get(reverse("downloads-import-detail", args=[track_import.pk]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Limite da rodada")
        self.assertContains(response, "Buscar e enfileirar")
        self.assertContains(response, "Atualizar status e tentar proxima fonte")

    def test_import_detail_shows_row_actions(self):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
        )

        response = self.client.get(reverse("downloads-import-detail", args=[track_import.pk]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("downloads-item-search", args=[track_import.pk, item.pk]))
        self.assertContains(response, reverse("downloads-item-transfer", args=[track_import.pk, item.pk]))
        self.assertContains(response, "Copiar")
        self.assertContains(response, "Salvar")
        self.assertContains(response, "Regenerar")
        self.assertContains(response, "Transferir")
        self.assertContains(response, "Download")

    def test_import_detail_filters_items(self):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=2)
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
            search_attempts=2,
            status=TrackImportItem.STATUS_DONE,
            download_path="ready/file.flac",
        )
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=2,
            artists="Xandria",
            name="Now & Forever",
            search_query="Xandria Now Forever",
            search_attempts=1,
            status=TrackImportItem.STATUS_DOWNLOADING,
        )

        response = self.client.get(
            reverse("downloads-import-detail", args=[track_import.pk]),
            {"q": "Fairytale", "status": "done", "downloaded": "yes", "search_attempts": "2"},
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fairytale")
        self.assertNotContains(response, "Now & Forever")
        self.assertContains(response, "Com arquivo")
        self.assertContains(response, 'name="search_attempts"')
        self.assertContains(response, ">2</td>", html=False)

    def test_import_detail_paginates_items(self):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=26)
        TrackImportItem.objects.bulk_create(
            [
                TrackImportItem(
                    track_import=track_import,
                    row_number=index,
                    artists="Artist",
                    name=f"Track {index:02d}",
                    search_query=f"Artist Track {index:02d}",
                )
                for index in range(1, 27)
            ]
        )

        response = self.client.get(
            reverse("downloads-import-detail", args=[track_import.pk]),
            {"page": 2},
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Página 2 de 2")
        self.assertContains(response, "Track 26")
        self.assertNotContains(response, "Track 01")

    @patch("apps.downloads.views._celery_workers_available", return_value=True)
    @patch("apps.downloads.views.process_download_round_task.delay")
    def test_process_round_uses_limit(self, delay, _workers_available):
        delay.return_value.id = "task-1"
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=2)

        response = self.client.post(
            reverse("downloads-process-round", args=[track_import.pk]),
            {"limit": "2"},
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        delay.assert_called_once_with(track_import.pk, limit=2)
        track_import.refresh_from_db()
        self.assertEqual(track_import.processing_task_id, "task-1")
        self.assertTrue(track_import.is_processing)

    @patch("apps.downloads.views._celery_workers_available", return_value=False)
    @patch("apps.downloads.views._start_local_process_round")
    @patch("apps.downloads.views.current_app.AsyncResult")
    def test_process_round_releases_stale_pending_task_without_worker(self, async_result, start_local, _workers_available):
        async_result.return_value.state = "PENDING"
        track_import = TrackImport.objects.create(
            source_name="downloads.csv",
            item_count=2,
            processing_task_id="task-1",
            processing_started_at=timezone.now() - timedelta(hours=2),
            processing_last_error="erro antigo",
        )

        def fake_start(selected_import, limit):
            selected_import.processing_task_id = "local-task"
            selected_import.save(update_fields=["processing_task_id"])

        start_local.side_effect = fake_start

        response = self.client.post(
            reverse("downloads-process-round", args=[track_import.pk]),
            {"limit": "3"},
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        start_local.assert_called_once()
        self.assertEqual(start_local.call_args.args[1], 3)
        track_import.refresh_from_db()
        self.assertEqual(track_import.processing_task_id, "local-task")
        self.assertEqual(track_import.processing_last_error, "")
        self.assertTrue(track_import.is_processing)

    @patch("apps.downloads.views.Thread")
    @patch("apps.downloads.views._celery_workers_available", return_value=False)
    def test_process_round_keeps_new_local_task_processing_until_thread_runs(self, _workers_available, thread_class):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Dio",
            name="Holy Diver",
            search_query="Dio Holy Diver",
        )

        response = self.client.post(
            reverse("downloads-process-round", args=[track_import.pk]),
            {"limit": "5"},
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        thread_class.return_value.start.assert_called_once()
        track_import.refresh_from_db()
        self.assertTrue(track_import.is_processing)
        self.assertTrue(track_import.processing_task_id.startswith("local-download-round-"))

    def test_import_detail_keeps_recent_local_task_without_active_search(self):
        track_import = TrackImport.objects.create(
            source_name="downloads.csv",
            item_count=1,
            processing_task_id="local-download-round-missing",
            processing_started_at=timezone.now(),
        )
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Dio",
            name="Holy Diver",
            search_query="Dio Holy Diver",
            status=TrackImportItem.STATUS_PENDING,
        )

        response = self.client.get(reverse("downloads-import-detail", args=[track_import.pk]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        track_import.refresh_from_db()
        self.assertTrue(track_import.is_processing)
        self.assertContains(response, "Rodada em background")

    def test_import_detail_releases_processing_without_active_search(self):
        track_import = TrackImport.objects.create(
            source_name="downloads.csv",
            item_count=1,
            processing_task_id="local-download-round-missing",
            processing_started_at=timezone.now() - timedelta(minutes=2),
        )
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Dio",
            name="Holy Diver",
            search_query="Dio Holy Diver",
            status=TrackImportItem.STATUS_DOWNLOADING,
        )

        response = self.client.get(reverse("downloads-import-detail", args=[track_import.pk]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        track_import.refresh_from_db()
        self.assertFalse(track_import.is_processing)
        self.assertNotContains(response, "Cancelar rodada")
        self.assertContains(response, '<button class="btn btn-dark" type="submit" >Buscar e enfileirar</button>', html=False)

    @patch("apps.downloads.views._start_local_process_round")
    @patch("apps.downloads.views._celery_workers_available", return_value=False)
    def test_process_round_allows_new_round_when_previous_has_no_active_search(self, _workers_available, start_local):
        track_import = TrackImport.objects.create(
            source_name="downloads.csv",
            item_count=1,
            processing_task_id="local-download-round-missing",
            processing_started_at=timezone.now() - timedelta(minutes=2),
        )
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Dio",
            name="Holy Diver",
            search_query="Dio Holy Diver",
            status=TrackImportItem.STATUS_DOWNLOADING,
        )

        response = self.client.post(
            reverse("downloads-process-round", args=[track_import.pk]),
            {"limit": "5"},
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        start_local.assert_called_once()

    def test_cancel_round_marks_import_for_cancellation(self):
        track_import = TrackImport.objects.create(
            source_name="downloads.csv",
            item_count=2,
            processing_task_id="task-1",
            processing_started_at=timezone.now(),
        )

        response = self.client.post(
            reverse("downloads-cancel-round", args=[track_import.pk]),
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        track_import.refresh_from_db()
        self.assertIsNotNone(track_import.cancel_requested_at)

    @patch("apps.downloads.views.search_slskd_sources")
    def test_item_search_uses_row(self, search_slskd_sources):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
        )

        response = self.client.post(
            reverse("downloads-item-search", args=[track_import.pk, item.pk]),
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        search_slskd_sources.assert_called_once_with(item)

    @patch("apps.downloads.services.time.sleep")
    @patch("apps.downloads.services._slskd_request")
    def test_manual_query_is_used_without_overwriting(self, slskd_request, sleep):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion custom query",
            search_query_mode=TrackImportItem.SEARCH_QUERY_MANUAL,
        )
        slskd_request.side_effect = [
            {"id": "abc"},
            {"isComplete": True},
            [],
            None,
        ]

        search_slskd_sources(item)

        item.refresh_from_db()
        self.assertEqual(slskd_request.call_args_list[0].args[2]["searchText"], "Elysion custom query")
        self.assertEqual(item.search_query_mode, TrackImportItem.SEARCH_QUERY_MANUAL)
        self.assertEqual(item.search_query, "Elysion custom query")
        sleep.assert_not_called()

    @patch("apps.downloads.views.enqueue_best_available_source")
    def test_item_transfer_uses_row(self, enqueue_best_available_source):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
        )
        enqueue_best_available_source.return_value = item.sources.model(item=item, rank=1, username="user", remote_filename="file.flac")

        response = self.client.post(
            reverse("downloads-item-transfer", args=[track_import.pk, item.pk]),
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]) + "?page=1")
        enqueue_best_available_source.assert_called_once()

    def test_item_download_serves_local_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            storage_root.mkdir()
            file_path = storage_root / "file.flac"
            file_path.write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root):
                track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
                item = TrackImportItem.objects.create(
                    track_import=track_import,
                    row_number=1,
                    artists="Elysion",
                    name="Fairytale",
                    search_query="Elysion Fairytale",
                    download_path="file.flac",
                )

                response = self.client.get(
                    reverse("downloads-item-download", args=[track_import.pk, item.pk]),
                    HTTP_HOST="localhost",
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"audio-bytes")
            self.assertIn('attachment; filename="file.flac"', response.headers["Content-Disposition"])

    def test_item_stream_serves_audio_inline_with_range_support(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            storage_root.mkdir()
            file_path = storage_root / "file.mp3"
            file_path.write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root):
                track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
                item = TrackImportItem.objects.create(
                    track_import=track_import,
                    row_number=1,
                    artists="Elysion",
                    name="Fairytale",
                    search_query="Elysion Fairytale",
                    download_path="file.mp3",
                )

                response = self.client.get(
                    reverse("downloads-item-stream", args=[track_import.pk, item.pk]),
                    HTTP_RANGE="bytes=0-4",
                    HTTP_HOST="localhost",
                )

            self.assertEqual(response.status_code, 206)
            self.assertEqual(b"".join(response.streaming_content), b"audio")
            self.assertEqual(response.headers["Content-Type"], "audio/mpeg")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.headers["Content-Range"], "bytes 0-4/11")

    def test_item_download_falls_back_to_source_filename_when_path_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            storage_root.mkdir()
            file_path = storage_root / "nested" / "file.flac"
            file_path.parent.mkdir()
            file_path.write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
                item = TrackImportItem.objects.create(
                    track_import=track_import,
                    row_number=1,
                    artists="Elysion",
                    name="Fairytale",
                    search_query="Elysion Fairytale",
                    status=TrackImportItem.STATUS_DONE,
                )
                item.sources.create(rank=1, username="user", remote_filename="file.flac")

                response = self.client.get(
                    reverse("downloads-item-download", args=[track_import.pk, item.pk]),
                    HTTP_HOST="localhost",
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"audio-bytes")

    def test_item_download_resolves_windows_style_relative_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            storage_root.mkdir()
            file_path = storage_root / "nested" / "file.flac"
            file_path.parent.mkdir()
            file_path.write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
                item = TrackImportItem.objects.create(
                    track_import=track_import,
                    row_number=1,
                    artists="Elysion",
                    name="Fairytale",
                    search_query="Elysion Fairytale",
                    download_path="nested\\file.flac",
                )

                response = self.client.get(
                    reverse("downloads-item-download", args=[track_import.pk, item.pk]),
                    HTTP_HOST="localhost",
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"audio-bytes")

    def test_item_download_serves_absolute_transfer_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            storage_root.mkdir()
            transfer_path = Path(temp_dir) / "downloads" / "file.flac"
            transfer_path.parent.mkdir()
            transfer_path.write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
                item = TrackImportItem.objects.create(
                    track_import=track_import,
                    row_number=1,
                    artists="Elysion",
                    name="Fairytale",
                    search_query="Elysion Fairytale",
                    download_path=str(transfer_path),
                )

                response = self.client.get(
                    reverse("downloads-item-download", args=[track_import.pk, item.pk]),
                    HTTP_HOST="localhost",
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"audio-bytes")

    def test_item_download_resolves_path_with_storage_root_prefix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            album_dir = storage_root / "Tubular Bells (1973)"
            album_dir.mkdir(parents=True)
            file_path = album_dir / "01 - Tubular Bells (Pt. I).flac"
            file_path.write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
                item = TrackImportItem.objects.create(
                    track_import=track_import,
                    row_number=1,
                    artists="Mike Oldfield",
                    name="Tubular Bells (Pt. I)",
                    search_query="Mike Oldfield Tubular Bells",
                    download_path="music\\Mike Oldfield\\Tubular Bells (1973)\\01 - Tubular Bells (Pt. I).flac",
                )

                response = self.client.get(
                    reverse("downloads-item-download", args=[track_import.pk, item.pk]),
                    HTTP_HOST="localhost",
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"audio-bytes")

    def test_download_files_page_lists_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            (nested_dir / "track.flac").write_bytes(b"audio-bytes")
            (nested_dir / "track.mp3").write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(
                    reverse("downloads-files"),
                    {"q": "track", "ext": ".flac", "root": str(storage_root)},
                    HTTP_HOST="localhost",
                )

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "nested/track.flac")
            self.assertNotContains(response, "nested/track.mp3")

    @patch("apps.downloads.views.MutagenFile")
    def test_download_files_page_filters_tracks_with_missing_metadata(self, mutagen_file):
        def fake_audio(path, easy=True):
            tags = (
                {
                    "title": ["Complete"],
                    "artist": ["Artist"],
                    "album": ["Album"],
                    "date": ["2024-05-01"],
                }
                if Path(path).name == "complete.mp3"
                else {
                    "artist": ["Artist"],
                }
            )
            return type("AudioFile", (), {"tags": tags})()

        mutagen_file.side_effect = fake_audio

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            (nested_dir / "missing.mp3").write_bytes(b"audio-bytes")
            (nested_dir / "complete.mp3").write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(
                    reverse("downloads-files"),
                    {"missing_metadata": "1"},
                    HTTP_HOST="localhost",
                )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nested/missing.mp3")
        self.assertNotContains(response, "nested/complete.mp3")
        self.assertContains(response, "Faltando: titulo, album, ano")
        self.assertContains(response, 'name="missing_metadata" value="1" checked', html=False)

    @patch("apps.downloads.views.MutagenFile")
    def test_download_files_page_preserves_missing_metadata_validation_with_persisted_track(self, mutagen_file):
        mutagen_file.return_value.tags = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            file_path = nested_dir / "track.mp3"
            file_path.write_bytes(b"audio-bytes")

            artist = Artist.objects.create(name="Epica", sort_name="Epica")
            album = Album.objects.create(title="The Phantom Agony", artist=artist, release_date="2003-06-05")
            track = Track.objects.create(title="Sensorium", artist=artist, album=album)
            directory = MonitoredDirectory.objects.create(name="Library", path=str(storage_root))
            MediaFile.objects.create(directory=directory, track=track, path=str(file_path.resolve()))

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(
                    reverse("downloads-files"),
                    {"missing_metadata": "1"},
                    HTTP_HOST="localhost",
                )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sensorium")
        self.assertContains(response, "Epica")
        self.assertContains(response, "Faltando: titulo, artista, album, ano")

    def test_music_player_page_lists_files_with_stream_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            (nested_dir / "track.mp3").write_bytes(b"audio-bytes")
            (nested_dir / "cover.jpg").write_bytes(b"cover-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(reverse("downloads-player"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Player")
        self.assertContains(response, "nested/track.mp3")
        self.assertContains(response, reverse("downloads-file-stream"))
        self.assertContains(response, reverse("downloads-file-cover"))

    def test_music_player_page_uses_mock_tracks_when_library_is_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            storage_root.mkdir(parents=True)

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(reverse("downloads-player"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aurora Drive")
        self.assertContains(response, "Night Shift")
        self.assertContains(response, "Glass Horizon")
        self.assertContains(response, "data:audio/wav;base64,")

    def test_music_player_page_appends_mock_tracks_when_debug_is_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            (nested_dir / "track.mp3").write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root, DEBUG=True):
                response = self.client.get(reverse("downloads-player"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nested/track.mp3")
        self.assertContains(response, "Aurora Drive")
        self.assertContains(response, "Night Shift")
        self.assertContains(response, "Glass Horizon")

    def test_file_cover_serves_adjacent_artwork(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            (nested_dir / "track.mp3").write_bytes(b"audio-bytes")
            (nested_dir / "cover.jpg").write_bytes(b"cover-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(
                    reverse("downloads-file-cover"),
                    {"path": "nested/track.mp3"},
                    HTTP_HOST="localhost",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response.content, b"cover-bytes")

    @patch("apps.downloads.views.MutagenFile")
    def test_file_cover_serves_opus_metadata_block_picture(self, mutagen_file):
        image_data = b"\x89PNG\r\n\x1a\ncover-bytes"
        picture = Picture()
        picture.type = 3
        picture.mime = "image/png"
        picture.data = image_data
        mutagen_file.return_value.tags = {"metadata_block_picture": [base64.b64encode(picture.write()).decode("ascii")]}
        mutagen_file.return_value.pictures = []

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            (nested_dir / "track.opus").write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(
                    reverse("downloads-file-cover"),
                    {"path": "nested/track.opus"},
                    HTTP_HOST="localhost",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response.content, image_data)

    @patch("apps.downloads.views.MutagenFile")
    def test_music_player_page_prefers_structured_audio_metadata(self, mutagen_file):
        mutagen_file.return_value.tags = {
            "title": ["Shine"],
            "artist": ["Within Temptation"],
            "album": ["Enter"],
            "date": ["1997-04-07"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            (nested_dir / "track.mp3").write_bytes(b"audio-bytes")

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(reverse("downloads-player"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shine")
        self.assertContains(response, "Within Temptation")
        self.assertContains(response, "Enter")
        self.assertContains(response, "1997")
        self.assertNotContains(response, ">track.mp3<")

    @patch("apps.downloads.views.MutagenFile")
    def test_music_player_page_prefers_persisted_library_metadata(self, mutagen_file):
        mutagen_file.return_value.tags = {
            "title": ["Wrong Title"],
            "artist": ["Wrong Artist"],
            "album": ["Wrong Album"],
            "date": ["2000-01-01"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            file_path = nested_dir / "track.mp3"
            file_path.write_bytes(b"audio-bytes")

            artist = Artist.objects.create(name="Epica", sort_name="Epica")
            album = Album.objects.create(title="The Phantom Agony", artist=artist, release_date="2003-06-05")
            track = Track.objects.create(title="Sensorium", artist=artist, album=album)
            directory = MonitoredDirectory.objects.create(name="Library", path=str(storage_root))
            MediaFile.objects.create(directory=directory, track=track, path=str(file_path.resolve()))

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(reverse("downloads-player"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sensorium")
        self.assertContains(response, "Epica")
        self.assertContains(response, "The Phantom Agony")
        self.assertContains(response, "2003")
        self.assertNotContains(response, "Wrong Title")

    def test_music_player_page_filters_by_artist_and_album(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "music"
            nested_dir = storage_root / "nested"
            nested_dir.mkdir(parents=True)
            first_file = nested_dir / "first.mp3"
            second_file = nested_dir / "second.mp3"
            first_file.write_bytes(b"audio-bytes")
            second_file.write_bytes(b"audio-bytes")

            artist_a = Artist.objects.create(name="Nightwish", sort_name="Nightwish")
            album_a = Album.objects.create(title="Once", artist=artist_a, release_date="2004-06-07")
            track_a = Track.objects.create(title="Nemo", artist=artist_a, album=album_a)

            artist_b = Artist.objects.create(name="Delain", sort_name="Delain")
            album_b = Album.objects.create(title="Lucidity", artist=artist_b, release_date="2006-09-04")
            track_b = Track.objects.create(title="See Me in Shadow", artist=artist_b, album=album_b)

            directory = MonitoredDirectory.objects.create(name="Library", path=str(storage_root))
            MediaFile.objects.create(directory=directory, track=track_a, path=str(first_file.resolve()))
            MediaFile.objects.create(directory=directory, track=track_b, path=str(second_file.resolve()))

            with override_settings(MUSIC_STORAGE_ROOT=storage_root, SLSKD_DOWNLOADS_DIR=storage_root):
                response = self.client.get(
                    reverse("downloads-player"),
                    {"artist": "Nightwish", "album": "Once"},
                    HTTP_HOST="localhost",
                )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nemo")
        self.assertNotContains(response, "See Me in Shadow")
        self.assertContains(response, '<option value="Nightwish" selected>', html=False)
        self.assertContains(response, '<option value="Once" selected>', html=False)

    @patch("apps.downloads.services._slskd_request")
    def test_update_download_statuses_skips_slskd_without_requested_sources(self, slskd_request):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Delain",
            name="Lost",
            search_query="Delain Lost",
        )

        summary = update_download_statuses(track_import, enqueue_next=False)

        self.assertEqual(summary, {"updated": 0, "done": 0, "failed": 0, "queued_next": 0})
        slskd_request.assert_not_called()

    @patch("apps.downloads.services._slskd_request")
    def test_update_download_statuses_saves_completed_directory_path(self, slskd_request):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Delain",
            name="Lost",
            search_query="Delain Lost",
            status=TrackImportItem.STATUS_DOWNLOADING,
            download_path="09 - Lost.flac",
        )
        source = item.sources.create(
            rank=1,
            username="user",
            remote_filename="09 - Lost.flac",
            download_state="InProgress",
        )
        source.mark_requested()
        slskd_request.return_value = [
            {
                "username": "user",
                "directories": [
                    {
                        "directory": "April Rain (Special Edition) [2009] [Album]",
                        "files": [
                            {
                                "filename": "09 - Lost.flac",
                                "state": "Completed, Succeeded",
                                "percentComplete": 100,
                            }
                        ],
                    }
                ],
            }
        ]

        update_download_statuses(track_import, enqueue_next=False)

        item.refresh_from_db()
        self.assertEqual(item.status, TrackImportItem.STATUS_DONE)
        self.assertEqual(item.download_path, "April Rain (Special Edition) [2009] [Album]/09 - Lost.flac")

    @patch("apps.downloads.services._slskd_request")
    def test_update_download_statuses_keeps_completed_source_done(self, slskd_request):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Delain",
            name="Lost",
            search_query="Delain Lost",
            status=TrackImportItem.STATUS_DOWNLOADING,
            download_path="alt.flac",
        )
        completed_source = item.sources.create(rank=1, username="winner", remote_filename="song.flac")
        completed_source.mark_requested()
        queued_source = item.sources.create(rank=2, username="queued", remote_filename="alt.flac")
        queued_source.mark_requested()
        slskd_request.return_value = [
            {
                "username": "winner",
                "directories": [
                    {
                        "directory": "Album",
                        "files": [
                            {
                                "filename": "song.flac",
                                "state": "Completed, Succeeded",
                                "percentComplete": 100,
                            }
                        ],
                    }
                ],
            },
            {
                "username": "queued",
                "directories": [
                    {
                        "directory": "Album",
                        "files": [
                            {
                                "filename": "alt.flac",
                                "state": "Queued, Remotely",
                                "percentComplete": 0,
                            }
                        ],
                    }
                ],
            },
        ]

        summary = update_download_statuses(track_import, enqueue_next=False)

        item.refresh_from_db()
        self.assertEqual(item.status, TrackImportItem.STATUS_DONE)
        self.assertEqual(item.download_progress, 100)
        self.assertEqual(item.download_path, "Album/song.flac")
        self.assertEqual(summary["done"], 1)

    @patch("apps.downloads.services._slskd_request")
    def test_update_download_statuses_uses_stored_completed_state_when_transfer_disappears(self, slskd_request):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Delain",
            name="Lost",
            search_query="Delain Lost",
            status=TrackImportItem.STATUS_DOWNLOADING,
        )
        source = item.sources.create(rank=1, username="winner", remote_filename="song.flac")
        source.mark_requested()
        source.download_state = "Completed, Succeeded"
        source.save(update_fields=["download_state", "updated_at"])
        slskd_request.return_value = []

        summary = update_download_statuses(track_import, enqueue_next=False)

        item.refresh_from_db()
        self.assertEqual(item.status, TrackImportItem.STATUS_DONE)
        self.assertEqual(item.download_progress, 100)
        self.assertEqual(item.download_path, "song.flac")
        self.assertEqual(summary["done"], 1)

    @patch("apps.downloads.services._slskd_request")
    def test_enqueue_source_sets_download_start_and_progress_timestamps(self, slskd_request):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Delain",
            name="Lost",
            search_query="Delain Lost",
        )
        source = item.sources.create(rank=1, username="user", remote_filename="09 - Lost.flac")

        enqueue_source(source)

        item.refresh_from_db()
        self.assertEqual(item.status, TrackImportItem.STATUS_DOWNLOADING)
        self.assertIsNotNone(item.download_started_at)
        self.assertIsNotNone(item.download_progress_updated_at)

    @patch("apps.downloads.services._slskd_request")
    def test_update_download_statuses_cancels_stale_download_and_queues_next_source(self, slskd_request):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        stale_at = timezone.now() - timedelta(hours=7)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Delain",
            name="Lost",
            search_query="Delain Lost",
            status=TrackImportItem.STATUS_DOWNLOADING,
            download_progress=35,
            download_path="09 - Lost.flac",
            download_started_at=stale_at,
            download_progress_updated_at=stale_at,
        )
        stale_source = item.sources.create(
            rank=1,
            username="user",
            remote_filename="09 - Lost.flac",
            download_state="InProgress",
        )
        stale_source.mark_requested()
        next_source = item.sources.create(
            rank=2,
            username="backup",
            remote_filename="09 - Lost (alt).flac",
        )
        slskd_request.side_effect = [
            [
                {
                    "username": "user",
                    "directories": [
                        {
                            "directory": "Album",
                            "files": [
                                {
                                    "id": "transfer-1",
                                    "filename": "09 - Lost.flac",
                                    "state": "InProgress",
                                    "percentComplete": 35,
                                }
                            ],
                        }
                    ],
                }
            ],
            None,
            None,
        ]

        summary = update_download_statuses(track_import)

        item.refresh_from_db()
        stale_source.refresh_from_db()
        next_source.refresh_from_db()
        self.assertEqual(item.status, TrackImportItem.STATUS_DOWNLOADING)
        self.assertEqual(item.download_path, "09 - Lost (alt).flac")
        self.assertEqual(item.last_error, "")
        self.assertIn("stale-cancelled", stale_source.download_state)
        self.assertIsNotNone(next_source.download_requested_at)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["queued_next"], 1)

    @patch("apps.downloads.services._slskd_request")
    def test_skip_item_download_cancels_current_transfer_and_queues_next(self, slskd_request):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Delain",
            name="Lost",
            search_query="Delain Lost",
            status=TrackImportItem.STATUS_DOWNLOADING,
            download_progress=35,
            download_path="09 - Lost.flac",
        )
        current_source = item.sources.create(
            rank=1,
            username="user",
            remote_filename="09 - Lost.flac",
            download_state="InProgress",
        )
        current_source.mark_requested()
        next_source = item.sources.create(rank=2, username="backup", remote_filename="09 - Lost (alt).flac")
        slskd_request.side_effect = [
            [
                {
                    "username": "user",
                    "directories": [
                        {
                            "directory": "Album",
                            "files": [
                                {
                                    "id": "transfer-1",
                                    "filename": "09 - Lost.flac",
                                    "state": "InProgress",
                                    "percentComplete": 35,
                                }
                            ],
                        }
                    ],
                }
            ],
            None,
            None,
        ]

        queued_source = skip_item_download(item)

        item.refresh_from_db()
        current_source.refresh_from_db()
        next_source.refresh_from_db()
        self.assertEqual(queued_source, next_source)
        self.assertEqual(item.status, TrackImportItem.STATUS_DOWNLOADING)
        self.assertEqual(item.download_path, "09 - Lost (alt).flac")
        self.assertIn("skipped-manual", current_source.download_state)
        self.assertIsNotNone(next_source.download_requested_at)

    @patch("apps.downloads.services.time.sleep")
    @patch("apps.downloads.services._slskd_request")
    def test_search_stops_when_cancel_requested(self, slskd_request, sleep):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
        )
        slskd_request.side_effect = [{"id": "abc"}, None]

        sources = search_slskd_sources(item, should_cancel=lambda: True)

        item.refresh_from_db()
        self.assertEqual(sources, [])
        self.assertEqual(item.search_state, "Cancelled")
        self.assertIsNotNone(item.search_finished_at)
        self.assertEqual(slskd_request.call_args_list[-1].args[:2], ("DELETE", "/api/v0/searches/abc"))
        sleep.assert_not_called()

    @patch("apps.downloads.services._slskd_request")
    def test_recover_stuck_search_fetches_saved_responses_and_queues_download(self, slskd_request):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=612,
            artists="Elton John",
            name="I'm Still Standing",
            album="Too Low For Zero",
            year=1983,
            search_query="Elton John I'm Still Standing Too Low For Zero 1983",
            status=TrackImportItem.STATUS_SEARCHING,
            search_slskd_id="search-612",
            search_state="Completed, TimedOut",
            search_response_count=145,
            search_started_at=timezone.now() - timedelta(hours=21),
        )
        slskd_request.side_effect = [
            {"isComplete": True, "state": "Completed, TimedOut", "responseCount": 145},
            [
                {
                    "username": "user",
                    "queueLength": 0,
                    "uploadSpeed": 1000,
                    "files": [
                        {
                            "filename": "Elton John - I'm Still Standing.flac",
                            "extension": "flac",
                            "size": 123,
                            "length": 184,
                        }
                    ],
                }
            ],
            None,
        ]

        summary = recover_stuck_searches(track_import)

        item.refresh_from_db()
        self.assertEqual(summary["queued"], 1)
        self.assertEqual(item.status, TrackImportItem.STATUS_DOWNLOADING)
        self.assertEqual(item.download_path, "Elton John - I'm Still Standing.flac")
        self.assertIsNotNone(item.search_finished_at)
        self.assertEqual(item.sources.count(), 1)

    @patch("apps.downloads.services._slskd_request")
    def test_recover_stuck_search_queues_existing_source(self, slskd_request):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=641,
            artists="Dio",
            name="Holy Diver",
            year=1983,
            search_query="Dio Holy Diver 1983",
            status=TrackImportItem.STATUS_SEARCHING,
            search_state="Completed, ResponseLimitReached",
            search_response_count=250,
            search_started_at=timezone.now() - timedelta(hours=1, minutes=17),
        )
        item.sources.create(rank=1, username="user", remote_filename="Dio - Holy Diver.flac", score=100)
        slskd_request.return_value = None

        summary = recover_stuck_searches(track_import)

        item.refresh_from_db()
        self.assertEqual(summary["queued"], 1)
        self.assertEqual(item.status, TrackImportItem.STATUS_DOWNLOADING)
        self.assertEqual(item.download_path, "Dio - Holy Diver.flac")
        self.assertEqual(slskd_request.call_count, 1)

    def test_item_query_update_manual(self):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
        )

        response = self.client.post(
            reverse("downloads-item-query", args=[track_import.pk, item.pk]),
            {"search_query": "Elysion custom query"},
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        item.refresh_from_db()
        self.assertEqual(item.search_query_mode, TrackImportItem.SEARCH_QUERY_MANUAL)
        self.assertEqual(item.search_query, "Elysion custom query")

    def test_item_query_update_auto_regenerates(self):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Mortemia",
            name="Frozen",
            year=2022,
            search_query="custom query",
            search_query_mode=TrackImportItem.SEARCH_QUERY_MANUAL,
        )

        response = self.client.post(
            reverse("downloads-item-query", args=[track_import.pk, item.pk]),
            {"query_action": "regenerate"},
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        item.refresh_from_db()
        self.assertEqual(item.search_query_mode, TrackImportItem.SEARCH_QUERY_AUTO)
        self.assertEqual(item.search_query, "Mortemia Frozen 2022")

    def test_upload_csv_rejects_missing_required_headers(self):
        content = "Nome;Album;Ano\nFrozen;Frozen;2022\n"
        uploaded_file = SimpleUploadedFile("invalido.csv", content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("downloads-import-list"),
            {"file": uploaded_file},
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nome e Artista")
        self.assertFalse(TrackImport.objects.exists())

    def test_build_search_query_ignores_empty_optional_fields(self):
        query = build_slskd_search_query(name="Fairytale", artists="Elysion")

        self.assertEqual(query, "Elysion Fairytale")

    def test_build_search_query_ignores_isrc(self):
        query = build_slskd_search_query(name="Frozen", artists="Mortemia", year=2022, isrc="QZTAU2295243")

        self.assertEqual(query, "Mortemia Frozen 2022")

    def test_score_prefers_lossless_source(self):
        item = TrackImportItem(name="Fairytale", artists="Elysion", row_number=1, search_query="Elysion Fairytale")
        response = {"uploadSpeed": 1_000_000, "queueLength": 0, "hasFreeUploadSlot": True}
        flac = score_slskd_source(response, {"filename": "Elysion - Fairytale.flac", "extension": "flac"}, item)
        mp3 = score_slskd_source(response, {"filename": "Elysion - Fairytale.mp3", "extension": "mp3"}, item)

        self.assertGreater(flac, mp3)

    @patch("apps.downloads.services.time.sleep")
    @patch("apps.downloads.services._slskd_request")
    def test_search_waits_until_slskd_completes(self, slskd_request, sleep):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
        )
        slskd_request.side_effect = [
            {"id": "abc"},
            {"isComplete": False},
            {"isComplete": True},
            [{"username": "user", "files": [{"filename": "Elysion - Fairytale.flac", "extension": "flac", "size": 1}]}],
            None,
        ]

        sources = search_slskd_sources(item)

        self.assertEqual(len(sources), 1)
        sleep.assert_called_once()

    @patch("apps.downloads.services.time.sleep")
    @patch("apps.downloads.services._slskd_request")
    def test_search_rebuilds_old_query_without_isrc(self, slskd_request, sleep):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Mortemia",
            name="Frozen",
            year=2022,
            isrc="QZTAU2295243",
            search_query="Mortemia Frozen 2022 QZTAU2295243",
        )
        slskd_request.side_effect = [
            {"id": "abc"},
            {"isComplete": True},
            [],
            None,
        ]

        search_slskd_sources(item)

        item.refresh_from_db()
        self.assertEqual(item.search_attempts, 1)
        self.assertEqual(item.search_query, "Mortemia Frozen 2022")
        self.assertEqual(slskd_request.call_args_list[0].args[2]["searchText"], "Mortemia Frozen 2022")

    @patch("apps.downloads.services.search_slskd_sources")
    def test_process_round_prioritizes_items_with_fewer_searches(self, search_slskd_sources):
        processed_items = []

        def capture(item):
            processed_items.append(item.pk)
            return []

        search_slskd_sources.side_effect = capture
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=3)
        item_1 = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Artist 1",
            name="Track 1",
            search_query="Artist 1 Track 1",
            search_attempts=2,
        )
        item_2 = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=2,
            artists="Artist 2",
            name="Track 2",
            search_query="Artist 2 Track 2",
            search_attempts=0,
        )
        item_3 = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=3,
            artists="Artist 3",
            name="Track 3",
            search_query="Artist 3 Track 3",
            search_attempts=1,
        )

        process_download_round(track_import, limit=3)

        self.assertEqual(processed_items, [item_2.pk, item_3.pk, item_1.pk])

    @patch("apps.downloads.services.search_slskd_sources")
    def test_process_round_marks_item_error_when_search_fails(self, search_slskd_sources):
        search_slskd_sources.side_effect = URLError("offline")
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
            status=TrackImportItem.STATUS_SEARCHING,
        )

        with self.assertRaises(URLError):
            process_download_round(track_import, limit=1)

        item.refresh_from_db()
        self.assertEqual(item.status, TrackImportItem.STATUS_ERROR)

    @patch("apps.downloads.services.time.sleep")
    @patch("apps.downloads.services.update_download_statuses")
    @patch("apps.downloads.services.enqueue_source")
    @patch("apps.downloads.services.search_slskd_sources")
    def test_process_round_with_zero_limit_does_not_wait_for_downloads(self, search_slskd_sources, enqueue_source, update_download_statuses, _sleep):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
        )
        source = item.sources.create(rank=1, username="user", remote_filename="fairytale.flac")
        search_slskd_sources.return_value = [source]

        def fake_enqueue(selected_source):
            selected_source.item.status = TrackImportItem.STATUS_DOWNLOADING
            selected_source.item.save(update_fields=["status", "updated_at"])

        def fake_update(_track_import, enqueue_next=True):
            if update_download_statuses.call_count == 2:
                item.refresh_from_db()
                item.status = TrackImportItem.STATUS_DONE
                item.save(update_fields=["status", "updated_at"])
            return {"updated": 0, "done": 0, "failed": 0, "queued_next": 0}

        enqueue_source.side_effect = fake_enqueue
        update_download_statuses.side_effect = fake_update

        summary = process_download_round(track_import, limit=0)

        self.assertEqual(summary, {"searched": 1, "queued": 1, "without_source": 0, "cancelled": 0})
        self.assertEqual(update_download_statuses.call_count, 1)
        search_slskd_sources.assert_called_once()
        item.refresh_from_db()
        self.assertEqual(item.status, TrackImportItem.STATUS_DOWNLOADING)

    @patch("apps.downloads.services.time.sleep")
    @patch("apps.downloads.services.update_download_statuses")
    @patch("apps.downloads.services.enqueue_source")
    @patch("apps.downloads.services.search_slskd_sources")
    def test_process_round_with_zero_limit_finishes_after_enqueuing(self, search_slskd_sources, enqueue_source, update_download_statuses, _sleep):
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=1)
        item = TrackImportItem.objects.create(
            track_import=track_import,
            row_number=1,
            artists="Elysion",
            name="Fairytale",
            search_query="Elysion Fairytale",
        )
        source = item.sources.create(rank=1, username="user", remote_filename="fairytale.flac")
        search_slskd_sources.return_value = [source]

        def fake_enqueue(selected_source):
            selected_source.item.status = TrackImportItem.STATUS_DOWNLOADING
            selected_source.item.save(update_fields=["status", "updated_at"])

        def fake_update(_track_import, enqueue_next=True):
            if update_download_statuses.call_count == 1:
                raise URLError("Connection refused")
            item.refresh_from_db()
            item.status = TrackImportItem.STATUS_DONE
            item.save(update_fields=["status", "updated_at"])
            return {"updated": 0, "done": 0, "failed": 0, "queued_next": 0}

        enqueue_source.side_effect = fake_enqueue
        update_download_statuses.side_effect = fake_update

        summary = process_download_round(track_import, limit=0)

        self.assertEqual(summary, {"searched": 1, "queued": 1, "without_source": 0, "cancelled": 0})
        self.assertEqual(update_download_statuses.call_count, 1)
        item.refresh_from_db()
        self.assertEqual(item.status, TrackImportItem.STATUS_DOWNLOADING)

    @override_settings(SLSKD_BASE_URL="http://slskd:5030")
    @patch("apps.downloads.services.urlopen")
    def test_slskd_request_uses_internal_service_url(self, urlopen):
        urlopen.return_value = FakeResponse()

        self.assertEqual(_slskd_request("GET", "/api/v0/session"), {})
        self.assertEqual(urlopen.call_args_list[0].args[0].full_url, "http://slskd:5030/api/v0/session")
