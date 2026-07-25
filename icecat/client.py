#!/usr/bin/env python3
"""
icecat  client.py  v1.0.0
===========================
Icecat REST API Client — Produktdaten per EAN, Brand+MPN oder Icecat-ID.
Extrahiert aus EAN2JTL (ean2jtl.py v3.15.0).

API:  https://live.icecat.biz/api
Auth: api-token + content-token als HTTP-Header
      app_key (optional) als Query-Param — noetig fuer FULL-Icecat-Inhalte
      (Elektronik u.a.). Ohne app_key liefert die API nur Open-Icecat-Produkte;
      Full-Content-Produkte antworten sonst mit HTTP 403.

Rueckgabe-Format (parse_product) ist identisch zu EAN2JTL:
  ean, icecat_id, name, brand, mpn, category,
  short_desc, long_desc, main_image, all_images, features

CHANGELOG
---------
v1.1.0  (2026-07-25)
  - app_key (optional): Query-Param fuer Full-Icecat-Zugriff. Rueckwaertskompatibel
    (ohne app_key unveraendertes Verhalten = nur Open-Icecat).
v1.0.0  (2026-05-25)
  - Initiales Release, extrahiert aus EAN2JTL
  - Bilddeduplication: seen_urls-Set statt _img_base_id()
    (_img_base_id() war Amazon-spezifisch; Icecat-URLs sind eindeutig)
  - fetch_by_ean(), fetch_by_brand_mpn(), fetch_by_icecat_id(): wie bisher
  - verify_token(): Token-Test mit Dummy-EAN
  - parse_product(): Icecat-API-Raw-Dict -> EAN2JTL-kompatibles Dict
"""
import re

import requests

__version__ = "1.1.0"


