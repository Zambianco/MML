from django.conf import settings
from django.db import models


class FavoriteTrack(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorite_tracks")
    file_path = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "file_path"], name="unique_favorite_track_per_user"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.file_path}"
