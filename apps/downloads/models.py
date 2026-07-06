from django.db import models


class TrackImport(models.Model):
    source_name = models.CharField(max_length=255)
    delimiter = models.CharField(max_length=1, default=";")
    item_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.source_name


class TrackImportItem(models.Model):
    STATUS_PENDING = "pending"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendente"),
    ]

    track_import = models.ForeignKey(TrackImport, on_delete=models.CASCADE, related_name="items")
    row_number = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    artists = models.CharField(max_length=255)
    album = models.CharField(max_length=255, blank=True)
    year = models.PositiveSmallIntegerField(blank=True, null=True)
    isrc = models.CharField(max_length=12, blank=True)
    search_query = models.CharField(max_length=600)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["row_number", "id"]
        constraints = [
            models.UniqueConstraint(fields=["track_import", "row_number"], name="unique_import_row_number"),
        ]

    def __str__(self) -> str:
        return f"{self.artists} - {self.name}"
