from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from rest_framework.authtoken.models import Token


@override_settings(ALLOWED_HOSTS=["testserver"])
class ApiAuthenticationTests(TestCase):
    def test_bearer_token_is_accepted_for_app_requests(self):
        user = get_user_model().objects.create_user(username="app", password="secret")
        token = Token.objects.create(user=user)

        response = Client().get("/api/player/tracks/", HTTP_AUTHORIZATION=f"Bearer {token.key}")

        self.assertNotEqual(response.status_code, 401)
