# techndev-providers

Gemeinsame Datenprovider-Bibliothek fuer TechNDev Tools.
Wird als **Git Submodul** unter `providers/` in EAN2JTL und amz-einkauf eingebunden.

## Provider

| Modul | Status | Beschreibung |
|---|---|---|
| `amazon_sp` | ✅ v1.0.0 | Amazon Selling Partner API — Katalog, Preise, Gebuehren, Restrictions |
| `icecat` | ✅ v1.0.0 | Icecat REST API — Produktdaten, Bilder, Features |
| `brickmerge` | ✅ v1.0.0 | brickmerge.de — SQLite-Cache, EOL-Listen (5 Jahre), Live-Preise + Händleranzahl |
| `ebay` | ✅ v1.0.0 | eBay Browse API + Marketplace Insights — Marktpreise, Sell-Through-Rate |

## Abhängigkeiten

```
python-amazon-sp-api   # amazon_sp
requests               # alle Provider (ausser amazon_sp; schon Transitivabhängigkeit)
```

## Einbinden (Git Submodul)

```bash
# Im Consumer-Repo:
git submodule add https://github.com/TechNDev/techndev-providers.git providers
git submodule update --init --recursive
```

## sys.path-Pattern

Jedes Consumer-Skript setzt am Anfang (nach stdlib-Imports, vor Provider-Imports):

```python
import sys as _sys
from pathlib import Path as _Path
_PROV = _Path(__file__).resolve().parent / 'providers'
if str(_PROV) not in _sys.path:
    _sys.path.insert(0, str(_PROV))
```

Danach stehen alle Provider als Top-Level-Packages zur Verfügung:

```python
from amazon_sp import search_by_ean, CatalogResult
from amazon_sp import get_offers, estimate_fba_fees, check_restrictions
```

## amazon_sp — Kurzreferenz

### Katalog

```python
from amazon_sp import search_by_ean, search_by_asin, CatalogResult

result = search_by_ean('4010232075488', credentials, marketplace='DE')
if result.ok():
    print(result.asin, result.title, result.bsr)
    print(result.main_image)
    print(result.weight_kg, result.length_cm)
```

### Preise & Angebote

```python
from amazon_sp import get_offers, get_item_price, OffersResult

offers = get_offers('B07XY...', credentials)
print(offers.buy_box_price, offers.fba_sellers_new, offers.amazon_on_listing)

price = get_item_price('B07XY...', credentials)   # Buy-Box oder niedrigster Neupreis
```

### Gebuehren & Restrictions

```python
from amazon_sp import estimate_fba_fees, get_last_fee_error, check_restrictions

fee        = estimate_fba_fees('B07XY...', price=29.99, credentials=creds)
fees_error = get_last_fee_error()   # None = OK; str = Fehlertext (z.B. "AttributeError: ...")
allowed    = check_restrictions('B07XY...', seller_id='AXXX...', credentials=creds)
```

`get_last_fee_error()` gibt den thread-lokalen Fehler des letzten `estimate_fba_fees()`-Aufrufs
zurueck. Bei Fehler wird die Exception zusaetzlich auf `sys.stderr` ausgegeben.
Sinnvoll fuer Fehler-Sichtbarkeit in UI oder Logs ohne das Fehler-als-None-Muster zu brechen.

### CatalogResult-Felder

