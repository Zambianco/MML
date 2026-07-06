import csv
import io
import re
import unicodedata
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import TrackImport, TrackImportItem

REQUIRED_HEADERS = {"name", "artists"}
HEADER_ALIASES = {
    "nome": "name",
    "artista": "artists",
    "artistas": "artists",
    "album": "album",
    "ano": "year",
    "isrc": "isrc",
}
ISRC_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")


@dataclass
class ParsedImportRow:
    row_number: int
    name: str
    artists: str
    album: str
    year: int | None
    isrc: str
    search_query: str


@dataclass
class ParsedImportFile:
    delimiter: str
    rows: list[ParsedImportRow]


def _normalize_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return normalized.strip().lower()


def _detect_delimiter(content: str) -> str:
    header_line = content.splitlines()[0] if content.splitlines() else ""
    return ";" if header_line.count(";") >= header_line.count(",") else ","


def _quote_term(value: str) -> str:
    return f'"{value}"' if any(char.isspace() for char in value) or "," in value else value


def build_slskd_search_query(*, name: str, artists: str, album: str = "", year: int | None = None, isrc: str = "") -> str:
    terms: list[str] = []
    for value in (artists.strip(), name.strip(), album.strip()):
        if value and value not in terms:
            terms.append(value)
    query_parts = [_quote_term(value) for value in terms]
    if year is not None:
        query_parts.append(str(year))
    if isrc:
        query_parts.append(isrc)
    return " ".join(query_parts)


def parse_track_import_csv(uploaded_file) -> ParsedImportFile:
    try:
        content = uploaded_file.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("Nao foi possivel ler o CSV em UTF-8.") from exc

    if not content.strip():
        raise ValidationError("O arquivo CSV esta vazio.")

    delimiter = _detect_delimiter(content)
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValidationError("O CSV precisa ter cabecalho.")

    normalized_headers: dict[str, str] = {}
    for header in reader.fieldnames:
        canonical = HEADER_ALIASES.get(_normalize_header(header))
        if canonical:
            normalized_headers[header] = canonical

    missing_headers = REQUIRED_HEADERS.difference(normalized_headers.values())
    if missing_headers:
        raise ValidationError("O CSV precisa conter as colunas Nome e Artista(s).")

    rows: list[ParsedImportRow] = []
    for index, raw_row in enumerate(reader, start=2):
        row = {normalized_headers[key]: (value or "").strip() for key, value in raw_row.items() if key in normalized_headers}
        name = row.get("name", "")
        artists = row.get("artists", "")
        album = row.get("album", "")
        year_value = row.get("year", "")
        isrc = row.get("isrc", "").replace(" ", "").replace("-", "").upper()

        if not name or not artists:
            raise ValidationError(f"Linha {index}: Nome e Artista(s) sao obrigatorios.")

        year: int | None = None
        if year_value:
            if not year_value.isdigit() or len(year_value) != 4:
                raise ValidationError(f"Linha {index}: Ano precisa ter 4 digitos.")
            year = int(year_value)

        if isrc and not ISRC_PATTERN.fullmatch(isrc):
            raise ValidationError(f"Linha {index}: ISRC invalido.")

        rows.append(
            ParsedImportRow(
                row_number=index,
                name=name,
                artists=artists,
                album=album,
                year=year,
                isrc=isrc,
                search_query=build_slskd_search_query(
                    name=name,
                    artists=artists,
                    album=album,
                    year=year,
                    isrc=isrc,
                ),
            )
        )

    if not rows:
        raise ValidationError("O CSV nao possui linhas de musica.")

    return ParsedImportFile(delimiter=delimiter, rows=rows)


@transaction.atomic
def create_track_import(uploaded_file) -> TrackImport:
    parsed_file = parse_track_import_csv(uploaded_file)
    track_import = TrackImport.objects.create(
        source_name=uploaded_file.name,
        delimiter=parsed_file.delimiter,
        item_count=len(parsed_file.rows),
    )
    TrackImportItem.objects.bulk_create(
        [
            TrackImportItem(
                track_import=track_import,
                row_number=row.row_number,
                name=row.name,
                artists=row.artists,
                album=row.album,
                year=row.year,
                isrc=row.isrc,
                search_query=row.search_query,
            )
            for row in parsed_file.rows
        ]
    )
    return track_import
