"""
ebay.eps — Bilder auf eBays eigenen Bildserver laden (eBay Picture Services).

WARUM: Die Sell-Inventory-API nimmt keine Bilddateien entgegen, sie will URLs und
holt die Bilder selbst ab. Lokale Pfade scheitern dort mit
    HTTP 400 · Invalid value for imageUrl. Incorrect URL format.
Wer keinen eigenen Webserver betreiben will, laedt die Bilder deshalb zu eBay:
UploadSiteHostedPictures nimmt die Datei binaer an und gibt eine i.ebayimg.com-URL
zurueck, die direkt in product.imageUrls passt.

Das ist die klassische TRADING-API (XML ueber /ws/api.dll), nicht die REST-Welt der
uebrigen Module. Authentifiziert wird trotzdem mit unserem OAuth-User-Token, das
die Trading-API als X-EBAY-API-IAF-TOKEN akzeptiert — ein Auth'n'Auth-Token ist
nicht noetig.

WICHTIG — Haltbarkeit: Ein hochgeladenes Bild, das an KEINEM Angebot haengt, wird
von eBay nach einiger Zeit wieder geloescht (eBay nennt hierfuer bis zu 30 Tage;
`extension_days` verlaengert das). Sobald das Bild in einem Angebot verwendet wird,
bleibt es. Fuer den Ablauf heisst das: hochladen und zeitnah listen, nicht auf
Vorrat.

    from ebay import upload_picture
    url, err = upload_picture(r"C:\\...\\x_fk86679.jpg", creds)

CHANGELOG:
- 1.1.1  Antwort explizit als UTF-8 lesen — die Trading-API deklariert den
         Zeichensatz nicht, Umlaute in Fehlertexten kamen verstuemmelt an.
- 1.1.0  Es wird die GROESSTE Variante zurueckgegeben statt FullURL — die zeigt bei
         eBay auf 500x500, waehrend das Original daneben liegt. Neu
         upload_picture_detail() mit Groesse, allen Varianten und der Ack-Warnung.
- 1.0.0  Erstausgabe: upload_picture() (multipart XML + Binaerteil), Fehlertexte
         aus der XML-Antwort, PictureSet/Extension konfigurierbar.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import requests

from ._auth import SCOPE_INVENTORY, api_base, get_user_token
from ._rate import inventory_limiter

__version__ = "1.1.1"

TIMEOUT = 60
COMPAT_LEVEL = "1193"

# Trading-API-Site-IDs (nur die Maerkte, die das Framework fuehrt).
SITE_IDS = {"EBAY_DE": "77", "EBAY_AT": "16", "EBAY_CH": "193", "EBAY_GB": "3",
            "EBAY_FR": "71", "EBAY_IT": "101", "EBAY_ES": "186", "EBAY_NL": "146",
            "EBAY_BE": "123", "EBAY_US": "0"}

_XML = """<?xml version="1.0" encoding="utf-8"?>
<UploadSiteHostedPicturesRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <PictureName>{name}</PictureName>
  <PictureSet>{picture_set}</PictureSet>
  {extension}