| Feld | Typ | Beschreibung |
|---|---|---|
| `ean`, `asin` | `str` | Identifikatoren |
| `title` / `.name` | `str` | Produkttitel (name = Alias fuer EAN2JTL) |
| `brand`, `mpn` | `str` | Marke, Modellnummer |
| `category` | `str` | Klassifikations-Kategorie |
| `short_desc`, `long_desc` | `str` | Bullet-Points aufbereitet |
| `bullet_points` | `list[str]` | Rohe Bullet-Point-Liste |
| `features` | `list[dict]` | `[{name, value}]` — Farbe, Groesse, etc. |
| `main_image`, `all_images` | `str / list[str]` | Bild-URLs |
| `weight_kg`, `ship_kg` | `float` | Artikel- und Versandgewicht |
| `width_cm`, `height_cm`, `length_cm` | `float` | Abmessungen |
| `bsr`, `bsr_category` | `int\|None, str` | Primaerer BSR (displayGroup first) |
| `bsr_display`, `bsr_display_category` | `int\|None, str` | Hauptkategorie-BSR (PDP) |
| `bsr_display_ranks` | `list[dict]` | Alle displayGroupRanks |
| `bsr_class_ranks` | `list[dict]` | Alle classificationRanks |
| `rating`, `review_count` | `float\|None, int` | Sternebewertung, Anzahl Reviews |
| `error` | `str\|None` | None = OK; sonst Fehlermeldung |

## icecat — Kurzreferenz

```python
from icecat import IcecatClient

client = IcecatClient(shopname, api_token, content_token, language='DE')

# Token pruefen
ok, msg = client.verify_token()

# Produkt abrufen (EAN, Brand+MPN oder Icecat-ID)
raw     = client.fetch_by_ean('4010232075488')
product = client.parse_product(raw, ean='4010232075488')
if product:
    print(product['name'], product['brand'])
    print(product['main_image'])
    print(product['features'])   # [{name, value}, ...]
```

### parse_product()-Felder

| Feld | Typ | Beschreibung |
|---|---|---|
| `ean`, `icecat_id` | `str` | Identifikatoren |
| `name`, `brand`, `mpn` | `str` | Titel, Marke, Modellnummer |
| `category` | `str` | Icecat-Kategorie |
| `short_desc`, `long_desc` | `str` | Kurz- und Langbeschreibung |
| `main_image` | `str` | URL des Hauptbildes |
| `all_images` | `list[str]` | Alle Bild-URLs (dedupliziert) |
| `features` | `list[dict]` | `[{name, value}]` — Technische Merkmale |

## brickmerge — Kurzreferenz

```python
from brickmerge import SetCatalog, BrickmergeProvider, get_catalog
from pathlib import Path

# ── Katalog (SQLite-Cache + CSV-Downloads) ─────────────────────────────────
catalog = SetCatalog(db_path=Path('brickmerge_catalog.db'), eol_years=5)

info = catalog.get('10294')           # SetInfo oder None
info = catalog.find_by_ean('12345678901234')

if info:
    print(info.name, info.uvp, info.status)   # 'active' | 'eol'
    print(info.eol_year)                       # None oder Jahreszahl

# Manuelles Refresh
catalog.refresh_active(force=True)
catalog.refresh_all_eol(n_years=5, force=True)

# ── Live-Scraping (Preise + Haendleranzahl) ────────────────────────────────
prov   = BrickmergeProvider(timeout=20)
result = prov.get_prices(
    '10294',
    ean_hint = info.ean if info else None,   # EAN aus Katalog (kein Regex noetig)
    uvp_hint = info.uvp if info else None,   # UVP aus Katalog (kein HTML-Regex)
    url_hint = info.brickmerge_url if info else None,
)
if result:
    print(result.best_price_current, result.seller_count)
    print(result.uvp_original, result.best_price_alltime)
```

### SetInfo-Felder

| Feld | Typ | Beschreibung |
|---|---|---|
| `set_no` | `str` | LEGO-Set-Nummer (z.B. `"10294"`) |
| `name`, `theme` | `str` | Produktname, Thema |
| `uvp` | `float\|None` | UVP in EUR (inkl. MwSt) |
| `year` | `int\|None` | Erscheinungsjahr |
| `ean` | `str\|None` | EAN-13 |
| `asin` | `str\|None` | Amazon ASIN |
| `brickmerge_url` | `str\|None` | Kanonische Brickmerge-URL |
| `status` | `str` | `'active'` oder `'eol'` |
| `eol_year` | `int\|None` | Jahrgang der EOL-Liste (None = aktiv) |

