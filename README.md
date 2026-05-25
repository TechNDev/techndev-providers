# techndev-providers

Gemeinsame Datenprovider-Bibliothek fuer TechNDev Tools.
Wird als **Git Submodul** unter `providers/` in EAN2JTL und amz-einkauf eingebunden.

## Provider

| Modul | Status | Beschreibung |
|---|---|---|
| `amazon_sp` | ✅ v1.0.0 | Amazon Selling Partner API — Katalog, Preise, Gebuehren, Restrictions |
| `icecat` | 🔜 Phase 2 | Icecat REST API — Produktdaten, Bilder, Features |
| `brickmerge` | 🔜 Phase 3 | brickmerge.de — CSV-Cache, EOL-Listen, Live-Preise |
| `ebay` | 🔜 Phase 4 | eBay Browse API — Marktpreise, Sell-Through |

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
from amazon_sp import estimate_fba_fees, check_restrictions

fee     = estimate_fba_fees('B07XY...', price=29.99, credentials=creds)
allowed = check_restrictions('B07XY...', seller_id='AXXX...', credentials=creds)
```

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

## Submodul aktualisieren

```bash
cd providers
git pull origin main
cd ..
git add providers
git commit -m "chore: techndev-providers aktualisiert"
```
