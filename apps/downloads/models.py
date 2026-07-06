from django.db import models
from django.utils import timezone


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
    SEARCH_QUERY_AUTO = "auto"
    SEARCH_QUERY_MANUAL = "manual"
    SEARCH_QUERY_MODE_CHOICES = [
        (SEARCH_QUERY_AUTO, "Automatica"),
        (SEARCH_QUERY_MANUAL, "Manual"),
    ]

    STATUS_PENDING = "pending"
    STATUS_SEARCHING = "searching"
    STATUS_DOWNLOADING = "downloading"
    STATUS_DONE = "done"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendente"),
        (STATUS_SEARCHING, "Buscando"),
        (STATUS_DOWNLOADING, "Baixando"),
        (STATUS_DONE, "Concluido"),
        (STATUS_ERROR, "Erro"),
    ]

    track_import = models.ForeignKey(TrackImport, on_delete=models.CASCADE, related_name="items")
    row_number = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    artists = models.CharField(max_length=255)
    album = models.CharField(max_length=255, blank=True)
    year = models.PositiveSmallIntegerField(blank=True, null=True)
    isrc = models.CharField(max_length=12, blank=True)
    search_query = models.CharField(max_length=600)
    search_query_mode = models.CharField(max_length=10, choices=SEARCH_QUERY_MODE_CHOICES, default=SEARCH_QUERY_AUTO)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    download_progress = models.PositiveSmallIntegerField(default=0)
    download_path = models.CharField(max_length=1000, blank=True)
    search_slskd_id = models.CharField(max_length=120, blank=True)
    search_state = models.CharField(max_length=80, blank=True)
    search_response_count = models.PositiveIntegerField(default=0)
    search_started_at = models.DateTimeField(blank=True, null=True)
    search_finished_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["row_number", "id"]
        constraints = [
            models.UniqueConstraint(fields=["track_import", "row_number"], name="unique_import_row_number"),
        ]

    def __str__(self) -> str:
        return f"{self.artists} - {self.name}"


class TrackImportItemSource(models.Model):
    item = models.ForeignKey(TrackImportItem, on_delete=models.CASCADE, related_name="sources")
    rank = models.PositiveIntegerField()
    username = models.CharField(max_length=255)
    remote_filename = models.CharField(max_length=1000)
    extension = models.CharField(max_length=20, blank=True)
    sample_rate = models.PositiveIntegerField(blank=True, null=True)
    bit_depth = models.PositiveSmallIntegerField(blank=True, null=True)
    duration_seconds = models.PositiveIntegerField(blank=True, null=True)
    size_bytes = models.BigIntegerField(blank=True, null=True)
    score = models.FloatField(default=0)
    queue_length = models.PositiveIntegerField(default=0)
    upload_speed = models.BigIntegerField(default=0)
    download_requested_at = models.DateTimeField(blank=True, null=True)
    download_state = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["item", "rank", "-score"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "username", "remote_filename", "size_bytes"],
                name="unique_import_item_source",
            ),
        ]

    def mark_requested(self) -> None:
        self.download_requested_at = timezone.now()
        self.download_state = "enfileirado"
        self.save(update_fields=["download_requested_at", "download_state", "updated_at"])

    def __str__(self) -> str:
        return f"{self.username} - {self.remote_filename}"
