# cubegolem — Provider

cubegolem.de Datenprovider — **Händler-EK-Preise (netto)** + Stammdaten.
Teil von `techndev-providers`, Nutzung wie die übrigen Provider via `providers/`-Submodul.

cubegolem.de ist ein B2B-Händlershop (PrestaShop). **Preise sind nur eingeloggt sichtbar** → ein Session-Cookie ist Pflicht.

## Schnellstart

```python
from cubegolem import CubeGolemProvider

prov = CubeGolemProvider(session_cookie=COOKIE)   # siehe „Cookie exportieren"

# alle Hauptkategorien
for sec in prov.list_sections():
    print(sec.slug, sec.name, len(sec.subcategories))

# alle Produkte einer Sektion (mit EK-Preisen)
produkte = prov.get_section("magic-the-gathering")
for p in produkte:
    print(p.name, p.ek_net, p.base_net, p.release_date)

# einzelnes Produkt
p = prov.get_product("mtg-aetherdrift-play-booster-display-30-boosters-de")
```

Mit Cache (empfohlen bei großen Sektionen / wiederholten Läufen):

```python
from cubegolem import CubeGolemCache
cache = CubeGolemCache("cubegolem_cache.db", session_cookie=COOKIE)
p = cache.get("mtg-aetherdrift-play-booster-display-30-boosters-de")  # Cache-first
print(p.price_is_live)   # False = aus Cache
```

## CLI

```bash
python -m cubegolem.cli --list                       # Hauptkategorien
python -m cubegolem.cli magic-the-gathering          # eine Sektion → CSV
python -m cubegolem.cli --all --out ./export         # alle Sektionen
python -m cubegolem.cli yu-gi-oh --no-prices         # nur Grid (ohne Detailseiten)
```

Beim ersten Start wird `cubegolem_config.json` (gitignored) als Template angelegt — dort die Session-Cookie eintragen.

## Cookie exportieren

### Einfach: `get_cookie.bat` (empfohlen)

1. Im Browser bei cubegolem.de **einloggen**.
2. DevTools (F12) → Tab **Network** → Seite neu laden (F5).
3. Beliebigen cubegolem.de-Request anklicken → Rechtsklick → **Copy** → **Copy as cURL (bash)**.
4. **`get_cookie.bat` doppelklicken.** Sie liest den Cookie aus der Zwischenablage, schreibt ihn in `cubegolem_config.json` und prüft live, ob die Session gültig ist.

> Warum nicht vollautomatisch aus Chrome? Chrome ≥ 127 (App-Bound Encryption) verschlüsselt Cookies so, dass sie von außen nicht mehr lesbar sind — ein automatisches Auslesen ginge nur mit Bypass-/Injektionstechniken (Malware-artig). Daher: einmal kopieren, Rest automatisch. Die HttpOnly-Session-Cookie ist ohnehin **nur** über den Network-Tab erreichbar, nicht über `document.cookie`.

`get_cookie.bat` / `setcookie.py` akzeptieren die Zwischenablage als cURL (`-b` / `-H 'cookie: …'`), als `Cookie: …`-Header-Block oder als rohen `name=value; …`-String.

### Manuell

Cookie-Header (`name=value; name2=value2; …`) direkt in `cubegolem_config.json` unter `session_cookie` eintragen, oder an `CubeGolemProvider(session_cookie=…)` übergeben.
Akzeptierte Formate (`_auth.normalize_cookie`): Roh-String, `dict`, oder Browser-Export-Liste `[{"name","value"}, …]`.

Läuft ein Lauf in `SessionExpiredError`, ist die Session abgelaufen → Cookie neu exportieren (Batch erneut ausführen).

## Felder (`Product`)

| Feld | Bedeutung |
|---|---|
| `ek_net` | Händler-EK (`.current-price`), **netto** |
| `base_net` | Listenpreis („Basispreis"); fehlt er, `== ek_net` |
| `discount_pct` | `1 − ek/base` (0.20 = 20 %) |
| `release_date` / `order_deadline` | Erscheinungs-/Bestellfrist (ISO), `None` = lagernd |
| `in_stock` | kein Vorbestelldatum hinterlegt |
| `ean` / `sku` | GTIN + Hersteller-Art.-Nr. (für Matching zu Amazon/eBay) |
| `manufacturer`, `image_url`, `category` | Stammdaten |
| `price_is_live` | `False` = aus Cache |

## Hinweise / Grenzen

- **Netto-Annahme:** Der Shop weist am Preis keinen MwSt-Hinweis aus; EK wird als netto behandelt (B2B-Konvention), nicht serverseitig bestätigt.
- **Enumeration** läuft über `/category/<sub>?section=<slug>` (server-gerendert, paginiert). `/section/<slug>` rendert nur eine Shell und ist ungeeignet.
- **Höflichkeit:** Default 0,8 s Mindestabstand zwischen Requests (`_rate.py`). Vollkatalog (~2.200 Produkte) ⇒ grob 30 Min.
- **Bild-URLs** können Sonderzeichen enthalten (z. B. U+2212 statt `-`); ggf. URL-encoden.

## Test

```bash
python -m cubegolem._selftest     # Offline-Parser-Smoke-Test (kein Cookie/Netz)
```
