import unittest
from datetime import date

from src.news_events import build_daily, eligible_headline, signal_day


DAYS = [date.fromisoformat(x) for x in ("2020-09-03", "2020-09-04", "2020-09-07", "2020-09-08")]
def review(key, stamp, modified=None):
    return dict(id=key, symbol="005930", event_type="signed", role="seller",
                event_id="contract", report_id="same_dispatch", published_at=stamp, modified_at=modified)


class NewsTest(unittest.TestCase):
    def test_exact_window(self):
        self.assertTrue(eligible_headline("삼성전자와 공급계약"))
        self.assertFalse(eligible_headline("삼성전자와 반도체 공급 기대"))
        self.assertFalse(eligible_headline("네이버·은행 협약 체결"))
        self.assertEqual(signal_day("2020-09-04T15:30:00+09:00", DAYS), DAYS[1])
        self.assertEqual(signal_day("2020-09-04T15:30:01+09:00", DAYS), DAYS[2])
        self.assertEqual(signal_day("2020-09-06", DAYS), DAYS[2])
        self.assertEqual(signal_day("2020-09-07", DAYS), DAYS[3])
        self.assertIsNone(signal_day("2020-09-08T16:00:00+09:00", DAYS))
        exam = [date(2020,12,3), date(2020,12,4)]
        self.assertEqual(signal_day("2020-12-03T16:20:00+09:00", exam), exam[0])

    def test_duplicate_and_weekend_gap(self):
        recs = [review("1","2020-09-04T16:00:00+09:00"), review("2","2020-09-07T09:00:00+09:00")]
        rows = build_daily(DAYS, recs, {}, lambda d: d != date(2020,9,6))
        monday = next(r for r in rows if r["symbol"] == "005930" and r["date"] == "2020-09-07")
        self.assertEqual((monday["news_status"],monday["contract_signed"]), ("unknown",None))
        rows = build_daily(DAYS, recs, {}, lambda d: True)
        monday = next(r for r in rows if r["symbol"] == "005930" and r["date"] == "2020-09-07")
        self.assertEqual((monday["contract_signed"],monday["contract_reports"]), (1,1))

    def test_revision_not_backdated(self):
        recs = [review("1","2020-09-04T09:00:00+09:00","2020-09-07T09:00:00+09:00")]
        rows = [r for r in build_daily(DAYS,recs,{},lambda d: True) if r["symbol"]=="005930"]
        self.assertEqual(rows[1]["news_status"], "unknown")
        self.assertEqual((rows[2]["news_status"], rows[2]["contract_signed"]), ("complete",1))
        self.assertEqual(rows[3]["contract_signed"], 0)

    def test_two_contracts_one_report_and_missing_candidate(self):
        row = review("3", "2020-09-07T11:00:00+09:00")
        row["additional_event_ids"] = ["separate_contract"]
        gaps = {"000660": {DAYS[2]}}
        result = build_daily(DAYS, [row], gaps, lambda d: True)
        monday = {r["symbol"]:r for r in result if r["date"] == "2020-09-07"}
        self.assertEqual((monday["005930"]["contract_signed"], monday["005930"]["contract_reports"]), (2,1))
        self.assertEqual((monday["000660"]["news_status"], monday["000660"]["contract_reports"]), ("unknown",None))
        self.assertEqual((monday["035420"]["news_status"], monday["035420"]["contract_signed"]), ("complete",0))
