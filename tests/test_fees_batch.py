#!/usr/bin/env python3
"""Parser-Test fuer estimate_fees_batch (getMyFeesEstimates) — flache Entry-Struktur,
ASIN-Mapping, Bucket-Aufteilung. Gemockte API-Antwort, kein Netz."""
import amazon_sp.fees as fees


class _FakeResp:
    def __init__(self, payload):
        self.payload = payload


class _FakeProductFees:
    """Ersetzt sp_api ProductFees: liefert eine kanonische Batch-Antwort."""
    def __init__(self, *a, **k):
        pass

    def get_my_fees_estimates(self, reqs):
        def entry(asin, total, referral, fba, status="Success"):
            return {
                "Status": status,
                "FeesEstimateIdentifier": {"IdValue": asin, "SellerInputIdentifier": asin},
                "FeesEstimate": {
                    "TotalFeesEstimate": {"CurrencyCode": "EUR", "Amount": total},
                    "FeeDetailList": [
                        {"FeeType": "ReferralFee", "FinalFee": {"Amount": referral}},
                        {"FeeType": "FBAFees",     "FinalFee": {"Amount": fba}},
                    ],
                },
            }
        return _FakeResp([
            entry("B0AAA", 19.56, 15.55, 4.01),
            entry("B0BBB", None, 0, 0, status="ClientError"),   # kein TotalFeesEstimate
        ])


def test_estimate_fees_batch_parses_and_maps(monkeypatch):
    monkeypatch.setattr(fees, "ProductFees", _FakeProductFees)
    monkeypatch.setattr(fees, "get_credentials", lambda c=None: {"x": 1})
    monkeypatch.setattr(fees, "get_marketplace", lambda m: object())
    monkeypatch.setattr(fees, "get_marketplace_id", lambda m: "A1PA6795UKMFR9")
    fees.pricing_limiter.wait = lambda: None   # kein echtes Throttling im Test

    res = fees.estimate_fees_batch([("B0AAA", 106.92), ("B0BBB", 50.0)])
    assert res["B0AAA"]["total"] == 19.56
    assert res["B0AAA"]["referral_fee"] == 15.55
    assert res["B0AAA"]["fba_fee"] == 4.01
    assert res["B0AAA"]["status"] == "Success"
    # ClientError ohne Betrag -> total None
    assert res["B0BBB"]["total"] is None
    assert res["B0BBB"]["status"] == "ClientError"


def test_estimate_fees_batch_skips_zero_price(monkeypatch):
    monkeypatch.setattr(fees, "ProductFees", _FakeProductFees)
    monkeypatch.setattr(fees, "get_credentials", lambda c=None: {"x": 1})
    monkeypatch.setattr(fees, "get_marketplace", lambda m: object())
    monkeypatch.setattr(fees, "get_marketplace_id", lambda m: "A1PA6795UKMFR9")
    fees.pricing_limiter.wait = lambda: None
    # price 0 / None werden vor dem Call gefiltert -> kein Crash
    res = fees.estimate_fees_batch([("B0AAA", 0), ("B0CCC", None)])
    assert isinstance(res, dict)
