#!/usr/bin/env python3
"""
amazon_sp  reports.py  v1.0.0
================================
Amazon SP-API Reports-API: Berichte anfordern, pollen, herunterladen, parsen.

Deckt die fuer Reimbursement-/Bestands-Auswertungen noetigen Berichtstypen ab:
  - GET_LEDGER_DETAIL_VIEW_DATA                      (Lagerbestandsbuch, Event-Ebene)
  - GET_FBA_REIMBURSEMENTS_DATA                      (Erstattungen an den Haendler)
  - GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA        (Kundenretouren)
  - GET_FBA_FULFILLMENT_REMOVAL_ORDER_DETAIL_DATA    (Remissions-/Entfernungsauftraege)

Oeffentliche API
----------------
  from amazon_sp import request_report
  from amazon_sp import ledger_detail, reimbursements, customer_returns, removal_order_detail

Jede Funktion liefert list[dict] (eine Zeile = ein dict, Header-Keys
normalisiert: lowercase, Leer/Bindestrich -> '_'). Spaltenzugriff robust
ueber row.get('event_type') etc.

Ablauf intern (request_report):
  1. create_report(reportType, dataStartTime, dataEndTime, reportOptions)
  2. get_report(reportId) pollen bis processingStatus == DONE (oder FATAL/CANCELLED)
  3. get_report_document(reportDocumentId) -> Text (decrypt + ggf. GZIP-Entpacken)
  4. Tab-getrennte Tabelle -> list[dict]

Rate-Limits (konservativ): createReport/getReportDocument sind sehr knapp
(0,0167 Req/s, Burst 15). Pro Lauf werden nur wenige Berichte erzeugt, daher
genuegt ein moderater Limiter + Burst. get_report-Polling laeuft mit ~2 Req/s.

CHANGELOG
---------
v1.0.0  (2026-06-10)
  - Initiales Release. request_report() (create/poll/download/parse) plus
    getypte Wrapper ledger_detail/reimbursements/customer_returns/
    removal_order_detail. Defensive Dokument-Auslieferung (document-Text
    ODER url+GZIP), header-normalisiertes Parsen.
"""
from __future__ import annotations

import gzip
import io
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional

from sp_api.api import Reports

from ._rate import RateLimiter, _retry
from ._credentials import get_credentials
from ._helpers import get_marketplace

__version__ = "1.0.0"

# Berichtstyp-Konstanten (sprechende Namen fuer die Wrapper)
LEDGER_DETAIL          = "GET_LEDGER_DETAIL_VIEW_DATA"
REIMBURSEMENTS         = "GET_FBA_REIMBURSEMENTS_DATA"
CUSTOMER_RETURNS       = "GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA"
REMOVAL_ORDER_DETAIL   = "GET_FBA_FULFILLMENT_REMOVAL_ORDER_DETAIL_DATA"

# createReport/getReportDocument haben harte Limits -> Burst nutzen, moderat warten.
reports_limiter = RateLimiter(min_interval_s=2.0)

# Endzustaende des Report-Pollings
_DONE     = "DONE"
_FAILURES = ("FATAL", "CANCELLED")


# ══════════════════════════════════════════════════════════════════════════════
# Oeffentliche API
# ══════════════════════════════════════════════════════════════════════════════

@_retry
def request_report(
    report_type: str,
    start: datetime,
    end: datetime,
    report_options: Optional[dict] = None,
    credentials: Optional[dict] = None,
    marketplace: str = "DE",
    poll_interval_s: float = 20.0,
    timeout_s: float = 900.0,
) -> list[dict]:
    """
    Fordert einen SP-API-Bericht an und gibt die Zeilen als list[dict] zurueck.

    start/end: tz-aware oder naive datetime (naive wird als UTC interpretiert).
    report_options: berichtsspezifische Optionen (z.B. Ledger:
                    {'aggregateByLocation': 'FC', 'aggregatedByTimePeriod': 'DAILY'}).
    timeout_s: maximale Gesamt-Wartezeit aufs Fertigstellen.

    Wirft RuntimeError bei FATAL/CANCELLED oder Timeout. HTTP 429 wird fuer
    @_retry propagiert.
    """
    credentials = get_credentials(credentials)
    mktpl       = get_marketplace(marketplace)
    mktpl_id    = mktpl.marketplace_id

    client = Reports(credentials=credentials, marketplace=mktpl)

    # ── 1. Bericht anfordern ────────────────────────────────────────────────
    params = dict(
        reportType=report_type,
        dataStartTime=_iso(start),
        dataEndTime=_iso(end),
        marketplaceIds=[mktpl_id],
    )
    if report_options:
        params["reportOptions"] = report_options

    reports_limiter.wait()
    created   = client.create_report(**params)
    report_id = (created.payload or {}).get("reportId")
    if not report_id:
        raise RuntimeError(f"create_report ohne reportId: {created.payload!r}")

    # ── 2. Pollen bis DONE ──────────────────────────────────────────────────
    deadline    = time.monotonic() + timeout_s
    document_id = None
    while True:
        reports_limiter.wait()
        status_payload = (client.get_report(report_id).payload or {})
        status         = status_payload.get("processingStatus")
        if status == _DONE:
            document_id = status_payload.get("reportDocumentId")
            break
        if status in _FAILURES:
            raise RuntimeError(f"Bericht {report_type} -> {status} (reportId {report_id})")
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"Timeout ({timeout_s:.0f}s) beim Warten auf {report_type} "
                f"(reportId {report_id}, letzter Status {status})"
            )
        time.sleep(poll_interval_s)

    if not document_id:
        raise RuntimeError(f"Bericht {report_type} DONE, aber ohne reportDocumentId")

    # ── 3. Dokument laden ───────────────────────────────────────────────────
    text = _download_document(client, document_id)

    # ── 4. Tabelle parsen ───────────────────────────────────────────────────
    return _parse_table(text)


