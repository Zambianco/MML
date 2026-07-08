import mimetypes
import re
from collections.abc import Iterator
from pathlib import Path

from django.http import FileResponse, HttpRequest, HttpResponse, StreamingHttpResponse


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
CHUNK_SIZE = 64 * 1024


def _read_range(path: Path, start: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as audio_file:
        audio_file.seek(start)
        remaining = length
        while remaining > 0:
            chunk = audio_file.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def stream_audio_file(request: HttpRequest, file_path: Path) -> HttpResponse:
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range", "").strip()
    match = RANGE_RE.match(range_header)

    if match:
        start_raw, end_raw = match.groups()
        if start_raw == "" and end_raw == "":
            return _range_not_satisfiable(file_size)
        if start_raw == "":
            suffix_length = int(end_raw)
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else file_size - 1
        if start >= file_size or end < start:
            return _range_not_satisfiable(file_size)
        end = min(end, file_size - 1)
        length = end - start + 1
        response = StreamingHttpResponse(
            _read_range(file_path, start, length),
            status=206,
            content_type=content_type,
        )
        response.headers["Content-Length"] = str(length)
        response.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    else:
        response = FileResponse(file_path.open("rb"), as_attachment=False, filename=file_path.name, content_type=content_type)
        response.headers["Content-Length"] = str(file_size)

    response.headers["Accept-Ranges"] = "bytes"
    return response


def _range_not_satisfiable(file_size: int) -> HttpResponse:
    response = HttpResponse(status=416)
    response.headers["Content-Range"] = f"bytes */{file_size}"
    return response
