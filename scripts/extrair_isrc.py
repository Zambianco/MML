"""Gera um manifesto CSV (ISRC;Nome;Artista(s);Album) a partir das tags dos FLAC de uma pasta.

Uso: python scripts/extrair_isrc.py <pasta> [saida.csv]
"""
import csv
import sys
from pathlib import Path

from mutagen.flac import FLAC


def first(tags, key):
    values = tags.get(key) or []
    return str(values[0]).strip() if values else ""


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else folder / "manifesto-isrc.csv"

    rows: dict[str, tuple[str, str, str]] = {}
    total = 0
    without_isrc = []
    for path in folder.rglob("*.flac"):
        total += 1
        try:
            tags = FLAC(path).tags or {}
        except Exception as exc:
            print(f"Erro ao ler {path}: {exc}")
            continue
        isrc = first(tags, "isrc").replace("-", "").replace(" ", "").upper()
        if not isrc:
            without_isrc.append(path)
            continue
        rows.setdefault(isrc, (first(tags, "title"), first(tags, "artist"), first(tags, "album")))

    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["ISRC", "Nome", "Artista(s)", "Album"])
        for isrc, (title, artist, album) in sorted(rows.items()):
            writer.writerow([isrc, title, artist, album])

    print(f"FLAC analisados: {total}")
    print(f"ISRCs unicos: {len(rows)}")
    print(f"Sem ISRC na tag: {len(without_isrc)}")
    print(f"Arquivo: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
