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
    directory = models.ForeignKey(MonitoredDirectory, on_delete=models.PROTECT, related_name="media_files")
    track = models.ForeignKey("library.Track", on_delete=models.SET_NULL, related_name="media_files", blank=True, null=True)
    path = models.TextField(unique=True)
    size_bytes = models.PositiveBigIntegerField(blank=True, null=True)
    mime_type = models.CharField(max_length=120, blank=True)
    checksum = models.CharField(max_length=128, blank=True, db_index=True)
    duration_ms = models.PositiveIntegerField(blank=True, null=True)
    bitrate_kbps = models.PositiveIntegerField(blank=True, null=True)
    discovered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["path"]

    def __str__(self) -> str:
        return self.path
