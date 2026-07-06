from django.test import Client, SimpleTestCase, override_settings


@override_settings(ALLOWED_HOSTS=["testserver"])
class LoginRedirectPrefixTests(SimpleTestCase):
    def test_redirect_preserves_forwarded_prefix(self):
        response = Client().get("/", HTTP_X_FORWARDED_PREFIX="/mml")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/mml/login/?next=/mml/")

    def test_redirect_preserves_url_prefix_path(self):
        response = Client().get("/mml/downloads/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/mml/login/?next=/mml/downloads/")
