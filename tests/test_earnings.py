import csv
import json
import unittest
from datetime import date, timedelta

from src.earnings import build_daily, validate_records
from src.fetch_market import ROOT
from src.news_schema import earnings_values


class EarningsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = json.loads((ROOT / "research/earnings.json").read_text(encoding="utf-8"))
        first, last = date(2020, 1, 1), date(2024, 12, 31)
        days = (first + timedelta(days=i) for i in range((last - first).days + 1))
        cls.weekdays = [day for day in days if day.weekday() < 5]

    def test_reviewed_periods_are_complete_and_unique(self):
        validate_records(self.records)
        with self.assertRaises(ValueError):
            validate_records(self.records[1:])
        with self.assertRaises(ValueError):
            validate_records(self.records + [self.records[0]])

    def test_quarterly_pulse_and_undefined_yoy(self):
        rows = build_daily(self.weekdays, self.records)
        lookup = {(r["date"], r["symbol"]): r for r in rows}
        self.assertEqual(sum(r["earnings_release"] for r in rows), 60)
        self.assertEqual(lookup["2020-01-08", "005930"]["earnings_op_yoy_pct"], -34.26)
        self.assertEqual(lookup["2020-01-09", "005930"]["earnings_release"], 0)
        self.assertEqual(lookup["2023-04-26", "000660"]["earnings_release"], 1)
        self.assertEqual(lookup["2023-04-26", "000660"]["earnings_yoy_available"], 0)
        self.assertEqual(lookup["2023-04-26", "000660"]["earnings_op_yoy_pct"], 0)

    def test_public_daily_context_uses_the_etoday_release_for_every_session(self):
        with (ROOT / "research/etoday_daily_context.csv").open(encoding="utf-8", newline="") as f:
            published = list(csv.DictReader(f))
        days = sorted({date.fromisoformat(r["date"]) for r in published})
        expected = [(r["date"], r["symbol"], earnings_values(r)) for r in build_daily(days, self.records)]
        self.assertEqual([(r["date"], r["symbol"], earnings_values(r)) for r in published], expected)
        self.assertEqual(len(expected), 3693)

    def test_modified_copy_waits_for_next_session(self):
        records = [dict(row) for row in self.records]
        row = next(r for r in records if r["article_id"] == "1842595")
        row["modified_at"] = row["available_at"] = "2020-01-08T16:00:00+09:00"
        result = {r["date"]: r for r in build_daily(self.weekdays, records) if r["symbol"] == "005930"}
        self.assertEqual(result["2020-01-08"]["earnings_release"], 0)
        self.assertEqual(result["2020-01-09"]["earnings_release"], 1)
        row["available_at"] = row["published_at"]
        with self.assertRaises(ValueError):
            validate_records(records)


if __name__ == "__main__":
    unittest.main()
