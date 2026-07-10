from django.core.exceptions import ValidationError
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


class BackupTarget(models.Model):
    class BackendType(models.TextChoices):
        S3 = "s3", "S3"
        SFTP = "sftp", "SFTP"

    name = models.CharField(max_length=120, unique=True)
    backend_type = models.CharField(max_length=20, choices=BackendType.choices)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    base_path = models.TextField(blank=True)
    aws_bucket = models.CharField(max_length=120, blank=True)
    aws_region = models.CharField(max_length=60, blank=True)
    aws_access_key_id = models.CharField(max_length=120, blank=True)
    aws_secret_access_key = models.CharField(max_length=255, blank=True)
    aws_endpoint_url = models.TextField(blank=True)
    aws_prefix = models.TextField(blank=True)
    sftp_host = models.CharField(max_length=255, blank=True)
    sftp_port = models.PositiveIntegerField(blank=True, null=True)
    sftp_username = models.CharField(max_length=120, blank=True)
    sftp_password = models.CharField(max_length=255, blank=True)
    sftp_private_key = models.TextField(blank=True)
    sftp_remote_path = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        errors = {}
        if self.backend_type == self.BackendType.S3:
            if not self.aws_bucket:
                errors["aws_bucket"] = "Informe o bucket da AWS."
            if not self.aws_region:
                errors["aws_region"] = "Informe a regiao da AWS."
            if not self.aws_access_key_id:
                errors["aws_access_key_id"] = "Informe a access key da AWS."
            if not self.aws_secret_access_key:
                errors["aws_secret_access_key"] = "Informe a secret key da AWS."
        elif self.backend_type == self.BackendType.SFTP:
            if not self.sftp_host:
                errors["sftp_host"] = "Informe o host SFTP."
            if not self.sftp_username:
                errors["sftp_username"] = "Informe o usuario SFTP."
            if not self.sftp_remote_path:
                errors["sftp_remote_path"] = "Informe o caminho remoto SFTP."
            if not self.sftp_password and not self.sftp_private_key:
                errors["sftp_password"] = "Informe senha ou chave privada do SFTP."
        if errors:
            raise ValidationError(errors)


class MediaFile(models.Model):
    class ImportStatus(models.TextChoices):
        IMPORTED = "imported", "Imported"
        DUPLICATE = "duplicate", "Duplicate"
        REVIEW = "review", "Review"
        ERROR = "error", "Error"

    class OriginType(models.TextChoices):
        ORIGINAL = "original", "Original"
        DERIVED = "derived", "Derived"

    class BackupStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        UPLOADED = "uploaded", "Uploaded"
        CONFIRMED = "confirmed", "Confirmed"
        FAILED = "failed", "Failed"

    class TranscodeStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    directory = models.ForeignKey(MonitoredDirectory, on_delete=models.PROTECT, related_name="media_files")
    track = models.ForeignKey("library.Track", on_delete=models.SET_NULL, related_name="media_files", blank=True, null=True)
    backup_target = models.ForeignKey("BackupTarget", on_delete=models.SET_NULL, related_name="media_files", blank=True, null=True)
    duplicate_of = models.ForeignKey("self", on_delete=models.SET_NULL, related_name="duplicates", blank=True, null=True)
    path = models.TextField(unique=True)
    source_path = models.TextField(blank=True)
    storage_path = models.TextField(blank=True)
    audio_format = models.CharField(max_length=20, blank=True)
    origin_type = models.CharField(max_length=20, choices=OriginType.choices, default=OriginType.ORIGINAL)
    is_master = models.BooleanField(default=True)
    transcode_status = models.CharField(max_length=20, choices=TranscodeStatus.choices, blank=True)
    transcode_error = models.TextField(blank=True)
    original_backup_path = models.TextField(blank=True)
    original_backup_status = models.CharField(max_length=20, choices=BackupStatus.choices, blank=True)
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