### MarketPrices-Felder

| Feld | Typ | Beschreibung |
|---|---|---|
| `uvp_original` | `float\|None` | UVP bei Release |
| `uvp_current` | `float\|None` | Aktuelle UVP (EOL oft hoeher) |
| `best_price_alltime` | `float\|None` | All-Time-Bestpreis |
| `best_price_alltime_days_ago` | `int\|None` | Alter des Bestpreises |
| `best_price_180d` | `float\|None` | Bestpreis letzte 180 Tage |
| `best_price_current` | `float\|None` | Aktueller Brickmerge-Bestpreis |
| `seller_count` | `int\|None` | Anzahl aktiver Haendler |
| `source`, `url`, `fetched_at` | `str` | Meta-Felder |

## ebay — Kurzreferenz

```python
from ebay import get_market_snapshot, search_sold, search_active

creds = {
    'client_id':     'DEINE_APP_ID',
    'client_secret': 'DEINE_CERT_ID',
    'env':           'production',    # oder 'sandbox'
}

# ── Kombinierter Snapshot (aktive + verkaufte Angebote + STR) ──────────────
snap = get_market_snapshot('4010232075488', creds, marketplace='EBAY_DE')

print(snap.sell_through_rate)      # z.B. 0.75 = 75% aller Angebote verkauft
print(snap.sold_median)            # Median-Verkaufspreis (letzte ~90 Tage)
print(snap.active_median)          # Median aktiver Angebotspreis
print(snap.sold_total)             # Gesamtanzahl verkaufte Angebote laut API
print(snap.active_total)           # Gesamtanzahl aktive Angebote laut API
print(snap.sold_error)             # None = OK; str = Fehler (z.B. 403)
print(snap.active_error)

for item in snap.sold_items[:5]:
    print(item.price, item.sold_date, item.title[:60])

# ── Nur verkaufte Angebote (Terapeak-Aequivalent) ──────────────────────────
total, items, error = search_sold('4010232075488', creds)

# ── Nur aktive Angebote (Marktpreisspiegel) ────────────────────────────────
total, items, error = search_active('4010232075488', creds)
```

### MarketSnapshot-Felder

| Feld | Typ | Beschreibung |
|---|---|---|
| `sold_total` | `int\|None` | Gesamtanzahl verkaufter Angebote laut API |
| `sold_median/mean/min/max` | `float\|None` | Preisstatistik der verkauften Angebote |
| `sold_count` | `int` | Anzahl Angebote mit Preis (Basis der Statistik) |
| `sold_items` | `list[SoldItem]` | Einzelne verkaufte Angebote |
| `sold_error` | `str\|None` | Fehlermeldung (None = OK) |
| `active_total` | `int\|None` | Gesamtanzahl aktiver Angebote laut API |
| `active_median/mean/min/max` | `float\|None` | Preisstatistik aktiver Angebote |
| `active_items` | `list[ActiveItem]` | Einzelne aktive Angebote |
| `active_error` | `str\|None` | Fehlermeldung (None = OK) |
| `sell_through_rate` | `float\|None` | `sold_total / (sold_total + active_total)` |

### Voraussetzungen eBay API

```
ebay_client_id     # App-ID (Client-ID) aus dem eBay Developer Portal
ebay_client_secret # Cert-ID (Client-Secret)
```

Marketplace Insights (verkaufte Angebote) benoetigt zusaetzlich eine
**Business Policy Approval** von eBay fuer den Scope `buy.marketplace.insights`.
Browse API (aktive Angebote) ist mit dem Basis-Scope verfuegbar.

## Submodul aktualisieren

```bash
cd providers
git pull origin main
cd ..
git add providers
git commit -m "chore: techndev-providers aktualisiert"
```
