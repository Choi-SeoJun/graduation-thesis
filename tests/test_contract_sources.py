import csv
import unittest
from datetime import date

from src.bigkinds_contract_counts import hynix_rows, naver_rows, samsung_rows
from src.contract_sources import RESEARCH, build_source_daily, event_records, source_contracts
from src.news_events import signal_day


class TestContractSources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = source_contracts()
        cls.contract = {(c.symbol, c.event_id): c for c in cls.cases}
        with (RESEARCH / "contract_source_daily.csv").open(encoding="utf-8", newline="") as f:
            cls.published = list(csv.DictReader(f))
        cls.calendar = sorted({date.fromisoformat(r["date"]) for r in cls.published})
        cls.daily = build_source_daily(cls.calendar, cls.cases)

    def test_public_tables_reproduce_with_no_private_files(self):
        with (RESEARCH / "contract_source_events.psv").open(encoding="utf-8", newline="") as f:
            self.assertEqual(list(csv.DictReader(f, delimiter="|")), list(event_records(self.cases)))
        self.assertEqual(len(self.published), 3693)
        seen = set()
        for row in self.published:
            key = row["symbol"], row["date"]
            self.assertNotIn(key, seen)
            seen.add(key)
            fields = {k: int(v) for k, v in row.items() if k not in ("date", "symbol")}
            self.assertEqual(self.daily[key], fields)
        self.assertEqual(seen, set(self.daily))

    def test_report_witnesses_match_independent_audit_dates(self):
        for symbol, audit_rows in (("005930", samsung_rows), ("000660", hynix_rows), ("035420", naver_rows)):
            witnesses = {c.event_id for c in self.cases if c.symbol == symbol and c.bigkinds}
            audited = set()
            for key, matched, other_close, etoday_close in audit_rows():
                audited.add(key)
                case = self.contract[symbol, key]
                self.assertEqual(signal_day(case.bigkinds.available_at, self.calendar).isoformat(), other_close)
                if matched:
                    self.assertEqual(signal_day(case.etoday.available_at, self.calendar).isoformat(), etoday_close)
                else:
                    self.assertIsNone(case.etoday)
            self.assertEqual(witnesses, audited)

    def test_etoday_only_cases_survive_and_amendments_are_removed(self):
        etoday_only = {(c.symbol, c.event_id) for c in self.cases if c.etoday and not c.bigkinds}
        self.assertEqual(etoday_only, {("005930", "E2005442"), ("035420", "E1853817"), ("035420", "E1962760")})
        self.assertNotIn(("005930", "E2171363"), self.contract)
        self.assertNotIn(("005930", "K20230823000139"), self.contract)
        totals = {s: (sum(bool(c.etoday) for c in self.cases if c.symbol == s),
                      sum(c.symbol == s for c in self.cases)) for s in ("005930", "000660", "035420")}
        self.assertEqual(totals, {"005930": (118, 227), "000660": (41, 85), "035420": (12, 28)})

    def test_union_moves_signal_without_repeating_it(self):
        # Holiday coverage on May 5 vs EToday's Friday May 8 after-close version.
        early = self.daily["035420", "2020-05-06"]
        late = self.daily["035420", "2020-05-11"]
        self.assertEqual((early["contract_signed"], early["union_contract_signed"]), (0, 1))
        self.assertEqual((late["contract_signed"], late["union_contract_signed"]), (1, 0))

    def test_joint_awards_share_one_announcement_and_termination_is_separate(self):
        grouped = [c for c in self.cases if c.report_id == "BK035420-20211112-REGION"]
        self.assertEqual(len(grouped), 8)
        close = signal_day(grouped[0].bigkinds.available_at, self.calendar)
        day = self.daily["035420", close.isoformat()]
        self.assertEqual(day["union_contract_signed"], 8)
        self.assertEqual(day["union_contract_reports"], 1 + day["union_contract_terminated"])
        self.assertEqual(sum(r["union_contract_terminated"] for (s, _), r in self.daily.items()
                             if s == "035420"), 1)

    def test_partial_calendar_does_not_move_old_news_into_the_first_row(self):
        with self.assertRaises(ValueError):
            build_source_daily(self.calendar[1:])
