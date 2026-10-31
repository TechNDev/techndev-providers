#!/usr/bin/env python3
"""
cardmarket  client.py  v1.4.0
===============================
MKM-API-Client: OAuth1.0a (HMAC-SHA1, Dedicated-App), urllib, kein SDK.

CHANGELOG:
  1.4.0  API-Tageslimit: jede Antwort liefert X-Request-Limit-Count/-Max; der zuletzt
         gesehene Stand liegt in LIMIT (limit_stand()), auch bei HTTP 429.
  1.3.0  Orders schreiben: send_order(id) bestaetigt den Versand (PUT /order/{id},
         action=send, paid -> sent), set_tracking(id, nr) traegt die
         Sendungsnummer nach (PUT /order/{id}/tracking, nur im Status sent).
  1.2.0  Bestand schreiben: add_stock(articles) per POST /stock und
         change_stock(articles) per PUT /stock (XML-Body, 100er-Pakete, Ergebnis
         je Artikel); get_stock_article(idArticle) liest einen eigenen Artikel.
         get_cheapest_offer: neue optionale Filter is_foil, exclude_seller,
         plain_only, Rueckgabe zusaetzlich id_article; sortiert selbst nach Preis.
         max_results > 100 blaettert seitenweise (MKM erlaubt max. 100 je Abruf). Dazu lesend get_expansions(idGame),
         get_singles(idExpansion) (24-h-Cache) und get_product(id) fuer die
         Zuordnung Name/Edition/Sammlernummer -> idProduct (cardmarket-orders
         cm_stock.py). _send() fuer alle HTTP-Methoden, _get() nutzt es.
  1.1.0  Orders (Verkaufsseite): get_order(id) mit Produktblock je Artikel,
         get_orders(actor, state, start) als eine Seite (max 100, HTTP 206 =
         weitere Seiten) und iter_orders(...) ueber alle Seiten. Fuer den
         Auftragsimport nach JTL (cardmarket-orders). Nur lesend.
  1.0.1  API-Host auf apiv2.cardmarket.com umgestellt — der alte Host
         api.cardmarket.com liefert seit der Cardmarket-API-Migration
         nur noch HTTP 410 (verifiziert 2026-09-16, OAuth1-Creds
         funktionieren am neuen Host unveraendert)
  1.0.0  Initial: /account, /productlist, /priceguide, /products, /articles

Endpoints:
  /account                       Auth-Check
  /productlist                   Bulk: alle Produkte (gzip+base64 CSV)
  /priceguide                    Bulk: Preis-Guide aller aktiven Produkte (CSV)
  /products/{id}                 Einzel-Detail (priceGuide)
  /articles/{id}                 LIVE: aktuelle Angebote (preis-sortiert)
  /order/{id}                    Einzel-Order inkl. Produktblock je Artikel
  /orders/{actor}/{state}/{start} Order-Liste je Status, 100 je Seite (206)
  PUT /order/{id}                Order-Status setzen, hier action=send (SCHREIBT)
  PUT /order/{id}/tracking       Sendungsnummer setzen (SCHREIBT)
  /games/{idGame}/expansions     Editionen eines Spiels (gecacht)
  /expansions/{id}/singles       Einzelkarten einer Edition inkl. number (gecacht)
  /stock/article/{idArticle}     ein eigener Bestandsartikel
  POST /stock                    Artikel in den eigenen Bestand einstellen (SCHREIBT)
  PUT /stock                     eigene Artikel aendern, je idArticle (SCHREIBT)

Die beiden Bulk-Dateien werden nur 1x/Tag serverseitig aktualisiert → fuer das
breite Screening gecacht (Default 24 h). Die Kaufentscheidung nutzt /articles
(Echtzeit).
"""
from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import hmac
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

__version__ = "1.4.0"

_BASE = "https://apiv2.cardmarket.com/ws/v2.0/output.json"
_REQUIRED = ("app_token", "app_secret", "access_token", "access_secret")


