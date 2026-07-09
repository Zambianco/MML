from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import StringIO
from pathlib import Path
from urllib.parse import quote

from django.utils import timezone

from .models import BackupTarget, MediaFile

try:
    import boto3
except ImportError:  # pragma: no cover - optional dependency during local bootstrap
    boto3 = None

try:
    import paramiko
except ImportError:  # pragma: no cover - optional dependency during local bootstrap
    paramiko = None


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class BackupResult:
    storage_path: str
    sha256: str


class BaseBackupStorage:
    def upload(self, *, media_file: MediaFile, source_path: Path) -> BackupResult:
        raise NotImplementedError

    def verify(self, *, media_file: MediaFile, result: BackupResult, source_path: Path) -> None:
        raise NotImplementedError


class S3BackupStorage(BaseBackupStorage):
    def __init__(self, target: BackupTarget):
        if boto3 is None:
            raise BackupError("boto3 nao esta instalado.")
        self.target = target
        self.client = boto3.client(
            "s3",
            region_name=target.aws_region,
            aws_access_key_id=target.aws_access_key_id,
            aws_secret_access_key=target.aws_secret_access_key,
            endpoint_url=target.aws_endpoint_url or None,
        )

    def upload(self, *, media_file: MediaFile, source_path: Path) -> BackupResult:
        key = build_backup_key(media_file=media_file, base_path=self.target.aws_prefix or self.target.base_path)
        extra_args = {
            "Metadata": build_object_metadata(media_file),
            "Tagging": build_object_tagging(media_file),
        }
        self.client.upload_file(str(source_path), self.target.aws_bucket, key, ExtraArgs=extra_args)
        return BackupResult(storage_path=f"s3://{self.target.aws_bucket}/{key}", sha256=media_file.sha256)

    def verify(self, *, media_file: MediaFile, result: BackupResult, source_path: Path) -> None:
        key = build_backup_key(media_file=media_file, base_path=self.target.aws_prefix or self.target.base_path)
        response = self.client.head_object(Bucket=self.target.aws_bucket, Key=key)
        metadata = response.get("Metadata", {})
        remote_sha256 = metadata.get("sha256", "")
        content_length = int(response.get("ContentLength", 0))
        if remote_sha256 != media_file.sha256:
            raise BackupError("Checksum do objeto S3 nao confere com o arquivo original.")
        if content_length != source_path.stat().st_size:
            raise BackupError("Tamanho do objeto S3 nao confere com o arquivo original.")