</UploadSiteHostedPicturesRequest>
"""


def _tag(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
    return m.group(1).strip() if m else ""


def _fehler(xml: str) -> str:
    """Kurzer, lesbarer Fehlertext aus einer Trading-API-Antwort."""
    kurz = _tag(xml, "ShortMessage")
    lang = _tag(xml, "LongMessage")
    code = _tag(xml, "ErrorCode")
    teile = [t for t in (code and f"Code {code}", kurz, lang) if t]
    return " · ".join(teile) or "unbekannter Fehler"


def upload_picture_detail(
    bild: str | Path | bytes, creds: dict, *,
    name: str = "", marketplace: str = "EBAY_DE",
    picture_set: str = "Supersize", extension_days: int | None = None,
) -> tuple[dict | None, str | None]:
    """
    Ein Bild zu eBay hochladen. Rueckgabe (details, error) — genau eines ist gesetzt.

    details = {url, width, height, full_url, members: [(w, h, url)], warnung}

    `url` ist die GROESSTE Variante, nicht FullURL: eBay liefert dort die
    500-px-Fassung, waehrend daneben die volle Aufloesung liegt (verifiziert an
    einem 800x800-Bild — FullURL zeigte auf $_12.JPG mit 500x500, das Original
    stand als $_3.JPG bereit). Unter 800 px Kantenlaenge schaltet eBay die
    Zoom-Ansicht ab, die kleinere Fassung waere also ein echter Verlust.

    bild:            Pfad oder Bytes (JPEG/PNG/GIF/BMP/TIFF, max. 12 MB bei eBay).
    name:            PictureName; ohne Angabe der Dateiname.
    picture_set:     'Supersize' (bis 1600 px) | 'Standard'.
    extension_days:  Aufbewahrung ohne Angebot verlaengern (eBay-Grenzen beachten).
    """
    if isinstance(bild, (str, Path)):
        p = Path(bild)
        if not p.is_file():
            return None, f"Datei nicht gefunden: {p}"
        daten = p.read_bytes()
        name = name or p.name
    else:
        daten = bytes(bild)
        name = name or f"bild-{uuid.uuid4().hex[:8]}.jpg"
    if not daten:
        return None, "Leere Bilddatei"

    try:
        token = get_user_token(creds["client_id"], creds["client_secret"],
                               creds["refresh_token"], scope=SCOPE_INVENTORY,
                               env=creds.get("env", "production"))
    except Exception as exc:                                 # noqa: BLE001
        return None, f"Token: {exc}"

    ext = (f"<ExtensionInDays>{int(extension_days)}</ExtensionInDays>"
           if extension_days else "")
    xml = _XML.format(name=name, picture_set=picture_set, extension=ext)

    base = api_base(creds.get("env", "production")).replace("https://api.", "https://api.")
    url = f"{base}/ws/api.dll"
    headers = {
        "X-EBAY-API-COMPATIBILITY-LEVEL": COMPAT_LEVEL,
        "X-EBAY-API-CALL-NAME":           "UploadSiteHostedPictures",
        "X-EBAY-API-SITEID":              SITE_IDS.get(marketplace, "77"),
        "X-EBAY-API-IAF-TOKEN":           token,
    }
    # Reihenfolge ist Pflicht: erst der XML-Teil, dann die Binaerdatei.
    files = [
        ("XML Payload", (None, xml, "text/xml")),
        ("dummy", (name, daten, "application/octet-stream")),
    ]

    inventory_limiter.wait()
    try:
        resp = requests.post(url, headers=headers, files=files, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        text = getattr(e.response, "text", "") or str(e)
        return None, f"HTTP {getattr(e.response, 'status_code', '?')} · {text[:200]}"
    except requests.RequestException as e:
        return None, f"Netzwerkfehler: {e}"

    # Die Trading-API liefert UTF-8, deklariert es im Header aber nicht — ohne das
    # hier werden Umlaute in Fehler-/Warntexten zu Kraut (aus "ö" werden
    # zwei Zeichen: das UTF-8-Byte 0xC3 + 0xB6, als Latin-1 gelesen).
    resp.encoding = "utf-8"
    body = resp.text
    if _tag(body, "Ack") in ("Failure", "PartialFailure"):
        return None, _fehler(body)

    members: list[tuple[int, int, str]] = []
    for m in re.finditer(r"<PictureSetMember>(.*?)</PictureSetMember>", body, re.S):
        blk = m.group(1)
        w, h, u = _tag(blk, "PictureWidth"), _tag(blk, "PictureHeight"), _tag(blk, "MemberURL")
        if u and w.isdigit() and h.isdigit():
            members.append((int(w), int(h), u))
    members.sort(key=lambda t: t[0] * t[1], reverse=True)

    voll = _tag(body, "FullURL")
    if members:
        w, h, url_gross = members[0]
    elif voll:
        w = h = 0
        url_gross = voll
    else:
        return None, _fehler(body) or "Antwort ohne Bild-URL"

    return {"url": url_gross, "width": w, "height": h, "full_url": voll,
            "members": members,
            "warnung": _fehler(body) if _tag(body, "Ack") == "Warning" else ""}, None


def upload_picture(
    bild: str | Path | bytes, creds: dict, *,
    name: str = "", marketplace: str = "EBAY_DE",
    picture_set: str = "Supersize", extension_days: int | None = None,
) -> tuple[str | None, str | None]:
    """Wie upload_picture_detail, gibt aber nur die URL der groessten Variante."""
    d, err = upload_picture_detail(bild, creds, name=name, marketplace=marketplace,
                                   picture_set=picture_set, extension_days=extension_days)
    return (d["url"] if d else None), err
