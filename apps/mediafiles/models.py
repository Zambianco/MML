from django.db import models


class MonitoredDirectory(models.Model):
    name = models.CharField(max_length=120)
    path = models.TextField(unique=True)
    is_active = models.BooleanField(default=True)
    last_scan_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "path"]
        verbose_name_plural = "monitored directories"

    def __str__(self) -> str:
        return self.name


class MediaFile(models.Model):
    class ImportStatus(models.TextChoices):
        IMPORTED = "imported", "Imported"
        DUPLICATE = "duplicate", "Duplicate"
        REVIEW = "review", "Review"
        ERROR = "error", "Error"

    class OriginType(models.TextChoices):
        ORIGINAL = "original", "Original"
        DERIVED = "derived", "Derived"

    directory = models.ForeignKey(MonitoredDirectory, on_delete=models.PROTECT, related_name="media_files")
    track = models.ForeignKey("library.Track", on_delete=models.SET_NULL, related_name="media_files", blank=True, null=True)
    duplicate_of = models.ForeignKey("self", on_delete=models.SET_NULL, related_name="duplicates", blank=True, null=True)
    path = models.TextField(unique=True)
    source_path = models.TextField(blank=True)
    storage_path = models.TextField(blank=True)
    audio_format = models.CharField(max_length=20, blank=True)
    origin_type = models.CharField(max_length=20, choices=OriginType.choices, default=OriginType.ORIGINAL)
    is_master = models.BooleanField(default=True)
    original_backup_path = models.TextField(blank=True)
    original_backup_status = models.CharField(max_length=20, blank=True)
    original_backup_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    original_backed_up_at = models.DateTimeField(blank=True, null=True)
    size_bytes = models.PositiveBigIntegerField(blank=True, null=True)
    mime_type = models.CharField(max_length=120, blank=True)
    checksum = models.CharField(max_length=128, blank=True, db_index=True)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    duration_ms = models.PositiveIntegerField(blank=True, null=True)
    bitrate_kbps = models.PositiveIntegerField(blank=True, null=True)
    acoustic_fingerprint = models.TextField(blank=True)
    acoustic_fingerprint_hash = models.CharField(max_length=64, blank=True, db_index=True)
    duplicate_confidence = models.PositiveSmallIntegerField(default=0)
    duplicate_reason = models.CharField(max_length=40, blank=True)
    needs_review = models.BooleanField(default=False)
    import_status = models.CharField(max_length=20, choices=ImportStatus.choices, default=ImportStatus.IMPORTED)
    discovered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["storage_path"],
                condition=~models.Q(storage_path=""),
                name="unique_media_file_storage_path",
            ),
        ]

    def __str__(self) -> str:
        return self.path
