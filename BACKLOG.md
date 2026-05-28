# BACKLOG — techndev-providers

Offene Features und Ideen für die gemeinsame Provider-Bibliothek.
Priorität: 🔴 Hoch · 🟡 Mittel · 🔵 Niedrig

---

## 🟡 amazon_sp — Preise von anderen Marktplätzen abrufen

**Kernidee:** Buy-Box-/Wettbewerbspreise einer ASIN nicht nur auf einem
Marktplatz, sondern marktplatzübergreifend (DE, FR, IT, ES, UK) in einem Aufruf
abrufen und vergleichbar zurückgeben. Grundlage für EU-Arbitrage-Entscheidungen
und Quellenwahl im Einkauf.

**Warum das wichtig ist:**
Dieselbe ASIN ist auf den EU-Marktplätzen oft unterschiedlich bepreist. Wer den
günstigsten Bezugs-Marktplatz und den teuersten Verkaufs-Marktplatz kennt, kann
gezielt cross-border einkaufen/verkaufen. Heute muss jeder Consumer
(`amz-einkauf`, `midas-bot`) `get_offers()` manuell pro Marktplatz mehrfach
aufrufen und die Ergebnisse selbst zusammenführen.

---

### Aktueller Stand

| Komponente | Status |
|---|---|
| `get_offers(asin, creds, marketplace='DE')` — Einzelmarktplatz | ✅ `pricing.py` |
| `get_item_price(asin, creds, marketplace='DE')` — Preis-only | ✅ `pricing.py` |
| Marktplatz-Tabellen (DE, FR, IT, ES, UK) | ✅ `_helpers.py` |
| Amazon-Eigenhändler-IDs je Marktplatz | ✅ `_helpers.py` |
| Rate-Limiting (`pricing_limiter`) + `@_retry` | ✅ `_rate.py` |
| **Komfort-Schicht für mehrere Marktplätze in einem Aufruf** | ❌ fehlt |

**Lücke:** Die marktplatzspezifische Mechanik (Marketplace-Enum, MarketplaceId,
Seller-ID) ist bereits vorhanden und parametrisiert — `get_offers` akzeptiert
`marketplace`. Es fehlt nur die aggregierende Funktion, die über mehrere
Marktplätze iteriert, Fehler je Marktplatz isoliert und ein Vergleichs-Ergebnis
liefert.

---

### Vorgeschlagene API

```python
from amazon_sp import get_offers_multi, MultiMarketResult

# Alle konfigurierten oder explizit gewünschten Marktplätze
result = get_offers_multi(
    asin='B07XY...',
    credentials=creds,
    marketplaces=['DE', 'FR', 'IT', 'ES'],   # Default: alle bekannten
)

for code, offers in result.by_marketplace.items():
    if offers.ok():
        print(code, offers.buy_box_price, offers.total_sellers_new)

print(result.cheapest)    # ('IT', 289.90) — günstigster Marktplatz mit Preis
print(result.dearest)     # ('DE', 349.00)
print(result.spread)      # 59.10 — Differenz teuerster/günstigster
```

**Datenmodell-Skizze:**

```python
@dataclass
class MultiMarketResult:
    by_marketplace: dict[str, OffersResult]   # 'DE' -> OffersResult
    # Abgeleitete Bequemlichkeits-Properties (nur Marktplätze mit best_price):
    #   cheapest -> tuple[str, float] | None
    #   dearest  -> tuple[str, float] | None
    #   spread   -> float | None        (dearest - cheapest)
```

---

### Umsetzungs-Hinweise

- **Wiederverwendung:** `get_offers_multi` ruft intern das bestehende
  `get_offers()` je Marktplatz auf — keine Duplizierung der ProductsV0-Logik.
- **Fehler-Isolation:** Ein fehlschlagender Marktplatz (z.B. ASIN dort nicht
  gelistet) darf die übrigen nicht abbrechen. Fehler landen im `error`-Feld des
  jeweiligen `OffersResult`, nicht als Exception.
- **Rate-Limiting:** Mehrere Marktplätze = mehrere Aufrufe. `pricing_limiter`
  greift bereits pro `get_offers()`-Aufruf; bei vielen ASINs × Marktplätzen auf
  Drossel-Verhalten achten (ProductsV0-Quota gilt pro Marktplatz/Region).
- **Währung:** EU-Marktplätze liefern EUR (außer UK = GBP). Ergebnis sollte
  Währung mitführen oder UK getrennt behandeln, damit `spread`/`cheapest` keine
  EUR/GBP-Werte vermischt. → Default-Marktplatzliste evtl. ohne UK.
- **ASIN vs. EAN:** ASINs sind in der EU meist marktplatzübergreifend gleich,
  aber nicht garantiert. Für robuste Cross-Market-Suche ggf. EAN je Marktplatz
  zu ASIN auflösen (`search_by_ean` pro Marktplatz) statt eine ASIN anzunehmen.

---

### Was bereits vorhanden ist

| Baustein | Status |
|---|---|
| Einzelmarktplatz-Preisabruf | ✅ `get_offers()` |
| Marktplatz-Auflösung (Enum, ID, Seller-ID) | ✅ `_helpers.py` |
| Rate-Limiter + Retry-Decorator | ✅ `_rate.py` |
| `OffersResult` mit `best_price`-Property | ✅ `pricing.py` |

### Was noch fehlt

| Komponente | Aufwand |
|---|---|
| `get_offers_multi()` — Iteration + Aggregation | Klein |
| `MultiMarketResult` Datenmodell (cheapest/dearest/spread) | Klein |
| Währungs-Behandlung (UK/GBP getrennt) | Klein |
| Optional: EAN→ASIN je Marktplatz statt fixe ASIN | Mittel |
| Export in `__init__.py` (`__all__`) + README-Kurzreferenz | Klein |

---
