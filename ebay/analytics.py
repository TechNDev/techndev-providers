#!/usr/bin/env python3
"""
techndev-providers  ebay/analytics.py  v1.0.0
===============================================
eBay Sell Analytics API — Traffic-Report + Seller Standards.

Benoetigt: User-Token (OAuth Authorization Code Flow + refresh_token).
Scope:     sell.analytics.readonly

Credentials-Format:
  {
    'client_id':     '...',
    'client_secret': '...',
    'refresh_token': '...',   # aus eBay OAuth Consent Flow
    'env':           'production',
  }

Setup ohne refresh_token:
  from ebay._auth import make_oauth_url
  url = make_oauth_url(client_id, ru_name='<RuName aus Developer Portal>')
  # URL im Browser oeffnen → eBay-Login → Code aus Redirect-URL extrahieren
  # Code gegen Tokens tauschen: POST /identity/v1/oauth2/token?grant_type=authorization_code

Endpunkte:
  GET /sell/analytics/v1/traffic_report          → Aufrufe, Transaktionen, CTR, Conversion
  GET /sell/analytics/v1/seller_standards_profile → Verkaeuferbewertung / Performance-Level

CHANGELOG
---------
v1.0.0  (2026-05-28)
  - get_traffic_report(): Traffic-Daten (Aufrufe, Verkäufe, CTR) pro Tag/Listing.
  - get_seller_standards(): Performance-Level + Defektraten.
  - TrafficRow, TrafficReport, SellerStandards: Datenklassen.
  - Alle Felder None-sicher; graceful degradation bei fehlenden API-Feldern.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime   import date, timedelta, datetime

import requests

from ._auth import get_user_token, api_base, SCOPE_ANALYTICS

__version__ = "1.0.0"

TIMEOUT = 30

# ── Verfuegbare Metriken ───────────────────────────────────────────────────────
ALL_METRICS: list[str] = [
    "CLICK_THROUGH_RATE",               # Klickrate in Suchergebnissen
    "LISTING_IMPRESSION_TOTAL",         # Impressionen gesamt
    "LISTING_IMPRESSION_STORE",         # Impressionen aus eBay Store
    "LISTING_VIEWS_TOTAL",              # Gesamtaufrufe
    "LISTING_VIEWS_SOURCE_DIRECT",      # Direkte Aufrufe (Link/Lesezeichen)
    "LISTING_VIEWS_SOURCE_OFF_EBAY",    # Externe Quellen (Google etc.)
    "LISTING_VIEWS_SOURCE_OTHER_EBAY",  # Andere eBay-Seiten
    "LISTING_VIEWS_SOURCE_SEARCH_AND_BROWSE",  # Suche & Browse
    "SALES_CONVERSION_RATE",            # Konversionsrate (Verkauf / Aufrufe)
    "TRANSACTION",                      # Anzahl Transaktionen (Verkäufe)
]

# Sinnvolle Kern-Metriken fuer Standard-Aufruf
DEFAULT_METRICS: list[str] = [
    "LISTING_IMPRESSION_TOTAL",
    "LISTING_VIEWS_TOTAL",
    "LISTING_VIEWS_SOURCE_SEARCH_AND_BROWSE",
    "LISTING_VIEWS_SOURCE_OFF_EBAY",
    "CLICK_THROUGH_RATE",
    "SALES_CONVERSION_RATE",
    "TRANSACTION",
]


# ══════════════════════════════════════════════════════════════════════════════
# Datenklassen
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrafficRow:
    """Eine Zeile im Traffic-Report (ein Tag oder ein Listing)."""
    dimension_value:           str          # Datum (DAY) oder Listing-ID (LISTING)
    dimension_label:           str          # Lesbare Beschreibung
    impressions_total:         int | None
    impressions_store:         int | None
    views_total:               int | None
    views_search_and_browse:   int | None
    views_off_ebay:            int | None
    views_direct:              int | None
    views_other_ebay:          int | None
    click_through_rate:        float | None  # in Prozent, z.B. 2.34
    sales_conversion_rate:     float | None  # in Prozent
    transactions:              int | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrafficReport:
    """Ergebnis von get_traffic_report()."""
    date_from:    str
    date_to:      str
    dimension:    str               # DAY | LISTING
    fetched_at:   str
    rows:         list[TrafficRow] = field(default_factory=list)
    error:        str | None       = None

    # Aggregierte Summen (ueber alle Rows)
    total_impressions: int | None  = None
    total_views:       int | None  = None
    total_transactions: int | None = None
    avg_ctr:           float | None = None
    avg_conversion:    float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class StandardsMetric:
    """Eine Einzel-Kennzahl aus dem Seller Standards Profile."""
    name:        str
    value:       float | None
    threshold:   float | None     # eBay-Grenzwert
    unit:        str              # z.B. "PERCENT" oder "COUNT"
    basis:       int | None       # Bewertungsgrundlage (Anzahl Transaktionen)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SellerStandards:
    """Ergebnis von get_seller_standards()."""
    fetched_at:       str
    program:          str          # "EBAY_DE" o.ä.
    cycle:            str          # "CURRENT" | "PROJECTED"
    status:           str          # "TOP_RATED" | "ABOVE_STANDARD" | "BELOW_STANDARD"
    evaluation_date:  str
    metrics:          list[StandardsMetric] = field(default_factory=list)
    error:            str | None            = None

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# API-Funktionen
# ══════════════════════════════════════════════════════════════════════════════

def get_traffic_report(
    credentials: dict,
    date_from:   str | None = None,
    date_to:     str | None = None,
    dimension:   str        = "DAY",
    metrics:     list[str] | None = None,
    listing_id:  str | None = None,
) -> TrafficReport:
    """
    Ruft den Traffic-Report der Sell Analytics API ab.

    credentials:  {'client_id': ..., 'client_secret': ..., 'refresh_token': ..., 'env': ...}
    date_from:    Start ISO-Datum (Default: vor 30 Tagen), z.B. '2026-04-01'
    date_to:      End ISO-Datum   (Default: heute),        z.B. '2026-05-28'
    dimension:    'DAY' (pro Tag) oder 'LISTING' (pro Listing)
    metrics:      Liste aus ALL_METRICS (Default: DEFAULT_METRICS)
    listing_id:   Optional — nur Daten fuer dieses Listing

    Rueckgabe: TrafficReport (error != None bei Fehler)
    """
    today    = date.today()
    d_from   = date_from or (today - timedelta(days=30)).isoformat()
    d_to     = date_to   or today.isoformat()
    mets     = metrics or DEFAULT_METRICS
    ts       = datetime.now().isoformat(timespec="seconds")

    report = TrafficReport(
        date_from  = d_from,
        date_to    = d_to,
        dimension  = dimension,
        fetched_at = ts,
    )

    # Token
    try:
        token = _get_token(credentials)
    except Exception as e:
        report.error = f"Auth-Fehler: {e}"
        return report

    env = credentials.get("env", "production")
    params: dict = {
        "dimension_key":      dimension,
        "metric":             ",".join(mets),
        "dateRange.start":    f"{d_from}T00:00:00.000Z",
        "dateRange.end":      f"{d_to}T23:59:59.000Z",
    }
    if listing_id:
        params["listing_id"] = listing_id

    url = f"{api_base(env)}/sell/analytics/v1/traffic_report"
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
            },
            params=params,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        body = ""
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text[:300] if e.response is not None else ""
        report.error = f"HTTP {code}: {body}"
        return report
    except requests.RequestException as e:
        report.error = f"Netzwerkfehler: {e}"
        return report

    report.rows = _parse_traffic(resp.json(), mets)
    _aggregate(report)
    return report


def get_seller_standards(
    credentials: dict,
    cycle:       str = "CURRENT",
    program:     str = "EBAY_DE",
) -> SellerStandards:
    """
    Ruft das Seller Standards Profile ab (Performance-Level + Kennzahlen).

    credentials: {'client_id': ..., 'client_secret': ..., 'refresh_token': ..., 'env': ...}
    cycle:       'CURRENT' (aktueller Bewertungszeitraum) oder 'PROJECTED' (Prognose)
    program:     'EBAY_DE' | 'EBAY_US' | 'EBAY_UK' usw.

    Rueckgabe: SellerStandards (error != None bei Fehler)
    """
    ts = datetime.now().isoformat(timespec="seconds")
    result = SellerStandards(
        fetched_at      = ts,
        program         = program,
        cycle           = cycle,
        status          = "",
        evaluation_date = "",
    )

    try:
        token = _get_token(credentials)
    except Exception as e:
        result.error = f"Auth-Fehler: {e}"
        return result

    env = credentials.get("env", "production")
    url = f"{api_base(env)}/sell/analytics/v1/seller_standards_profile"
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
            },
            params={
                "cycle":   cycle,
                "program": program,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        body = ""
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text[:300] if e.response is not None else ""
        result.error = f"HTTP {code}: {body}"
        return result
    except requests.RequestException as e:
        result.error = f"Netzwerkfehler: {e}"
        return result

    result = _parse_standards(resp.json(), ts, program, cycle)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Interne Helfer
# ══════════════════════════════════════════════════════════════════════════════

def _get_token(credentials: dict) -> str:
    """Waehlt automatisch User- oder Application-Token je nach Credentials."""
    client_id     = credentials["client_id"]
    client_secret = credentials["client_secret"]
    refresh_token = credentials.get("refresh_token")
    env           = credentials.get("env", "production")

    if not refresh_token:
        raise ValueError(
            "Sell Analytics API benoetigt einen User-Token.\n"
            "Bitte 'refresh_token' in den Credentials hinterlegen.\n"
            "Setup: from ebay._auth import make_oauth_url; make_oauth_url(client_id, ru_name='...')"
        )
    return get_user_token(client_id, client_secret, refresh_token,
                          scope=SCOPE_ANALYTICS, env=env)


def _parse_traffic(data: dict, metrics: list[str]) -> list[TrafficRow]:
    """Parst die traffic_report-Antwort in TrafficRow-Objekte."""

    def _metric(name: str, record: dict) -> float | int | None:
        """Sucht den Wert einer Metrik im verschachtelten API-Record."""
        for entry in record.get("metricData", []):
            if entry.get("name") == name or entry.get("metricType") == name:
                raw = entry.get("value") or entry.get("metricValue")
                if raw is None:
                    return None
                try:
                    f = float(raw)
                    return int(f) if f == int(f) else round(f, 4)
                except (TypeError, ValueError):
                    return None
        return None

    rows: list[TrafficRow] = []

    # API-Antwortformat: entweder flache Liste oder dimensionierte Struktur
    records = (
        data.get("trafficReportData")
        or data.get("trafficReport")
        or data.get("records")
        or []
    )

    for rec in records:
        dim_val   = str(rec.get("dimensionValue") or rec.get("date") or rec.get("listingId") or "")
        dim_label = str(rec.get("dimensionLabel") or rec.get("title") or dim_val)

        rows.append(TrafficRow(
            dimension_value         = dim_val,
            dimension_label         = dim_label,
            impressions_total       = _metric("LISTING_IMPRESSION_TOTAL", rec),
            impressions_store       = _metric("LISTING_IMPRESSION_STORE", rec),
            views_total             = _metric("LISTING_VIEWS_TOTAL", rec),
            views_search_and_browse = _metric("LISTING_VIEWS_SOURCE_SEARCH_AND_BROWSE", rec),
            views_off_ebay          = _metric("LISTING_VIEWS_SOURCE_OFF_EBAY", rec),
            views_direct            = _metric("LISTING_VIEWS_SOURCE_DIRECT", rec),
            views_other_ebay        = _metric("LISTING_VIEWS_SOURCE_OTHER_EBAY", rec),
            click_through_rate      = _metric("CLICK_THROUGH_RATE", rec),
            sales_conversion_rate   = _metric("SALES_CONVERSION_RATE", rec),
            transactions            = _metric("TRANSACTION", rec),
        ))

    return rows


def _aggregate(report: TrafficReport) -> None:
    """Fuellt aggregierte Felder in TrafficReport aus den Rows."""
    impr  = [r.impressions_total    for r in report.rows if r.impressions_total    is not None]
    views = [r.views_total          for r in report.rows if r.views_total          is not None]
    trx   = [r.transactions         for r in report.rows if r.transactions         is not None]
    ctrs  = [r.click_through_rate   for r in report.rows if r.click_through_rate   is not None]
    conv  = [r.sales_conversion_rate for r in report.rows if r.sales_conversion_rate is not None]

    report.total_impressions  = sum(impr)  if impr  else None
    report.total_views        = sum(views) if views else None
    report.total_transactions = sum(trx)   if trx   else None
    report.avg_ctr            = round(sum(ctrs) / len(ctrs), 4) if ctrs else None
    report.avg_conversion     = round(sum(conv) / len(conv), 4) if conv else None


def _parse_standards(data: dict, ts: str, program: str, cycle: str) -> SellerStandards:
    """Parst die seller_standards_profile-Antwort."""
    profiles = data.get("standardsProfiles") or []
    profile  = next(
        (p for p in profiles if p.get("cycle", {}).get("cycleType") == cycle),
        profiles[0] if profiles else {},
    )

    status = (
        profile.get("sellerLevel")
        or profile.get("standardsStatus")
        or data.get("sellerLevel")
        or ""
    )
    eval_date = (
        profile.get("evaluationDate")
        or profile.get("cycle", {}).get("evaluationDate")
        or ""
    )

    metrics: list[StandardsMetric] = []
    for m in (profile.get("metrics") or data.get("metrics") or []):
        metrics.append(StandardsMetric(
            name      = m.get("metricKey") or m.get("name") or "",
            value     = _safe_float(m.get("value")),
            threshold = _safe_float(m.get("goal") or m.get("threshold")),
            unit      = m.get("unit") or "",
            basis     = _safe_int(m.get("basis") or m.get("transactionCount")),
        ))

    return SellerStandards(
        fetched_at      = ts,
        program         = program,
        cycle           = cycle,
        status          = status,
        evaluation_date = eval_date,
        metrics         = metrics,
    )


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None