def ledger_detail(
    start: datetime,
    end: datetime,
    credentials: Optional[dict] = None,
    marketplace: str = "DE",
    aggregate_by_location: str = "FC",
) -> list[dict]:
    """
    Lagerbestandsbuch (Detailansicht) als list[dict].

    Relevante Spalten (header-normalisiert): date, fnsku, asin, msku, title,
    event_type, reference_id, quantity, fulfillment_center, disposition,
    reason, country.

    event_type-Werte (Auswahl): Receipts, CustomerReturns, VendorReturns,
    Adjustments, WhseTransfers, Removals, Disposals, Found, Lost, Damaged.
    Die DETAILANSICHT wird ueber reportOptions eventType='' (leer = alle)
    erzwungen; aggregateByLocation FC = je Logistikzentrum.
    """
    return request_report(
        LEDGER_DETAIL, start, end,
        report_options={
            "aggregateByLocation": aggregate_by_location,
            "aggregatedByTimePeriod": "DAILY",
        },
        credentials=credentials, marketplace=marketplace,
    )


def reimbursements(
    start: datetime,
    end: datetime,
    credentials: Optional[dict] = None,
    marketplace: str = "DE",
) -> list[dict]:
    """
    Erstattungen an den Haendler als list[dict].

    Relevante Spalten: approval_date, reimbursement_id, case_id,
    amazon_order_id, reason, sku, fnsku, asin, amount_total,
    quantity_reimbursed_cash, quantity_reimbursed_inventory, ...
    """
    return request_report(
        REIMBURSEMENTS, start, end,
        credentials=credentials, marketplace=marketplace,
    )


def customer_returns(
    start: datetime,
    end: datetime,
    credentials: Optional[dict] = None,
    marketplace: str = "DE",
) -> list[dict]:
    """
    FBA-Kundenretouren als list[dict].

    Relevante Spalten: return_date, order_id, sku, asin, fnsku,
    product_name, quantity, detailed_disposition, status, ...
    detailed_disposition: SELLABLE / CUSTOMER_DAMAGED / DEFECTIVE / ...
    """
    return request_report(
        CUSTOMER_RETURNS, start, end,
        credentials=credentials, marketplace=marketplace,
    )


def removal_order_detail(
    start: datetime,
    end: datetime,
    credentials: Optional[dict] = None,
    marketplace: str = "DE",
) -> list[dict]:
    """
    Remissions-/Entfernungsauftraege (Detail) als list[dict].

    Relevante Spalten: request_date, order_id, order_type, order_status,
    sku, fnsku, disposition, requested_quantity, ...
    order_type: Return / Disposal. order_status: Completed / Cancelled / ...
    """
    return request_report(
        REMOVAL_ORDER_DETAIL, start, end,
        credentials=credentials, marketplace=marketplace,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Interne Helfer
# ══════════════════════════════════════════════════════════════════════════════

def _iso(dt: datetime) -> str:
    """datetime -> ISO-8601 mit UTC-Offset. Naive datetime gilt als UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _download_document(client: Reports, document_id: str) -> str:
    """
    Laedt das Report-Dokument als Text. Robust ueber SDK-Versionen:
      - liefert get_report_document ein 'document' (Text) -> direkt nutzen
      - liefert es 'url' (+ ggf. compressionAlgorithm GZIP) -> selbst laden
    """
    reports_limiter.wait()
    try:
        doc = client.get_report_document(document_id, decrypt=True)
    except TypeError:
        # aeltere/andere Signatur ohne decrypt-Flag
        doc = client.get_report_document(document_id)
    payload = doc.payload or {}

    # Fall A: SDK hat bereits dekodiert
    text = payload.get("document")
    if isinstance(text, str) and text:
        return text

    # Fall B: nur URL -> selbst herunterladen (+ optional GZIP)
    url = payload.get("url")
    if not url:
        raise RuntimeError(f"Report-Dokument ohne 'document'/'url': {payload!r}")

    with urllib.request.urlopen(url, timeout=120) as resp:   # noqa: S310 (Amazon-URL)
        raw = resp.read()

    if str(payload.get("compressionAlgorithm", "")).upper() == "GZIP":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    else:
        # manche Amazon-URLs liefern GZIP ohne explizites Flag -> defensiv pruefen
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()

    # Amazon-FBA-Berichte sind i.d.R. cp1252 oder utf-8 -> tolerant dekodieren
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _norm_key(header: str) -> str:
    """Header -> normalisierter dict-Key: lowercase, nicht-alnum -> '_'."""
    out = []
    for ch in header.strip().lower():
        out.append(ch if ch.isalnum() else "_")
    key = "".join(out).strip("_")
    while "__" in key:
        key = key.replace("__", "_")
    return key


def _parse_table(text: str) -> list[dict]:
    """
    Tab-getrennte Tabelle mit Kopfzeile -> list[dict] (Keys normalisiert).
    Leere Zeilen werden uebersprungen. Spalten ohne Header werden ignoriert.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # erste nicht-leere Zeile = Header
    header = None
    rows: list[dict] = []
    for line in lines:
        if header is None:
            if line.strip() == "":
                continue
            header = [_norm_key(h) for h in line.split("\t")]
            continue
        if line.strip() == "":
            continue
        cells = line.split("\t")
        row = {}
        for i, key in enumerate(header):
            if not key:
                continue
            row[key] = cells[i].strip() if i < len(cells) else ""
        rows.append(row)
    return rows
