from unittest import TestCase

from scripts.crypto_floor_exemption import crypto_exempt


class CryptoFloorExemptionTest(TestCase):
    def setUp(self):
        self.floors = []
        self.terms = crypto_exempt(lambda df, e, pair, meta, atr_floor: self.floors.append((pair, atr_floor)))

    def test_the_crypto_class_is_priced_without_the_atr_floor(self):
        self.terms(None, 0, "BTCUSD", {}, 3.0)
        self.terms(None, 0, "ETHUSD", {}, 3.0)
        self.assertEqual([("BTCUSD", 0.0), ("ETHUSD", 0.0)], self.floors)

    def test_every_other_instrument_keeps_the_floor(self):
        self.terms(None, 0, "US500", {}, 3.0)
        self.terms(None, 0, "GOLD", {}, 3.0)
        self.assertEqual([("US500", 3.0), ("GOLD", 3.0)], self.floors)