class IcecatClient:
    """
    Icecat REST API Client.

    Instanzierung:
      client = IcecatClient(shopname, api_token, content_token, language='DE')

    Typischer Ablauf:
      raw     = client.fetch_by_ean('4010232075488')
      product = client.parse_product(raw, ean='4010232075488')
      if product:
          print(product['name'], product['brand'])
    """
    BASE_URL = "https://live.icecat.biz/api"
    TIMEOUT  = 30

    def __init__(
        self,
        shopname:      str,
        api_token:     str,
        content_token: str,
        language:      str = "DE",
        app_key:       str | None = None,
    ):
        self.shopname = shopname
        self.language = language
        self.app_key  = app_key or None      # noetig fuer Full-Icecat-Inhalte
        self.session  = requests.Session()
        self.session.headers.update({
            "User-Agent":    "TechNDevIcecatClient/1.0",
            "Accept":        "application/json",
            "api-token":     api_token,
            "content-token": content_token,
        })

    def _params(self, **extra) -> dict:
        """Basis-Query-Params inkl. app_key (falls gesetzt)."""
        p = {"shopname": self.shopname, "lang": self.language, "content": ""}
        if self.app_key:
            p["app_key"] = self.app_key
        p.update(extra)
        return p

    # ── Verbindungstest ────────────────────────────────────────────────────────

    def verify_token(self) -> tuple[bool, str]:
        """
        Token-Verifikation mit Dummy-EAN.
        Gibt (ok: bool, message: str) zurueck — wirft keine Exception.
        """
        TEST_EAN = "0711719709695"
        try:
            resp = self.session.get(
                self.BASE_URL, params=self._params(GTIN=TEST_EAN), timeout=self.TIMEOUT)
            if resp.status_code == 200:
                name = ((resp.json().get("data") or {})
                        .get("GeneralInfo", {}).get("Title", "–"))
                return True, f"Zugang OK  ·  Testprodukt: {name}"
            elif resp.status_code == 401:
                return False, "HTTP 401 – Zugangsdaten ungueltig"
            elif resp.status_code in (400, 403, 404):
                # 400 GTIN-nicht-gefunden / 403 Full-Content ohne app_key / 404 Marken-
                # Restriktion → API grundsaetzlich erreichbar, Auth ok.
                return True, "Zugang OK  ·  API erreichbar"
            else:
                return False, f"HTTP {resp.status_code}"
        except requests.ConnectionError:
            return False, "Verbindungsfehler – live.icecat.biz nicht erreichbar"
        except Exception as exc:
            return False, f"Fehler: {exc}"

    # ── Abruf-Methoden (werfen requests.HTTPError bei Fehler) ─────────────────

    def fetch_by_ean(self, ean: str) -> dict:
        """EAN-Suche. Raises requests.HTTPError."""
        resp = self.session.get(
            self.BASE_URL, params=self._params(GTIN=ean.strip()), timeout=self.TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def fetch_by_brand_mpn(self, brand: str, mpn: str) -> dict:
        """Lookup via Hersteller + Artikelnummer. Raises HTTPError."""
        resp = self.session.get(
            self.BASE_URL,
            params=self._params(Brand=brand.strip(), ProductCode=mpn.strip()),
            timeout=self.TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def fetch_by_icecat_id(self, icecat_id: str) -> dict:
        """Lookup via interne Icecat-ID. Raises HTTPError."""
        resp = self.session.get(
            self.BASE_URL, params=self._params(icecat_id=icecat_id.strip()),
            timeout=self.TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # ── Parser ─────────────────────────────────────────────────────────────────

    def parse_product(self, raw: dict, ean: str) -> dict | None:
        """
        Icecat-API-Antwort -> EAN2JTL-kompatibles Produkt-Dict.
        Gibt None zurueck wenn 'data' fehlt oder leer ist.

        Felder: ean, icecat_id, name, brand, mpn, category,
                short_desc, long_desc, main_image, all_images, features
        """
        data = raw.get("data") or {}
        if not data:
            return None

        general = data.get("GeneralInfo") or {}
        desc    = general.get("Description") or {}
        summary = general.get("SummaryDescription") or {}

        # ── Merkmale aus FeaturesGroups ────────────────────────────────────────
        features: list[dict] = []
        for group in data.get("FeaturesGroups") or []:
            for feat in group.get("Features") or []:
                fi       = feat.get("Feature") or {}
                # Feature-Name: Icecat liefert String oder {"Value": "…", "Language": "DE"}
                name_raw = fi.get("Name", "")
                if isinstance(name_raw, dict):
                    name = name_raw.get("Value") or name_raw.get("value", "")
                else:
                    name = str(name_raw)
                name = name.strip()

                val       = feat.get("Value", "")
                meas      = fi.get("Measure") or {}
                unit      = meas.get("Sign", "") if isinstance(meas, dict) else ""
                # Zeilenumbrueche aus Merkmal-Werten entfernen (JTL-Ameise-Kompatibilitaet)
                val_clean = re.sub(r"[\r\n]+", " / ", str(val)).strip()
                if name and val_clean:
                    features.append({"name": name, "value": f"{val_clean} {unit}".strip()})

        # ── Bilder: Hauptbild + Gallery (URL-Deduplizierung) ──────────────────
        # Icecat-URLs sind eindeutige absolute Pfade → Set-Deduplizierung genuegt.
        all_images: list[str] = []
        seen_urls:  set[str]  = set()

        img = data.get("Image") or {}
        if isinstance(img, dict):
            url = img.get("HighPic") or img.get("LowPic") or ""
            if url and url not in seen_urls:
                all_images.append(url)
                seen_urls.add(url)

        for ph in data.get("Gallery") or []:
            if isinstance(ph, dict):
                url = ph.get("HighPic") or ph.get("LowPic") or ""
                if url and url not in seen_urls:
                    all_images.append(url)
                    seen_urls.add(url)

        # ── Kategorie-Name ────────────────────────────────────────────────────
        # Icecat liefert entweder String oder {"Value": "…", "Language": "DE"}
        cat = general.get("Category") or {}
        if isinstance(cat, dict):
            cat_name_raw = cat.get("Name", "")
            if isinstance(cat_name_raw, dict):
                cat_name = cat_name_raw.get("Value") or cat_name_raw.get("value", "")
            else:
                cat_name = str(cat_name_raw)
        else:
            cat_name = str(cat)

        gtins = general.get("GTIN") or general.get("GTINs") or []
        brand = (general.get("Brand") or
                 (general.get("BrandInfo") or {}).get("BrandName") or
                 general.get("BrandName") or "")

        return {
            "ean":        gtins[0] if gtins else ean,
            "icecat_id":  str(general.get("IcecatId", "")),
            "name":       general.get("Title", ""),
            "brand":      brand,
            "mpn":        general.get("BrandPartCode", ""),
            "category":   cat_name.strip(),
            "short_desc": (summary.get("ShortSummaryDescription") or
                           desc.get("ShortDesc") or ""),
            "long_desc":  (desc.get("LongDesc") or
                           summary.get("LongSummaryDescription") or ""),
            "main_image": all_images[0] if all_images else "",
            "all_images": all_images,
            "features":   features,
        }
