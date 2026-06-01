#!/usr/bin/env python3
"""
techndev-providers  cubegolem/_selftest.py  v1.0.0
====================================================
Offline-Parser-Smoke-Test (kein Netzwerk, kein Cookie).
Prueft RE_*/_parse_* gegen die real beobachteten HTML-Strukturen
(Aetherdrift-Detail + Hobbit/Chessex-Grid, Stand Mai 2026).

Lauf:  python -m cubegolem._selftest   (aus techndev-providers/)
"""
from __future__ import annotations

from . import scraper as S

# ── Reale Detail-Markup-Struktur (Aetherdrift) ────────────────────────────────
DETAIL = (
    '<title>MTG - Aetherdrift Play Booster Display (30 Boosters) - DE - CubeGolem.de Shop</title>'
    '<div class="product-prices"><div class="current-price">'
    '  <span class="current-price-value">\n        90,48 €</span></div>'
    '  <div class="product-unit-price">Basispreis: &nbsp;\n        113,10 €</div></div>'
    '<div class="product-cover">'
    '<img class="js-qv-product-cover img-fluid lazyload" '
    'data-zoom-image="https://cubegolem.de/products/5568/conversions/d41311000−1-full.jpg" '
    'data-src="https://cubegolem.de/products/5568/conversions/d41311000−1-full.jpg" '
    'src="https://cubegolem.de/images/lazy-loader.svg"></div>'
    '<div class="product-information">EAN: <strong>5010996284594</strong><br>'
    'Art. Nr.: <strong>D41311000</strong></div>'
    '<a href="/manufacturer/wizards-of-the-coast">Wizards of the Coast</a>'
)

# ── Reale Grid-Markup-Struktur ────────────────────────────────────────────────
GRID = (
    '<section class="promo"><article class="product-miniature">'
    '<a class="product-name" href="https://cubegolem.de/product/echoes-promo" '
    'title="Echoes Promo">x</a><span>Erscheint: 20.10.2026</span></article></section>'
    '<div id="js-product-list"><div class="products">'
    '<article class="product_item product-miniature js-product-miniature">'
    '<a class="product_img_link product-thumbnail" '
    'href="https://cubegolem.de/product/mtg-the-hobbit-bundle-de" title="MTG - The Hobbit Bundle - DE"></a>'
    '<a class="product-name" href="https://cubegolem.de/product/mtg-the-hobbit-bundle-de" '
    'title="MTG - The Hobbit Bundle - DE">MTG - The Hobbit Bundle - DE</a>'
    '<div class="product-price-and-shipping">Deutsch '
    'Erscheinungsdatum Fr., 14. Aug 2026 Bestellfrist Fr., 26. Jun 2026</div></article>'
    '<article class="product_item product-miniature js-product-miniature">'
    '<a class="product-name" href="https://cubegolem.de/product/chessex-festive-x-d6" '
    'title="Chessex Festive X D6">Chessex Festive X D6</a>'
    '<div class="product-price-and-shipping">Englisch</div></article>'
    '</div></div><nav class="pagination">1 2 3</nav>'
    '<section class="footer-promo"><article class="product-miniature">'
    '<a class="product-name" href="https://cubegolem.de/product/footer-promo" title="Footer">x</a>'
    '</article></section>'
)

SELECT = (
    '<select><option value="all">Kategorien</option>'
    '<option value="magic-the-gathering">Magic: The Gathering</option>'
    '<option value="_reality-fracture">&middot; Reality Fracture</option>'
    '<option value="_the-hobbit">&middot; The Hobbit</option>'
    '<option value="chessex">Chessex</option>'
    '<option value="_dice-d6-16mm">&middot; Dice (D6 - 16mm)</option></select>'
)


def _check(name, cond):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    return cond


def main() -> int:
    ok = True

    print("Detail-Parser:")
    ek   = S._money(S.RE_EK.search(DETAIL).group(1))
    base = S._money(S.RE_BASE.search(DETAIL).group(1))
    ok &= _check("ek_net == 90.48", ek == 90.48)
    ok &= _check("base_net == 113.10", base == 113.10)
    ok &= _check("discount == 0.2", round(1 - ek / base, 3) == 0.2)
    cover = S.RE_COVER.search(DETAIL).group(0)
    img = S.RE_DATA.search(cover).group(1)
    ok &= _check("image hat U+2212", "−" in img and img.endswith("-full.jpg"))
    ok &= _check("ean == 5010996284594", S.RE_EAN.search(DETAIL).group(1) == "5010996284594")
    ok &= _check("sku == D41311000", S.RE_SKU.search(DETAIL).group(1) == "D41311000")
    ok &= _check("mfr == Wizards of the Coast",
                 S.RE_MFR.search(DETAIL).group(1).strip() == "Wizards of the Coast")

    print("Datum-Normalisierung:")
    ok &= _check("'02.10.2026' -> 2026-10-02", S._de_date("02.10.2026") == "2026-10-02")
    ok &= _check("'Fr., 14. Aug 2026' -> 2026-08-14",
                 S._de_date("Fr., 14. Aug 2026") == "2026-08-14")
    ok &= _check("'2. Mär 2026' -> 2026-03-02", S._de_date("2. Mär 2026") == "2026-03-02")
    ok &= _check("leer -> None", S._de_date(None) is None)

    print("Grid-Parser (nur #js-product-list, keine Promos):")
    grid = S._parse_grid(GRID)
    slugs = [g["slug"] for g in grid]
    ok &= _check("genau 2 Produkte (Promos raus)", len(grid) == 2)
    ok &= _check("kein echoes/footer-promo",
                 "echoes-promo" not in slugs and "footer-promo" not in slugs)
    hobbit = next((g for g in grid if g["slug"] == "mtg-the-hobbit-bundle-de"), {})
    ok &= _check("hobbit name", hobbit.get("name") == "MTG - The Hobbit Bundle - DE")
    ok &= _check("hobbit release 2026-08-14", hobbit.get("release_date") == "2026-08-14")
    ok &= _check("hobbit deadline 2026-06-26", hobbit.get("order_deadline") == "2026-06-26")
    dice = next((g for g in grid if g["slug"] == "chessex-festive-x-d6"), {})
    ok &= _check("dice ohne Datum (lagernd)", dice.get("release_date") is None)

    print("Sektions-Parser:")
    secs = S._parse_sections(SELECT)
    mtg = next((s for s in secs if s.slug == "magic-the-gathering"), None)
    chx = next((s for s in secs if s.slug == "chessex"), None)
    ok &= _check("2 Sektionen", len(secs) == 2)
    ok &= _check("MTG name + 2 subs",
                 mtg and mtg.name == "Magic: The Gathering"
                 and mtg.subcategories == ["reality-fracture", "the-hobbit"])
    ok &= _check("Chessex 1 sub",
                 chx and chx.subcategories == ["dice-d6-16mm"])

    print("\n" + ("ALLE TESTS BESTANDEN" if ok else "TESTS FEHLGESCHLAGEN"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