# MKM-Order-Status (Pfadsegment von /orders/{actor}/{state})
ORDER_STATES = ("bought", "paid", "sent", "received", "lost", "cancelled")
ORDER_ACTORS = ("seller", "buyer")
ORDERS_PAGE = 100
ARTICLES_PAGE = 100                                        # MKM: max. 100 Angebote je /articles-Abruf

# Bestand (POST /stock)
STOCK_CHUNK = 100                                          # MKM: max. 100 Artikel je Request
CONDITIONS = ("MT", "NM", "EX", "GD", "LP", "PL", "PO")
STOCK_FIELDS = ("idArticle", "idProduct", "idLanguage", "comments", "count", "price",
                "condition", "isFoil", "isSigned", "isPlayset")


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: str | Path | None = None) -> dict:
    """
    Laedt MKM-Credentials. Reihenfolge:
      1. expliziter path
      2. Env CARDMARKET_CONFIG
      3. ./cardmarket_config.json (CWD — i.d.R. product-catalog)
      4. Aufwaerts-Suche ab dieser Datei (bis 5 Ebenen)
      5. Sibling product-catalog/cardmarket_config.json
    Akzeptiert {"cardmarket": {...}} oder direkte Felder.
    """
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    env = os.environ.get("CARDMARKET_CONFIG", "").strip()
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "cardmarket_config.json")
    here = Path(__file__).resolve()
    for up in here.parents[:5]:
        candidates.append(up / "cardmarket_config.json")
        candidates.append(up / "product-catalog" / "cardmarket_config.json")
    for c in candidates:
        try:
            if c.is_file():
                data = json.loads(c.read_text(encoding="utf-8"))
                sec = data.get("cardmarket", data)
                if all(sec.get(k) for k in _REQUIRED):
                    return {k: sec[k] for k in _REQUIRED}
        except Exception:                                  # noqa: BLE001
            continue
    raise RuntimeError(
        "cardmarket_config.json nicht gefunden/unvollstaendig "
        f"(brauche {_REQUIRED}). Gesucht u.a. in CWD + product-catalog/.")


def _enc(s) -> str:
    return urllib.parse.quote(str(s), safe="~")


# ── Client ────────────────────────────────────────────────────────────────────

# ── API-Tageslimit ──────────────────────────────────────────────────────────
# Cardmarket zählt alle Requests der App je Kalendertag (Reset Mitternacht MEZ) und meldet den
# Stand in jeder Antwort. Gilt für das ganze Konto, also auch für Requests anderer Programme.
LIMIT: dict = {"count": None, "max": None, "zeit": None, "erschoepft": False}


def _limit_merken(headers, erschoepft: bool = False) -> None:
    try:
        n, m = headers.get("X-Request-Limit-Count"), headers.get("X-Request-Limit-Max")
    except Exception:                                       # noqa: BLE001
        return
    if n is None and not erschoepft:
        return
    LIMIT.update(count=int(n) if n is not None else LIMIT["count"],
                 max=int(m) if m is not None else LIMIT["max"],
                 zeit=time.time(), erschoepft=erschoepft)


def limit_stand() -> dict:
    """Zuletzt gesehener Stand des Tageslimits: {count, max, zeit (epoch), erschoepft}."""
    return dict(LIMIT)


