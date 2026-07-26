#!/usr/bin/env python3
"""Parser-Tests fuer brickmerge/scraper.py — Gewicht, Teile, OVP-Masse.
Regression fuer den Bug "weight_part_g/weight_set_g immer None" (Naeherungszeichen
kommt als HTML-Entitaet &asymp;, Tausendertrenner ist ein Punkt) und fuer
"piece_count fehlt ab 1.000 Teilen". Fragmente sind woertlich aus dem Live-HTML
(brickmerge.de/10280/, /10294/, /75192/ am 27.07.2026)."""
from brickmerge.scraper import (RE_BOX_DIMS, RE_PIECE_COUNT, RE_WEIGHT_PARTS,
                                RE_WEIGHT_SET, _extract_box_dims, _extract_int)

# Woertliche Live-Fragmente (Entitaet, kein Leerzeichen vor 'g', Tausenderpunkt)
FRAG_10280 = ("&nbsp;| Teile: <strong>756</strong> &nbsp;| Teilegewicht: "
              "<strong>&asymp;494g</strong> &nbsp;| Setgewicht: <strong>&asymp;774g</strong>"
              " &nbsp;| OVP-Maße: <strong>26.2 x 38.2 x 7.05 cm</strong>")
FRAG_10294 = ("&nbsp;| Teile: <strong>9.090</strong> &nbsp;| Teilegewicht: "
              "<strong>&asymp;7.750g</strong> &nbsp;| Setgewicht: <strong>&asymp;12.645g</strong>"
              " &nbsp;| OVP-Maße: <strong>58.5 x 47.5 x 38.5 cm</strong>")
# Aeltere/andere Schreibweise: echtes Zeichen, Leerzeichen vor 'g'
FRAG_LITERAL = "Teilegewicht: <strong>≈ 228 g</strong> Setgewicht: <strong>~497 g</strong>"


def test_gewicht_mit_entitaet():
    assert _extract_int(RE_WEIGHT_PARTS, FRAG_10280) == 494
    assert _extract_int(RE_WEIGHT_SET,   FRAG_10280) == 774


def test_gewicht_mit_tausenderpunkt():
    # Der eigentliche Bug: 7.750 wurde zu None (nicht zu 7!)
    assert _extract_int(RE_WEIGHT_PARTS, FRAG_10294) == 7750
    assert _extract_int(RE_WEIGHT_SET,   FRAG_10294) == 12645


def test_gewicht_mit_literalem_zeichen():
    # Alte Schreibweise muss weiter greifen (Entitaet ODER Zeichen, Space optional)
    assert _extract_int(RE_WEIGHT_PARTS, FRAG_LITERAL) == 228
    assert _extract_int(RE_WEIGHT_SET,   FRAG_LITERAL) == 497


def test_teile_mit_und_ohne_tausenderpunkt():
    assert _extract_int(RE_PIECE_COUNT, FRAG_10280) == 756
    assert _extract_int(RE_PIECE_COUNT, FRAG_10294) == 9090


def test_teilegewicht_faelscht_teilezahl_nicht():
    # "Teile:" darf nicht in "Teilegewicht:" hineinmatchen
    nur_gewicht = "Teilegewicht: <strong>&asymp;494g</strong>"
    assert _extract_int(RE_PIECE_COUNT, nur_gewicht) is None


def test_ovp_masse_bleiben_dezimal():
    # Gegenprobe zum Separator-Strip: hier ist der Punkt Dezimaltrenner
    assert _extract_box_dims(FRAG_10280) == (26.2, 38.2, 7.05)
    assert _extract_box_dims(FRAG_10294) == (58.5, 47.5, 38.5)


def test_kein_treffer_gibt_none():
    assert _extract_int(RE_WEIGHT_SET, "<strong>kein Gewicht</strong>") is None
    assert RE_BOX_DIMS.search("keine Masse") is None
