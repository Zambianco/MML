from __future__ import annotations

import base64
import mimetypes
import subprocess
from pathlib import Path

from django.conf import settings

from .models import MediaFile
from .services import media_file_relative_path

from apps.scanner.services import calculate_sha256, extract_audio_metadata

try:
    from mutagen.flac import FLAC, Picture
    from mutagen.oggopus import OggOpus
except ImportError:  # pragma: no cover - optional dependency during local bootstrap
    FLAC = None
    OggOpus = None
    Picture = None

LOCAL_COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp", "folder.jpg", "folder.jpeg", "folder.png", "album.jpg", "album.jpeg", "album.png")


class TranscodeError(Exception):
    pass


def transcode_media_file_to_opus(*, media_file: MediaFile, bitrate_kbps: int = 128) -> MediaFile:
    if media_file.origin_type != MediaFile.OriginType.ORIGINAL or media_file.audio_format != "flac":
        raise TranscodeError("Apenas arquivos FLAC originais podem ser convertidos para Opus.")

    existing = (
        MediaFile.objects.filter(
            track=media_file.track,
            origin_type=MediaFile.OriginType.DERIVED,
            audio_format="opus",
        )
        .order_by("-updated_at")
        .first()
    )
    if existing is not None and Path(existing.path).is_file():
        media_file.transcode_status = MediaFile.TranscodeStatus.COMPLETED
        media_file.transcode_error = ""
        media_file.save(update_fields=["transcode_status", "transcode_error", "updated_at"])
        return existing

    source_path = Path(media_file.path or media_file.source_path)
    if not source_path.is_file():
        raise TranscodeError("Arquivo FLAC original nao encontrado.")

    output_path = derived_opus_path(media_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-map",
            "0:a:0",
            "-c:a",
            "libopus",
            "-b:a",
            f"{bitrate_kbps}k",
            "-vn",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    copy_flac_metadata_and_cover_to_opus(source_path=source_path, output_path=output_path)
    metadata = extract_audio_metadata(output_path)
    sha256 = calculate_sha256(output_path)
    relative_path = media_file_relative_path(output_path)
    derived_file, _ = MediaFile.objects.update_or_create(
        path=str(output_path),
        defaults={
            "directory": media_file.directory,
            "track": media_file.track,
            "source_path": str(output_path),
            "storage_path": relative_path,
            "audio_format": "opus",
            "origin_type": MediaFile.OriginType.DERIVED,
            "is_master": False,
            "size_bytes": output_path.stat().st_size,
            "mime_type": metadata.mime_type or "audio/ogg",
            "checksum": sha256,
            "sha256": sha256,
            "duration_ms": metadata.duration_ms,
            "bitrate_kbps": metadata.bitrate_kbps,
            "acoustic_fingerprint": metadata.acoustic_fingerprint,
            "acoustic_fingerprint_hash": metadata.acoustic_fingerprint_hash,
            "import_status": MediaFile.ImportStatus.IMPORTED,
        },
    )
    media_file.transcode_status = MediaFile.TranscodeStatus.COMPLETED
    media_file.transcode_error = ""
    media_file.save(update_fields=["transcode_status", "transcode_error", "updated_at"])
    return derived_file


def derived_opus_path(media_file: MediaFile) -> Path:
    if media_file.storage_path:
        return Path(settings.MUSIC_STORAGE_ROOT) / Path(media_file.storage_path).with_suffix(".opus")
    return Path(media_file.path).with_suffix(".opus")


def copy_flac_metadata_and_cover_to_opus(*, source_path: Path, output_path: Path) -> None:
    if FLAC is None or OggOpus is None or Picture is None:
        raise TranscodeError("Mutagen nao esta disponivel para copiar metadados do FLAC para Opus.")

    source = FLAC(source_path)
    target = OggOpus(output_path)
    target.clear()

    for key, values in (source.tags or {}).items():
        target[key] = [str(value) for value in values]

    pictures = list(getattr(source, "pictures", []))
    if not pictures:
        local_cover = local_cover_candidate(source_path)
        if local_cover is not None:
            pictures.append(picture_from_local_cover(local_cover))
    if pictures:
        target["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii") for picture in pictures]

    target.save()


def local_cover_candidate(path: Path) -> Path | None:
    for name in LOCAL_COVER_NAMES:
        candidate = path.with_name(name)
        if candidate.is_file():
            return candidate
    return None


def picture_from_local_cover(path: Path) -> Picture:
    picture = Picture()
    picture.type = 3
    picture.mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    picture.desc = "Cover"
    picture.data = path.read_bytes()
    return picture