class CardmarketClient:
    def __init__(self, creds: dict, *, cache_dir: str | Path | None = None,
                 cache_ttl_s: int = 24 * 3600):
        self.creds = creds
        self.cache_dir = Path(cache_dir or (Path.home() / ".cache" / "techndev" / "cardmarket"))
        self.cache_ttl_s = cache_ttl_s

    # ── OAuth1 ──────────────────────────────────────────────────────────────
    def _auth_header(self, method: str, url: str) -> str:
        oauth = {
            "oauth_consumer_key":     self.creds["app_token"],
            "oauth_token":            self.creds["access_token"],
            "oauth_nonce":            os.urandom(8).hex(),
            "oauth_timestamp":        str(int(time.time())),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_version":          "1.0",
        }
        p = urllib.parse.urlsplit(url)
        base_url = f"{p.scheme}://{p.netloc}{p.path}"
        allp = dict(urllib.parse.parse_qsl(p.query))
        allp.update(oauth)
        pstr = "&".join(f"{_enc(k)}={_enc(allp[k])}" for k in sorted(allp))
        base = f"{method}&{_enc(base_url)}&{_enc(pstr)}"
        key = f"{_enc(self.creds['app_secret'])}&{_enc(self.creds['access_secret'])}"
        oauth["oauth_signature"] = base64.b64encode(
            hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
        parts = [f'realm="{base_url}"'] + [f'{k}="{_enc(v)}"' for k, v in oauth.items()]
        return "OAuth " + ", ".join(parts)

    def _send(self, method: str, path: str, body: str | None = None, timeout: int = 180):
        url = _BASE + path
        headers = {"Authorization": self._auth_header(method, url)}
        data = None
        if body is not None:                               # XML-Body geht NICHT in die Signatur
            data = body.encode("utf-8")
            headers["Content-Type"] = "application/xml; charset=utf-8"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _limit_merken(r.headers)
                raw = r.read()                             # 204 (nichts da) = leerer Body
                return r.status, (json.loads(raw.decode()) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            # 206 (Partial Content, paginierte Artikel) ist OK
            _limit_merken(e.headers, erschoepft=e.code == 429)
            text = e.read().decode("utf-8", "replace")
            if e.code == 206:
                try:
                    return 206, json.loads(text)
                except Exception:                          # noqa: BLE001
                    pass
            raise RuntimeError(f"MKM {method} {path} -> HTTP {e.code}: {text[:160]}") from e

    def _get(self, path: str, timeout: int = 180):
        return self._send("GET", path, timeout=timeout)

    # ── Auth-Check ──────────────────────────────────────────────────────────
    def account(self) -> dict:
        return (self._get("/account")[1] or {}).get("account", {})

    # ── Bulk-Dateien (gecacht) ──────────────────────────────────────────────
    def _cached_csv(self, path: str, file_key: str, force: bool,
                    cache_key: str | None = None) -> str:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache = self.cache_dir / f"{cache_key or file_key}.csv"
        if not force and cache.is_file() and (time.time() - cache.stat().st_mtime) < self.cache_ttl_s:
            return cache.read_text(encoding="utf-8")
        payload = self._get(path)[1] or {}
        raw = payload.get(file_key)
        if not raw:
            raise RuntimeError(f"MKM {path}: kein '{file_key}' in der Antwort")
        text = gzip.decompress(base64.b64decode(raw)).decode("utf-8", "replace")
        cache.write_text(text, encoding="utf-8")
        return text

    def fetch_product_list(self, force: bool = False) -> list[dict]:
        """Alle Produkte (alle Spiele): [{idProduct, Name, Category ID, Category,
        Expansion ID, Metacard ID, Date Added}]."""
        return list(csv.DictReader(io.StringIO(self._cached_csv(
            "/productlist", "productsfile", force))))

    def fetch_price_guide(self, id_game: int | None = None, force: bool = False) -> dict[str, dict]:
        """Preis-Guide je idProduct: {low, low_ex, trend, sell, de_pro_low, uvp,
        avg7, avg30}. Der Bulk ist SPIELSPEZIFISCH — ohne id_game = Magic (Default).
        Fuer andere Spiele id_game setzen (Pokémon=6, One Piece=18, Lorcana=19)."""
        path = "/priceguide" + (f"?idGame={id_game}" if id_game else "")
        ck = "priceguidefile" if not id_game else f"priceguide_{id_game}"
        out: dict[str, dict] = {}
        for r in csv.DictReader(io.StringIO(self._cached_csv(
                path, "priceguidefile", force, cache_key=ck))):
            pid = r.get("idProduct")
            if not pid:
                continue
            out[pid] = {
                "low":        _f(r.get("Low Price")),
                "low_ex":     _f(r.get("Low Price Ex+")),
                "trend":      _f(r.get("Trend Price")),
                "sell":       _f(r.get("Avg. Sell Price")),
                "de_pro_low": _f(r.get("German Pro Low")),
                "uvp":        _f(r.get("Suggested Price")),
                "avg7":       _f(r.get("AVG7")),
                "avg30":      _f(r.get("AVG30")),
            }
        return out

    def _cached_json(self, path: str, cache_key: str, force: bool) -> dict:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache = self.cache_dir / f"{cache_key}.json"
        if not force and cache.is_file() and (time.time() - cache.stat().st_mtime) < self.cache_ttl_s:
            return json.loads(cache.read_text(encoding="utf-8"))
        data = self._get(path)[1] or {}
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def get_expansions(self, id_game: int = 1, force: bool = False) -> list[dict]:
        """Editionen eines Spiels: [{idExpansion, enName, abbreviation, ...}]."""
        return self._cached_json(f"/games/{int(id_game)}/expansions",
                                 f"expansions_{int(id_game)}", force).get("expansion") or []

    def get_singles(self, id_expansion: int, force: bool = False) -> list[dict]:
        """Einzelkarten einer Edition: [{idProduct, enName, localization, number,
        rarity, expansionName, website, ...}]. Varianten heissen „Name (V.2)“."""
        return self._cached_json(f"/expansions/{int(id_expansion)}/singles",
                                 f"singles_{int(id_expansion)}", force).get("single") or []

    # ── Einzel-Detail / LIVE ────────────────────────────────────────────────
    def get_product(self, id_product) -> dict:
        """Produkt inkl. enName, number, expansion, website, priceGuide."""
        return (self._get(f"/products/{int(id_product)}")[1] or {}).get("product", {}) or {}

    def get_price_guide(self, id_product) -> dict:
        pr = (self._get(f"/products/{id_product}")[1] or {}).get("product", {})
        return pr.get("priceGuide", {}) or {}

    def get_cheapest_offer(self, id_product, *, commercial_only: bool = False,
                           country: str | None = None, min_count: int = 1,
                           condition: str = "MT", id_language: int | None = None,
                           max_results: int = 20, is_foil: bool | None = None,
                           exclude_seller: str | None = None,
                           plain_only: bool = False) -> dict | None:
        """
        LIVE guenstigstes passendes Angebot via /articles (preis-sortiert).
        Filter: optional gewerblich + Land + Mindestmenge + Sprache (id_language —
        Cardmarket fuehrt ein Produkt je Set, die Sprache wird per Filter
        unterschieden; muss zur Sprache des Quell-EAN passen). Fuer Singles:
        is_foil (True/False, None = egal), exclude_seller (eigener Username, damit
        man sich nicht selbst unterbietet), plain_only (ohne signiert/altered/
        Playset). max_results > 100 blaettert in 100er-Seiten (MKM-Maximum je
        Abruf), bis ein passendes Angebot gefunden ist — noetig fuer country, das
        die API nicht serverseitig filtert. Rueckgabe: {price, shipping, count,
        is_commercial, country, vat, seller, condition, id_article} oder None.
        """
        page = min(max_results, ARTICLES_PAGE)
        for start in range(0, max_results, page):
            path = (f"/articles/{id_product}?minCondition={condition}"
                    f"&start={start}&maxResults={page}"
                    + (f"&idLanguage={id_language}" if id_language else "")
                    + (f"&isFoil={'true' if is_foil else 'false'}" if is_foil is not None else ""))
            arts = (self._get(path, timeout=60)[1] or {}).get("article", []) or []
            hit = self._first_offer(arts, commercial_only, country, min_count, is_foil,
                                    exclude_seller, plain_only)
            if hit or len(arts) < page:
                return hit
        return None

    @staticmethod
    def _first_offer(arts, commercial_only, country, min_count, is_foil, exclude_seller, plain_only):
        for a in sorted(arts, key=lambda x: _f(x.get("price")) or float("inf")):
            s = a.get("seller", {}) or {}
            addr = s.get("address", {}) or {}
            if commercial_only and not s.get("isCommercial"):
                continue
            if country and addr.get("country") != country:
                continue
            if (a.get("count") or 0) < min_count:
                continue
            if is_foil is not None and bool(a.get("isFoil")) != is_foil:
                continue
            if exclude_seller and (s.get("username") or "").casefold() == exclude_seller.casefold():
                continue
            if plain_only and (a.get("isSigned") or a.get("isAltered") or a.get("isPlayset")):
                continue
            return {
                "id_article":    a.get("idArticle"),
                "price":         _f(a.get("price")),
                "shipping":      _f(a.get("shippingCost")) or 0.0,
                "count":         a.get("count"),
                "is_commercial": bool(s.get("isCommercial")),
                "country":       addr.get("country"),
                "vat":           s.get("vat") or None,
                "seller":        s.get("username"),
                "condition":     a.get("condition"),
            }
        return None

    # ── Orders (nur lesend) ─────────────────────────────────────────────────
    def get_order(self, id_order) -> dict:
        """Eine Order inkl. Produktblock je Artikel (enName, expansion, rarity,
        idGame, nr). Die Listen-Endpoints liefern diesen Block NICHT."""
        return (self._get(f"/order/{int(id_order)}")[1] or {}).get("order", {}) or {}

    def get_orders(self, actor: str = "seller", state: str = "paid",
                   start: int = 1) -> tuple[list[dict], bool]:
        """
        Eine Seite Orders: (orders, more). `start` ist 1-basiert. MKM antwortet
        mit 206, wenn weitere Seiten folgen, 204/leer wenn nichts da ist.
        Artikel kommen OHNE Produktblock — fuer Namen get_order() nutzen.
        """
        if actor not in ORDER_ACTORS:
            raise ValueError(f"actor muss {ORDER_ACTORS} sein, nicht {actor!r}")
        if state not in ORDER_STATES:
            raise ValueError(f"state muss {ORDER_STATES} sein, nicht {state!r}")
        status, data = self._get(f"/orders/{actor}/{state}/{int(start)}", timeout=60)
        orders = (data or {}).get("order") or []
        if isinstance(orders, dict):                       # MKM: Einzelobjekt statt Liste
            orders = [orders]
        return orders, status == 206 and len(orders) >= ORDERS_PAGE

    def iter_orders(self, actor: str = "seller", state: str = "paid",
                    max_pages: int = 50):
        """Alle Orders eines Status, seitenweise (Schutz: max_pages)."""
        start = 1
        for _ in range(max_pages):
            orders, more = self.get_orders(actor, state, start)
            yield from orders
            if not more:
                return
            start += ORDERS_PAGE


    # ── Orders schreiben ────────────────────────────────────────────────────
    def send_order(self, id_order) -> dict:
        """Bestaetigt den Versand (PUT /order/{id}, action=send, SCHREIBT LIVE):
        paid -> sent, der Kaeufer wird benachrichtigt. Rueckgabe: die Order danach."""
        body = '<?xml version="1.0" encoding="UTF-8" ?><request><action>send</action></request>'
        return (self._send("PUT", f"/order/{int(id_order)}", body, timeout=60)[1] or {}).get("order", {}) or {}

    def set_tracking(self, id_order, tracking_number: str) -> dict:
        """Setzt die Sendungsnummer (PUT /order/{id}/tracking, SCHREIBT LIVE).
        MKM nimmt sie nur fuer Orders im Status sent an. Rueckgabe: die Order danach."""
        nr = str(tracking_number).strip()
        if not nr:
            raise ValueError("tracking_number ist leer")
        body = ('<?xml version="1.0" encoding="UTF-8" ?><request>'
                f"<trackingNumber>{escape(nr)}</trackingNumber></request>")
        return (self._send("PUT", f"/order/{int(id_order)}/tracking", body, timeout=60)[1] or {}).get("order", {}) or {}

    # ── Bestand schreiben ───────────────────────────────────────────────────
    def add_stock(self, articles: list[dict]) -> list[dict]:
        """
        Stellt Artikel in den eigenen Bestand ein (POST /stock, SCHREIBT LIVE).
        articles: [{idProduct, count, price, condition, idLanguage, isFoil,
        isSigned, isPlayset, comments}], gesendet in 100er-Paketen.
        Rueckgabe in Eingangsreihenfolge, je Artikel MKMs Eintrag aus `inserted`:
        {success: True, idArticle: {...}} oder {success: False, tried, error}.
        """
        out: list[dict] = []
        for i in range(0, len(articles), STOCK_CHUNK):
            chunk = articles[i:i + STOCK_CHUNK]
            _, data = self._send("POST", "/stock", _stock_xml(chunk), timeout=120)
            ins = (data or {}).get("inserted") or []
            if isinstance(ins, dict):                      # MKM: Einzelobjekt statt Liste
                ins = [ins]
            out.extend(ins)
            out.extend({"success": False, "error": "keine Rueckmeldung von MKM"}
                       for _ in range(len(chunk) - len(ins)))
        return out

    def get_stock_article(self, id_article) -> dict:
        """Eigener Bestandsartikel: {idArticle, idProduct, language{idLanguage},
        comments, price, count, condition, isFoil, isSigned, ...}; {} wenn weg."""
        return (self._get(f"/stock/article/{int(id_article)}", timeout=60)[1] or {}).get("article", {}) or {}

    def change_stock(self, articles: list[dict]) -> list[dict]:
        """
        Aendert eigene Artikel (PUT /stock, SCHREIBT LIVE). Je Artikel idArticle
        plus die Felder wie bei add_stock — sicherheitshalber alle aktuellen Werte
        mitschicken, nicht nur das geaenderte Feld.
        Rueckgabe in Eingangsreihenfolge: {success: True, article} oder
        {success: False, error}.
        """
        out: list[dict] = []
        for i in range(0, len(articles), STOCK_CHUNK):
            chunk = articles[i:i + STOCK_CHUNK]
            _, data = self._send("PUT", "/stock", _stock_xml(chunk), timeout=120)
            data = data or {}
            ok = data.get("updatedArticles") or []
            bad = data.get("notUpdatedArticles") or []
            ok = [ok] if isinstance(ok, dict) else ok
            bad = [bad] if isinstance(bad, dict) else bad
            by_id = {int(x["idArticle"]): {"success": True, "article": x} for x in ok if x.get("idArticle")}
            for x in bad:
                tid = (x.get("tried") or {}).get("idArticle")
                if tid:
                    by_id[int(tid)] = {"success": False, "error": x.get("error") or "abgelehnt"}
            out.extend(by_id.get(int(a["idArticle"]), {"success": False, "error": "keine Rueckmeldung von MKM"})
                       for a in chunk)
        return out


def _stock_xml(articles: list[dict]) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" ?>', "<request>"]
    for a in articles:
        if a.get("condition") not in CONDITIONS:
            raise ValueError(f"condition muss {CONDITIONS} sein, nicht {a.get('condition')!r}")
        parts.append("<article>")
        for k in STOCK_FIELDS:
            v = a.get(k)
            if v is None:
                continue
            if isinstance(v, bool):
                v = "true" if v else "false"
            elif k == "price":
                v = f"{float(v):.2f}"
            parts.append(f"<{k}>{escape(str(v))}</{k}>")
        parts.append("</article>")
    parts.append("</request>")
    return "".join(parts)


def _f(v):
    try:
        f = float(v)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None
