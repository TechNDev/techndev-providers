#!/usr/bin/env python3
"""
check_encoding  v1.0.0
======================
Encoding-Guard fuer TechNDev-Repos: prueft die *staged* Textdateien vor jedem
Commit auf drei Fehlerbilder, die sonst still einchecken und Nutzdaten (z. B.
Service-Namen) verstuemmeln:

  1. UTF-8-BOM (EF BB BF am Dateianfang)  -> abgelehnt
  2. kein gueltiges UTF-8                 -> abgelehnt
  3. Doppel-Encoding / Mojibake           -> abgelehnt
     (klassisch: UTF-8-Bytes als cp1252/latin-1 gelesen und erneut als UTF-8
      gespeichert -> ein High-Byte-Zeichen wird zu U+00C2/U+00C3/U+00E2 gefolgt
      von einem weiteren High-/Sonderzeichen.)

Aktivierung pro Klon (einmalig):
    git config core.hooksPath .githooks

Direktaufruf zum Pruefen des Staging-Bereichs:
    python .githooks/check_encoding.py

Quelle bewusst rein ASCII (Mojibake-Marker aus Codepoints via chr() gebaut),
damit der Checker sich nicht selbst als Treffer meldet.

CHANGELOG
---------
v1.0.0  (2026-07-07)
  - Initial: BOM- + UTF-8-Validitaet + Mojibake-Bigramm-Heuristik ueber staged
    Blobs (git show :path). Skippt Binaerdateien (NUL-Byte / bekannte Endungen).
"""

from __future__ import annotations

import re
import subprocess
import sys

__version__ = "1.0.0"

# -- Mojibake-Signatur --------------------------------------------------------
# Bigramm: erstes Zeichen U+00C2/U+00C3/U+00E2, unmittelbar gefolgt von einem
# cp1252-High-/Sonderzeichen (U+00A0..U+00FF plus die cp1252-Punktuation aus
# 0x80..0x9F). Legitime Umlaute (einzelne Codepoints wie U+00E4) oder ein
# alleinstehendes Mal-Zeichen/Gedankenstrich matchen NICHT -- ihnen geht kein
# C2/C3/E2 voraus. Klassen aus Codepoints gebaut -> Quelle bleibt rein ASCII.
_FIRST = (0x00C2, 0x00C3, 0x00E2)
_SECOND_PUNCT = (
    0x20AC, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6,
    0x2030, 0x0160, 0x2039, 0x0152, 0x017D, 0x2018, 0x2019, 0x201C,
    0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A,
    0x0153, 0x017E, 0x0178,
)
_first_cls = "".join(chr(c) for c in _FIRST)
_second_cls = chr(0x00A0) + "-" + chr(0x00FF) + "".join(chr(c) for c in _SECOND_PUNCT)
_MOJIBAKE = re.compile("[" + _first_cls + "][" + _second_cls + "]")

# Endungen, die nie als Text geprueft werden (deckt .gitattributes-binary ab).
_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".xlsx", ".xls",
    ".db", ".mp3", ".mp4", ".wav", ".zip", ".woff", ".woff2", ".ttf",
    ".otf", ".eot", ".gz", ".bz2", ".7z", ".exe", ".dll", ".so", ".pyc",
}


def _staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"],
        capture_output=True, check=True,
    ).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def _staged_blob(path: str) -> bytes:
    # Inhalt so pruefen, wie er tatsaechlich eingecheckt wird (Index, nicht Worktree).
    return subprocess.run(
        ["git", "show", f":{path}"], capture_output=True, check=True,
    ).stdout


def _lower_ext(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot != -1 else ""


def check_file(path: str, data: bytes) -> list[str]:
    """Liste menschenlesbarer Fehlermeldungen (leer = ok)."""
    if _lower_ext(path) in _BINARY_EXT or b"\x00" in data:
        return []                                   # Binaer -> nicht pruefen

    problems: list[str] = []
    if data.startswith(b"\xef\xbb\xbf"):
        problems.append(f"{path}: UTF-8-BOM am Dateianfang (EF BB BF)")
        data = data[3:]

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        problems.append(f"{path}: kein gueltiges UTF-8 ({e})")
        return problems

    for lineno, line in enumerate(text.splitlines(), 1):
        m = _MOJIBAKE.search(line)
        if m:
            snippet = line.strip()
            if len(snippet) > 80:
                snippet = snippet[:77] + "..."
            problems.append(
                f"{path}:{lineno}: Mojibake / Doppel-Encoding erkannt "
                f"(U+{ord(m.group()[0]):04X} U+{ord(m.group()[1]):04X}): {snippet}"
            )
    return problems


def main() -> int:
    try:
        files = _staged_files()
    except subprocess.CalledProcessError as e:
        print(f"encoding-guard: git-Aufruf fehlgeschlagen: {e}", file=sys.stderr)
        return 0                                     # nie den Commit wegen Toolfehler blocken

    problems: list[str] = []
    for path in files:
        try:
            problems += check_file(path, _staged_blob(path))
        except subprocess.CalledProcessError:
            continue                                 # geloeschte/unlesbare Blobs ignorieren

    if problems:
        print("encoding-guard: Commit abgelehnt (BOM / Mojibake / kein UTF-8):",
              file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        print("\nDatei sauber als UTF-8 ohne BOM speichern. Reparatur-Rezept fuer\n"
              "doppelt kodierte Dateien: Bytes utf-8-sig lesen -> cp1252 encoden ->\n"
              "utf-8 decoden. Notfalls Pruefung mit  git commit --no-verify  umgehen.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
