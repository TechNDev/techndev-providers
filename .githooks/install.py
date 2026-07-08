#!/usr/bin/env python3
"""
install  v1.0.0
===============
Aktiviert den TechNDev-Encoding-Guard in DIESEM Klon:

    git config core.hooksPath .githooks

Hintergrund: core.hooksPath ist klon-lokale Git-Konfiguration (nicht versioniert
und aus Sicherheitsgruenden nicht automatisch beim Klonen setzbar). Jede frische
Arbeitskopie muss den Guard daher einmal aktivieren. Danach laeuft
.githooks/pre-commit bei jedem Commit und lehnt BOM / ungueltiges UTF-8 /
Doppel-Encoding (Mojibake) ab.

Aufruf (aus dem Repo-Wurzelverzeichnis):

    python .githooks/install.py

CHANGELOG
---------
v1.0.0  (2026-07-07)
  - Initial: setzt core.hooksPath=.githooks und bestaetigt die Aktivierung.
"""

from __future__ import annotations

import subprocess
import sys

__version__ = "1.0.0"


def main() -> int:
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=True, capture_output=True,
        )
    except FileNotFoundError:
        print("Fehler: 'git' nicht im PATH gefunden.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError:
        print("Fehler: kein Git-Arbeitsverzeichnis (bitte im Repo-Root ausfuehren).",
              file=sys.stderr)
        return 1

    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], check=True)
    cur = subprocess.run(
        ["git", "config", "core.hooksPath"],
        capture_output=True, text=True,
    ).stdout.strip()
    print(f"Encoding-Guard aktiviert: core.hooksPath={cur}")
    print("Selbsttest des Staging-Bereichs: python .githooks/check_encoding.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
