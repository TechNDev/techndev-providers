#!/usr/bin/env python3
"""
techndev-providers  cubegolem/setcookie.py  v1.0.0
====================================================
Schreibt die cubegolem-Session-Cookie aus der Zwischenablage in
cubegolem_config.json und prueft live, ob die Session gueltig ist.

Warum nicht automatisch aus Chrome auslesen?
  Chrome >= 127 (hier 148) nutzt App-Bound Encryption — Cookies sind nicht
  mehr von aussen entschluesselbar (Schluessel an Chrome gebunden). Ein
  externes Auslesen ginge nur mit Bypass-Techniken (Code-Injektion), die
  wie Schadsoftware wirken. Daher: einmal manuell kopieren, Rest automatisch.

So kommst du an den Cookie (HttpOnly-Session-Cookie ist NUR so erreichbar):
  DevTools (F12) → Tab "Network" → Seite auf cubegolem.de neu laden →
  beliebigen Request auf cubegolem.de anklicken → Rechtsklick →
  "Copy" → "Copy as cURL (bash)"  (enthaelt den vollstaendigen Cookie-Header).
  Alternativ den "cookie:"-Request-Header aus dem Headers-Tab kopieren.

Eingabe wird aus stdin gelesen (die get_cookie.bat pipet Get-Clipboard hinein).
Akzeptiert: cURL-Kommando, "Cookie: ..."-Header-Block oder roher
"name=value; name2=value2"-String.

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. Parser fuer cURL/Header/roh + Config-Write + Live-Check.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ._auth   import is_logged_out, normalize_cookie
from .scraper import CubeGolemProvider, RE_EK

CONFIG_PATH = Path(__file__).resolve().parent.parent / "cubegolem_config.json"
# Stabiler, lange verfuegbarer Artikel fuer den Login-Check.
VALIDATION_SLUG = "mtg-aetherdrift-play-booster-display-30-boosters-de"


def extract_cookie(raw: str) -> str:
    """
    Holt den Cookie-Header aus beliebiger DevTools-Kopie:
    cURL (-b/--cookie oder -H 'cookie: …'), Header-Block ('Cookie: …')
    oder rohem 'name=value; …'-String.
    """
    if not raw:
        return ""
    text = raw.strip()

    # cURL:  -b 'COOKIE'  /  --cookie "COOKIE"
    m = re.search(r"(?:-b|--cookie)\s+(['\"])(.+?)\1", text, re.S)
    if m:
        return m.group(2).strip()

    # cURL / Header:  -H 'cookie: COOKIE'  oder Zeile 'Cookie: COOKIE'
    m = re.search(r"(?:-H\s+['\"])?\s*cookie:\s*([^'\"\r\n]+)", text, re.I)
    if m:
        return m.group(1).strip()

    # roh: muss mindestens ein name=value enthalten
    if re.search(r"[^=;\s]+=[^;]+", text) and "\n" not in text.strip():
        return text

    # letzter Versuch: erste Zeile, die wie ein Cookie-String aussieht
    for line in text.splitlines():
        if re.fullmatch(r"\s*[^=;\s]+=[^;]+(;\s*[^=;\s]+=[^;]*)*\s*", line):
            return line.strip()
    return ""


def validate(cookie: str) -> tuple[bool, str]:
    """Live-Check: Session gueltig? → (ok, Meldung)."""
    try:
        prov = CubeGolemProvider(session_cookie=cookie)
        html = prov._get(f"/product/{VALIDATION_SLUG}")
    except Exception as e:                       # Netzwerk o.ae.
        return False, f"Konnte nicht pruefen (Netzwerk?): {e}"
    if is_logged_out(html):
        return False, "Session NICHT gueltig — Shop zeigt den Login-Hinweis."
    if RE_EK.search(html):
        return True, "Session gueltig — Preise sichtbar."
    return False, "Eingeloggt? Kein Preis gefunden — Cookie evtl. unvollstaendig."


def main(argv=None) -> int:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    cookie = normalize_cookie(extract_cookie(raw))
    if not cookie:
        print("FEHLER: Keine Cookie-Daten in der Zwischenablage erkannt.")
        print("Tipp: DevTools → Network → Request → Copy → 'Copy as cURL (bash)',")
        print("      dann diese Batch erneut starten.")
        return 1

    n = cookie.count("=")
    print(f"Cookie erkannt ({n} Wert(e), {len(cookie)} Zeichen). Pruefe Session …")
    ok, msg = validate(cookie)
    print(("  OK  " if ok else "  !!  ") + msg)

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["session_cookie"] = cookie
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"Gespeichert in: {CONFIG_PATH}")
    if not ok:
        print("Hinweis: Cookie wurde gespeichert, aber die Pruefung schlug fehl.")
        return 2
    print("Fertig — du kannst jetzt z.B. 'python -m cubegolem.cli "
          "magic-the-gathering' ausfuehren.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