class SFTPBackupStorage(BaseBackupStorage):
    def __init__(self, target: BackupTarget):
        if paramiko is None:
            raise BackupError("paramiko nao esta instalado.")
        self.target = target

    def upload(self, *, media_file: MediaFile, source_path: Path) -> BackupResult:
        destination = build_backup_key(media_file=media_file, base_path=self.target.sftp_remote_path or self.target.base_path)
        transport = paramiko.Transport((self.target.sftp_host, self.target.sftp_port or 22))
        try:
            if self.target.sftp_private_key:
                private_key = load_private_key(self.target.sftp_private_key)
                transport.connect(username=self.target.sftp_username, pkey=private_key)
            else:
                transport.connect(username=self.target.sftp_username, password=self.target.sftp_password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            try:
                ensure_sftp_directory(sftp, destination)
                sftp.put(str(source_path), destination)
            finally:
                sftp.close()
        finally:
            transport.close()
        return BackupResult(storage_path=f"sftp://{self.target.sftp_host}{destination}", sha256=media_file.sha256)

    def verify(self, *, media_file: MediaFile, result: BackupResult, source_path: Path) -> None:
        destination = build_backup_key(media_file=media_file, base_path=self.target.sftp_remote_path or self.target.base_path)
        transport = paramiko.Transport((self.target.sftp_host, self.target.sftp_port or 22))
        try:
            if self.target.sftp_private_key:
                private_key = load_private_key(self.target.sftp_private_key)
                transport.connect(username=self.target.sftp_username, pkey=private_key)
            else:
                transport.connect(username=self.target.sftp_username, password=self.target.sftp_password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            try:
                remote_stat = sftp.stat(destination)
                if remote_stat.st_size != source_path.stat().st_size:
                    raise BackupError("Tamanho do arquivo SFTP nao confere com o original.")
                remote_sha256 = calculate_remote_sha256(sftp=sftp, destination=destination)
                if remote_sha256 != media_file.sha256:
                    raise BackupError("Checksum do arquivo SFTP nao confere com o original.")
            finally:
                sftp.close()
        finally:
            transport.close()


def build_backup_storage(target: BackupTarget) -> BaseBackupStorage:
    if target.backend_type == BackupTarget.BackendType.S3:
        return S3BackupStorage(target)
    if target.backend_type == BackupTarget.BackendType.SFTP:
        return SFTPBackupStorage(target)
    raise BackupError("Backend de backup nao suportado.")


def backup_media_file(*, media_file: MediaFile, target: BackupTarget | None = None) -> BackupResult:
    backup_target = target or media_file.backup_target or BackupTarget.objects.filter(is_active=True, is_default=True).first()
    if backup_target is None:
        raise BackupError("Nenhum destino de backup padrao configurado.")

    source_path = resolve_media_file_path(media_file)
    if not source_path.is_file():
        raise BackupError("Arquivo original nao encontrado para backup.")

    media_file.backup_target = backup_target
    media_file.original_backup_status = MediaFile.BackupStatus.PENDING
    media_file.save(update_fields=["backup_target", "original_backup_status", "updated_at"])

    storage = build_backup_storage(backup_target)
    result = storage.upload(media_file=media_file, source_path=source_path)

    media_file.original_backup_path = result.storage_path
    media_file.original_backup_sha256 = result.sha256
    media_file.original_backup_status = MediaFile.BackupStatus.UPLOADED
    media_file.save(
        update_fields=[
            "original_backup_path",
            "original_backup_sha256",
            "original_backup_status",
            "updated_at",
        ]
    )

    storage.verify(media_file=media_file, result=result, source_path=source_path)

    media_file.original_backup_status = MediaFile.BackupStatus.CONFIRMED
    media_file.original_backed_up_at = timezone.now()
    media_file.save(
        update_fields=[
            "original_backup_path",
            "original_backup_sha256",
            "original_backup_status",
            "original_backed_up_at",
            "updated_at",
        ]
    )
    return result


def pending_backup_queryset():
    return MediaFile.objects.filter(
        origin_type=MediaFile.OriginType.ORIGINAL,
        audio_format="flac",
    ).exclude(original_backup_status=MediaFile.BackupStatus.CONFIRMED)


def resolve_media_file_path(media_file: MediaFile) -> Path:
    for candidate in (media_file.path, media_file.source_path):
        if candidate:
            path = Path(candidate)
            if path.is_file():
                return path
    return Path(media_file.path or media_file.source_path or "")


def build_backup_key(*, media_file: MediaFile, base_path: str) -> str:
    extension = f".{media_file.audio_format}" if media_file.audio_format else Path(media_file.path).suffix.lower() or ".bin"
    prefix = base_path.strip().strip("/") if base_path else "flac"
    return f"/{prefix}/{media_file.sha256[:2]}/{media_file.sha256}{extension}".replace("//", "/")


def build_object_metadata(media_file: MediaFile) -> dict[str, str]:
    return {
        "sha256": media_file.sha256,
        "track_id": str(media_file.track_id or ""),
        "audio_format": media_file.audio_format,
        "origin_type": media_file.origin_type,
        "acoustic_fingerprint_hash": media_file.acoustic_fingerprint_hash,
    }


def build_object_tagging(media_file: MediaFile) -> str:
    tags = {
        "backup_status": MediaFile.BackupStatus.CONFIRMED,
        "media_type": media_file.audio_format or "audio",
        "library": "mml",
    }
    return "&".join(f"{quote(key)}={quote(value)}" for key, value in tags.items() if value)


def ensure_sftp_directory(sftp, destination: str) -> None:
    current = ""
    for part in Path(destination).parent.parts:
        current = f"{current}/{part}".replace("//", "/")
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def calculate_remote_sha256(*, sftp, destination: str) -> str:
    digest = hashlib.sha256()
    with sftp.open(destination, "rb") as remote_file:
        for chunk in iter(lambda: remote_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_private_key(private_key_text: str):
    if paramiko is None:
        raise BackupError("paramiko nao esta instalado.")

    normalized = private_key_text.replace("\\n", "\n").strip()
    if not normalized:
        raise BackupError("Chave privada vazia.")

    key_classes = [getattr(paramiko, name, None) for name in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey")]
    last_error: Exception | None = None
    for key_class in key_classes:
        if key_class is None:
            continue
        try:
            return key_class.from_private_key(StringIO(normalized))
        except Exception as exc:  # pragma: no cover - depends on key type/runtime
            last_error = exc

    raise BackupError("Nao foi possivel ler a chave privada SSH. Verifique se ela e uma chave privada valida.") from last_error
