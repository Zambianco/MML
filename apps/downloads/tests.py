from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import TrackImport, TrackImportItem
from .services import build_slskd_search_query


class DownloadImportTests(TestCase):
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
                search_query='"Mortemia, Federica Lanna" Frozen 2022 QZTAU2295243',
            ).exists()
        )

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
