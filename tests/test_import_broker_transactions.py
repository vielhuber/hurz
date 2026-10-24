import asyncio
from datetime import datetime, timezone
import unittest

from scripts import import_broker_transactions as importer


class FakePlatform:
    """Answers with 100 entries for any page wider than a day, as the capped endpoint does."""

    def __init__(self):
        self.calls = []

    async def _raw_request(self, method, path, auth):
        self.calls.append(path)
        start = datetime.fromisoformat(path.split("from=")[1].split("&")[0])
        end = datetime.fromisoformat(path.split("to=")[1].split("&")[0])
        if (end - start).days >= 1:
            return {"transactions": [{"transactionType": "SWAP"}] * 100}
        return {"transactions": [{"transactionType": "TRADE", "dateUtc": f"{start:%Y-%m-%dT%H:%M:%S}"}]}


class ImportBrokerTransactionsTest(unittest.TestCase):
    def test_a_capped_page_is_fetched_again_in_halves_without_losing_entries(self):
        platform = FakePlatform()
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        rows = asyncio.run(importer.fetch_transactions(platform, start, datetime(2026, 10, 2, tzinfo=timezone.utc), pause=0))
        self.assertEqual(["TRADE", "TRADE"], [row["transactionType"] for row in rows])
        self.assertEqual("2026-10-01T00:00:00", rows[0]["dateUtc"])

    def test_a_transaction_becomes_one_row_with_its_amount_as_a_number(self):
        row = importer.as_row("capital_com", {"dateUtc": "2026-10-14T18:00:30.123", "transactionType": "TRADE",
                                              "instrumentName": "US30", "size": "1,234.5", "currency": "EUR",
                                              "reference": "ABC", "note": "Trade closed"})
        self.assertEqual(("capital_com", "2026-10-14 18:00:30", "TRADE", "US30", 1234.5, "EUR", "ABC",
                          "Trade closed"), row)


if __name__ == "__main__":
    unittest.main()
