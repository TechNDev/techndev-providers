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

## 🟡 cashback — Cashback-Layer (Portale + Hersteller + Karte)

**Kernidee:** Cashback ist eine **dritte EK-Korrektur** neben Warenpreis und
Inbound-Versand. Ein neuer, quer über alle Preisquellen liegender Resolver-Layer
ermittelt je Merchant/Produkt den anwendbaren Cashback und reicht ihn als
**Anzeige-Wert** in die Profitabilität — analog zum `sources/`-Registry-Muster
(`resolve() → CashbackOffer[]` + eine Registry-Zeile pro Quelle).

**Warum das wichtig ist:**
Portal- (Shoop/iGraal), Hersteller- (Lego-Aktionen) und Kreditkarten-Cashback
senken den effektiven EK real, tauchen heute aber nirgends in der Kalkulation
auf. Da product-catalog **und** midas-bot **und** die Watcher Profitabilität
rechnen, gehört der Layer in die Shared-Lib (Submodul-Graph).

**Designentscheidungen (festgelegt 2026-06-04):**
- **Nur anzeigen, nicht in die Gates.** Cashback ist unsicher (Tracking-Fail,
  späte/keine Auszahlung) → es darf KEIN knappes Angebot auf KAUFEN heben. Die
  KAUFEN/ABLEHNEN-Logik rechnet unverändert mit EK **ohne** Cashback; „Marge
  inkl. Cashback" wird separat ausgewiesen.
- **Drei Quellen:** Portale (Shoop/iGraal), Hersteller-Aktionen, Kreditkarte.
- **Ort:** techndev-providers (shared).

### Rechnerische Vereinfachung
Cashback senkt **nur den EK**; alle Gebühren sind **VK-basiert** (Referral, FBA,
eBay-Provision — nie EK-abhängig). Damit gilt für **jede** Plattform:

```
margin_inkl_cashback = margin_eur + cashback_netto
roi_inkl_cashback    = (margin_eur + cashback_netto) / (ek_netto − cashback_netto)
```

→ **Kein zweiter `qualify_all`-Lauf nötig**, Gate-Logik bleibt unangetastet. Die
Anzeige-Schicht ist reines Post-Processing auf `PlatformResult`.

### Datenmodell

```python
@dataclass
class CashbackOffer:
    merchant:   str            # Shop-Slug/Domain, z.B. "galeria"
    kind:       str            # "percent" | "fixed"
    value:      float          # 0.06 (=6 %) oder 20.00 (€)
    vat_link:   bool           # True = Lieferanten-/Rechnungsrabatt (Vorsteuer-korrigierbar)
                               # False = Portal/Karte (3rd party → voller Netto-Abzug)
    ean:        str | None = None    # None = merchant-weit, gesetzt = produktspezifisch
    valid_to:   str | None = None    # Ablaufdatum (Cashback-Aktionen verfallen!)
    cap:        float | None = None  # max. € Cashback
    min_order:  float | None = None
    exclusive:  bool = True          # stapelbar mit anderen?
    source:     str = "manual"       # "shoop" | "mydealz" | "card"
    label:      str = ""
```

### Paketstruktur

```
cashback/
  __init__.py        # CashbackOffer, resolve_all(), best_cashback(), REGISTRY
  base.py            # Dataclass + CashbackResolver-Protocol (analog PriceSource)
  portal.py          # Shoop/iGraal: Shop→%  (manual config, vat_link=False)
  manufacturer.py    # Lego/Hersteller-Aktion: ean→fix €, valid_to (vat_link=False)
  card.py            # globaler %-Satz auf jeden Kauf (vat_link=False)
```

- `best_cashback(offers, brutto, date)` — **summiert** über Kategorien (Portal +
  Karte + Hersteller stapeln, weil verschiedene Zahler), respektiert je Angebot
  `cap`/`min_order`/`valid_to`/`exclusive`.
- **Confidence-Haircut** `cashback_confidence` (z.B. 0.8) zentral angewandt — nur
  ein Teil des Nominalwerts geht in die Kalkulation (Tracking-Risiko).
- Config `cashback_config.json` (gitignored, `*_config.json`-Pattern):
  Portal-Sätze je Shop, Karten-Satz, optional Hersteller-Aktionen.

### Was noch fehlt (providers-Anteil)

| Komponente | Aufwand |
|---|---|
| `cashback/base.py` — `CashbackOffer` + `CashbackResolver`-Protocol | Klein |
| `portal.py` / `card.py` / `manufacturer.py` Adapter | Mittel |
| `best_cashback()` Selektor (Stapeln, cap, valid_to, confidence) | Mittel |
| `resolve_all()` + REGISTRY + `__init__.py`-Export | Klein |
| Config-Template + README-Kurzreferenz | Klein |

### Konsumenten-Anteil (eigene Repos, siehe deren BACKLOG)

- **reseller-profitability:** pure Helper `annotate_cashback(result, cashback_netto)`
  + 4 optionale Anzeige-Felder in `PlatformResult` (`cashback_netto`,
  `margin_eur_cb`, `margin_pct_cb`, `roi_cb`).
- **product-catalog:** `to_cashback_netto()` neben `to_ek_netto()`; Resolver in
  `process()` einhängen (EK-Gates unverändert!); `supplier_imports` um
  `cashback_netto` + `cashback_source` erweitern; `_result_dict()` nachreichen.
- **MarginPilot:** Spalte „Marge inkl. CB" + Badge mit Quelle/Satz
  (z.B. „Shoop 6 % + Amex 1 %"). ⚠️ Browser-JS-Template-Falle (`\\n` verdoppeln),
  Server-Neustart Port 7766.

**Umsetzungs-Reihenfolge:** providers/cashback → reseller-profitability
(`annotate_cashback`) → product-catalog (Verdrahtung + DB) → MarginPilot (Anzeige).

---
