"""
test_cardmarket_stock_file.py — Sperre, 429-Wiederholung und Sammel-Lesen um GET /stock/file (cardmarket ≥ 1.7.0).
Ohne Netzwerk: _get / get_stock_article werden ersetzt. Die Sperre wird mit einem zweiten PROZESS geprüft.
"""
import base64
import gzip
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardmarket import client as cmc  # noqa: E402

CSV = ("idArticle;idProduct;English Name;Local Name;Exp.;Exp. Name;Price;Language;Condition;Foil?;Signed?;"
       "Playset?;Altered?;Comments;Amount;onSale\n"
       "11;501;Sol Ring;;C21;Commander 2021;1.50;1;NM;;;;;2026-11-01-A0001;2;1\n"
       "12;502;Arcane Signet;;C21;Commander 2021;0.80;3;EX;X;;;;F-001 · eck;1;1\n")


def _payload(text=CSV):
    return 200, {"stock": base64.b64encode(gzip.compress(text.encode())).decode()}


def _client():
    return cmc.CardmarketClient({"app_token": "a", "app_secret": "b", "access_token": "c", "access_secret": "d"})


class StockFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sperre = Path(self.tmp.name) / "s.lock"
        p = mock.patch.object(cmc, "STOCK_FILE_SPERRE", self.sperre)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        cmc.LIMIT.update(count=None, max=None, erschoepft=False)

    def test_fetch_stock_parst(self):
        c = _client()
        with mock.patch.object(c, "_get", return_value=_payload()) as g:
            st = c.fetch_stock()
        g.assert_called_once_with("/stock/file?idGame=1")
        self.assertEqual([a["idArticle"] for a in st], [11, 12])
        self.assertTrue(st[1]["foil"])
        self.assertEqual(st[1]["comment"], "F-001 · eck")

    def test_sealed_pfad(self):
        c = _client()
        with mock.patch.object(c, "_get", return_value=_payload()) as g:
            c.stock_file_raw(1, sealed=True)
        g.assert_called_once_with("/stock/file?idGame=1&isSealed=true")

    def test_429_wird_wiederholt(self):
        c = _client()
        fehler = RuntimeError("MKM GET /stock/file?idGame=1 -> HTTP 429: busy")
        with mock.patch.object(c, "_get", side_effect=[fehler, fehler, _payload()]) as g, \
                mock.patch.object(cmc.time, "sleep") as sl:
            self.assertEqual(len(c.fetch_stock()), 2)
        self.assertEqual(g.call_count, 3)
        self.assertEqual([x.args[0] for x in sl.call_args_list], [10, 30])

    def test_429_bei_vollem_tageslimit_nicht_wiederholen(self):
        c = _client()
        cmc.LIMIT.update(count=5000, max=5000)
        with mock.patch.object(c, "_get", side_effect=RuntimeError("-> HTTP 429: limit")) as g, \
                mock.patch.object(cmc.time, "sleep"):
            with self.assertRaises(RuntimeError):
                c.fetch_stock()
        self.assertEqual(g.call_count, 1)

    def test_andere_fehler_nicht_wiederholen(self):
        c = _client()
        with mock.patch.object(c, "_get", side_effect=RuntimeError("-> HTTP 401: x")) as g:
            with self.assertRaises(RuntimeError):
                c.fetch_stock()
        self.assertEqual(g.call_count, 1)

    def test_sperre_zwischen_threads(self):
        c = _client()
        laeuft, max_gleichzeitig = [0], [0]
        lock = threading.Lock()

        def langsam(*a, **k):
            with lock:
                laeuft[0] += 1
                max_gleichzeitig[0] = max(max_gleichzeitig[0], laeuft[0])
            time.sleep(0.3)
            with lock:
                laeuft[0] -= 1
            return _payload()
        with mock.patch.object(c, "_get", side_effect=langsam):
            ts = [threading.Thread(target=c.fetch_stock) for _ in range(3)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(max_gleichzeitig[0], 1)

    def test_sperre_zwischen_prozessen(self):
        code = ("import sys,time; sys.path.insert(0, sys.argv[1]); from cardmarket import client as c; "
                "from pathlib import Path\n"
                "with c.stock_file_sperre(5, Path(sys.argv[2])):\n print('drin', flush=True); time.sleep(2)")
        p = subprocess.Popen([sys.executable, "-c", code, str(Path(__file__).resolve().parents[1]), str(self.sperre)],
                             stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "drin")
            with self.assertRaises(TimeoutError):
                with cmc.stock_file_sperre(0.5):
                    pass
            t0 = time.monotonic()
            with cmc.stock_file_sperre(10):
                gewartet = time.monotonic() - t0
            self.assertGreater(gewartet, 0.5)
        finally:
            p.wait(10)

    def test_stock_articles_massen_und_einzeln(self):
        c = _client()
        with mock.patch.object(c, "_get", return_value=_payload()) as g, \
                mock.patch.object(c, "get_stock_article") as einzeln:
            arts = c.stock_articles([11, 12, 99], massen_ab=2)
        g.assert_called_once()
        einzeln.assert_not_called()
        self.assertEqual(set(arts), {11, 12})                     # 99 = verkauft/weg
        a = arts[12]
        self.assertEqual(a["language"]["idLanguage"], 3)
        self.assertEqual((a["comments"], a["count"], a["isFoil"], a["condition"]), ("F-001 · eck", 1, True, "EX"))
        self.assertNotIn("isPlayset", a)
        self.assertTrue(a["_aus_datei"])
        with mock.patch.object(c, "get_stock_article", side_effect=lambda i: {"idArticle": i} if i == 11 else {}) as e, \
                mock.patch.object(c, "_get") as g2:
            arts = c.stock_articles([11, 99], massen_ab=50)
        g2.assert_not_called()
        self.assertEqual(e.call_count, 2)
        self.assertEqual(set(arts), {11})


if __name__ == "__main__":
    unittest.main()
