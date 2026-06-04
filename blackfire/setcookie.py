#!/usr/bin/env python3
"""
techndev-providers  blackfire/setcookie.py  v1.0.0
====================================================
Schreibt die Blackfire-B2B-Session-Cookie aus der Zwischenablage in
blackfire_config.json und prueft live, ob die Session gueltig ist
(Preise sind bei Blackfire NUR eingeloggt sichtbar).

Warum nicht automatisch aus Chrome auslesen?
  Chrome >= 127 nutzt App-Bound Encryption — der HttpOnly-Session-Cookie ist
  von aussen nicht entschluesselbar. Daher: einmal manuell kopieren.

So kommst du an den Cookie:
  DevTools (F12) -> Tab "Network" -> auf blackfire.eu (eingeloggt) eine Seite
  neu laden -> beliebigen Request auf www.blackfire.eu anklicken ->
  Rechtsklick -> "Copy" -> "Copy as cURL (bash)"  (enthaelt den Cookie-Header).
  Alternativ den "cookie:"-Request-Header aus dem Headers-Tab kopieren.

Aufruf:
  blackfire\\get_cookie.bat            (pipet Get-Clipboard hinein)
  ODER:  python -m blackfire.setcookie   (liest stdin)
  ODER:  python blackfire/setcookie.py "<cookie-string>"

CHANGELOG
---------
v1.0.0 (2026-06-03)
  - Initiales Release. Parser (cURL/Header/roh) + Config-Write + Live-Check.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

CONFIG_PATH = Path(__file__).resolve().parent.parent / "blackfire_config.json"
# Stabile Kategorie fuer den Login-Check: eingeloggt listet sie Produkte
# (Detail-Links + Preise); ausgeloggt ist sie leer.
VALIDATION_URL = "https://www.blackfire.eu/de-de/trading-card-games/close-out/"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_PRICE_RE = re.compile(r"\d{1,4}[.,]\d{2}\s*(?:€|EUR)")
_LINK_RE = re.compile(r"/de-de/[a-z0-9-]+-\d{4,7}")   # Produkt-Detail-Links


def extract_cookie(raw: str) -> str:
    """Holt den Cookie-Header aus DevTools-Kopie (cURL / Header / roh)."""
    if not raw:
        return ""
    text = raw.strip()
    m = re.search(r"(?:-b|--cookie)\s+(['\"])(.+?)\1", text, re.S)      # cURL -b 'COOKIE'
    if m:
        return m.group(2).strip()
    m = re.search(r"(?:-H\s+['\"])?\s*cookie:\s*([^'\"\r\n]+)", text, re.I)  # -H 'cookie: ..'/Header
    if m:
        return m.group(1).strip()
    if re.search(r"[^=;\s]+=[^;]+", text) and "\n" not in text.strip():  # roh: name=value; ..
        return text
    for line in text.splitlines():
        if re.fullmatch(r"\s*[^=;\s]+=[^;]+(;\s*[^=;\s]+=[^;]*)*\s*", line):
            return line.strip()
    return ""


def _get(url: str, cookie: str) -> str:
    headers = {
        "User-Agent": _UA, "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "de-DE,de;q=0.9", "Accept-Encoding": "gzip, identity",
        "Cookie": cookie,
    }
    with urlopen(Request(url, headers=headers), timeout=25) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode(r.headers.get_content_charset() or "utf-8", "replace")


def validate(cookie: str) -> tuple[bool, str]:
    """Live-Check: zeigt die Seite mit diesem Cookie Preise (= eingeloggt)?"""
    try:
        html = _get(VALIDATION_URL, cookie)
    except Exception as e:                                   # noqa: BLE001
        return False, f"Konnte nicht pruefen (Netzwerk?): {e}"
    # Eingeloggt listet die Kategorie Produkte (Detail-Links, oft auch Preise);
    # ausgeloggt ist sie leer. Konto-/Login-Links taugen NICHT (in beiden Zustaenden).
    links = len(set(_LINK_RE.findall(html)))
    prices = len(_PRICE_RE.findall(html))
    if links >= 3:
        extra = f", {prices} Preise" if prices else " (Preise erst auf Detailseiten)"
        return True, f"Session gueltig — {links} Produkte gelistet{extra}."
    return False, ("Keine Produkte gelistet — vermutlich NICHT eingeloggt. Cookie ist "
                   "gespeichert; bitte erneut eingeloggt 'Copy as cURL' kopieren.")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    raw = argv[0] if argv else (sys.stdin.read() if not sys.stdin.isatty() else "")
    cookie = extract_cookie(raw)
    if not cookie:
        print("FEHLER: Keine Cookie-Daten erkannt.")
        print("Tipp: DevTools -> Network -> Request -> Copy -> 'Copy as cURL (bash)',")
        print("      dann blackfire\\get_cookie.bat erneut starten.")
        return 1

    print(f"Cookie erkannt ({cookie.count('=')} Wert(e), {len(cookie)} Zeichen). Pruefe Session ...")
    ok, msg = validate(cookie)
    print(("  OK   " if ok else "  !!   ") + msg)

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["session_cookie"] = cookie
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Gespeichert in: {CONFIG_PATH}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
