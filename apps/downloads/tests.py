import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from urllib.error import URLError
from unittest.mock import patch

from .models import TrackImport, TrackImportItem
from .services import _slskd_request, build_slskd_search_query, process_download_round, score_slskd_source, search_slskd_sources


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
    def test_import_detail_refreshes_statuses_and_fragment_polls(self, update_download_statuses):
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
        fragment = self.client.get(reverse("downloads-import-detail-fragment", args=[track_import.pk]), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fragment.status_code, 200)
        self.assertEqual(update_download_statuses.call_count, 2)
        self.assertContains(fragment, 'hx-trigger="load, every 10s"')

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

    @patch("apps.downloads.views.process_download_round")
    def test_process_round_uses_limit(self, process_download_round):
        process_download_round.return_value = {"searched": 2, "queued": 2, "without_source": 0}
        track_import = TrackImport.objects.create(source_name="downloads.csv", item_count=2)

        response = self.client.post(
            reverse("downloads-process-round", args=[track_import.pk]),
            {"limit": "2"},
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, reverse("downloads-import-detail", args=[track_import.pk]))
        process_download_round.assert_called_once_with(track_import, limit=2)

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
        self.assertEqual(item.search_query, "Mortemia Frozen 2022")
        self.assertEqual(slskd_request.call_args_list[0].args[2]["searchText"], "Mortemia Frozen 2022")

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

    @override_settings(SLSKD_BASE_URL="http://slskd:5030")
    @patch("apps.downloads.services.urlopen")
    def test_slskd_request_falls_back_to_localhost(self, urlopen):
        urlopen.side_effect = [URLError("getaddrinfo failed"), FakeResponse()]

        self.assertEqual(_slskd_request("GET", "/api/v0/session"), {})
        self.assertIn("http://localhost:5030/api/v0/session", urlopen.call_args_list[1].args[0].full_url)
