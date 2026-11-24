import csv
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from src import server_train as srv
from src.execution import Session


def sample_rows():
    # Synthetic trading sessions only; tests exercise chronology, not a holiday calendar.
    d = date(2021, 12, 9)
    dates = []
    while len(dates) < 36:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    rows = []
    for i, day in enumerate(dates):
        end = i + 6
        row = {k: "0.1" for k in srv.FEATURES}
        row.update({k: "0" for k in srv.NEWS})
        row.update(symbol="005930", date=day, next_open_date=dates[i+1] if i+1 < len(dates) else "",
                   label_end_date_5=dates[end] if end < len(dates) else "",
                   target_up_5=str(i % 2) if end < len(dates) else "", news_status="complete")
        rows.append(row)
    return rows


class ServerContractTest(unittest.TestCase):
    def test_ablation_columns_and_sample_are_fixed(self):
        row = sample_rows()[0]
        self.assertEqual(srv.FEATURE_SETS["trend-vol-market"], (*srv.TREND_VOL, "market_ret_20", "relative_strength_20"))
        self.assertEqual(srv.FEATURE_SETS["trend-vol-volume"], (*srv.TREND_VOL, "relative_volume_20"))
        for columns in srv.FEATURE_SETS.values():
            self.assertEqual(len(srv.values(row, columns)), len(columns))
            self.assertEqual(srv.values(row, (*columns, *srv.CONDITIONS['market'])), srv.values(row, (*columns, *srv.NEWS)))

    def test_earnings_pulse_is_only_visible_in_the_third_condition(self):
        row = sample_rows()[0] | {"earnings_release": "1", "earnings_yoy_available": "1", "earnings_op_yoy_pct": "-12.6"}
        for tag in ('market', 'contracts'):
            self.assertEqual(srv.values(row, srv.CONDITIONS[tag])[-3:], (0., 0., 0.))
        self.assertEqual(srv.values(row, srv.CONDITIONS['contracts-earnings'])[-3:], (1., 1., -12.6))
        srv.check_input(row, 0, [r['date'] for r in sample_rows()], 5)
        with self.assertRaises(ValueError):
            srv.check_input(row | {'earnings_yoy_available': '0'}, 0, [r['date'] for r in sample_rows()], 5)

    def test_fixed_scores_replayed_at_three_execution_costs(self):
        dates = [date.fromisoformat(row["date"]) for row in sample_rows()[:4]]
        sessions = [Session(d, dates[i-1] if i else d-timedelta(days=1), 100, 100, "open", True)
                    for i, d in enumerate(dates)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            result = srv.replay_policy(path, sessions, lambda day, holding: .9, srv.threshold, True)
            cheap = json.loads((path / "slippage-0bp/summary.json").read_text())
            expensive = json.loads((path / "slippage-10bp/summary.json").read_text())
            self.assertGreater(cheap["book_return"], result["book_return"])
            self.assertGreater(result["book_return"], expensive["book_return"])
            self.assertEqual((cheap["execution_slippage_bps"], result["execution_slippage_bps"],
                              expensive["execution_slippage_bps"]), (0, 5, 10))

    def test_future_revision_flag_does_not_block_primary_training_or_decisions(self):
        rows = sample_rows()
        for i in (2, 20):
            rows[i].update(news_status="unknown", contract_signed="1", contract_reports="1")
        train, decisions = srv.panel_fold(rows, 2022, 5)
        self.assertIn(rows[2], train)
        self.assertIn(rows[20], decisions)
        self.assertEqual((train, decisions), srv.panel_fold(rows, 2022, 5, price_only=True))
        matched_train, matched_decisions = srv.panel_fold(rows, 2022, 5, complete_news_only=True)
        self.assertEqual(matched_train, [row for row in train if row is not rows[2]])
        self.assertEqual(matched_decisions, [row for row in decisions if row is not rows[20]])
        self.assertEqual(srv.values(rows[2], srv.CONDITIONS["market"])[0], 0.)
        for tag in ("contracts", "contracts-earnings"):
            self.assertEqual(srv.values(rows[2], srv.CONDITIONS[tag])[0], 1.)
        self.assertLess(max(r["label_end_date_5"] for r in train), "2022")
        self.assertEqual(decisions[0]["date"], "2021-12-31")
        self.assertEqual(decisions[-1]["next_open_date"], rows[-1]["date"])
        with self.assertRaises(ValueError):
            srv.panel_fold(rows, 2025, 5)

    def test_default_entry_point_never_fits(self):
        rows = sample_rows()
        rows[2]["news_status"] = "unknown"
        with tempfile.TemporaryDirectory() as directory, patch.object(srv, "PREPARED", Path(directory)):
            with (Path(directory) / "005930.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with (
                patch.object(srv, "supervised", side_effect=AssertionError("unexpected fit")),
                patch.object(srv, "q_scores", side_effect=AssertionError("unexpected fit")),
            ):
                base_args = ["--year", "2022", "--symbol", "005930"]
                for flags, mode, count in (([], "paired-observed", 11),
                                           (["--complete-news-only"], "complete-news-only", 10),
                                           (["--price-only"], "price-only", 11)):
                    with self.subTest(flags=flags), redirect_stdout(io.StringIO()) as out:
                        srv.main(base_args + flags)
                        self.assertIn(mode, out.getvalue())
                        self.assertIn(f"training_labels={count} ", out.getvalue())
                with self.assertRaises(SystemExit) as error, redirect_stderr(io.StringIO()):
                    srv.main(base_args + ["--complete-news-only", "--price-only"])
                self.assertEqual(error.exception.code, 2)

    def test_forward_columns_and_news_validation(self):
        rows = sample_rows()
        days = [r["date"] for r in rows]
        for override in ({"next_open_date": days[0]}, {"label_end_date_5": days[5]},
                         {"contract_signed": "-1"}, {"contract_reports": ""},
                         {"news_status": "unknown", "contract_signed": ""}):
            with self.subTest(override), self.assertRaises(ValueError):
                srv.check_input({**rows[0], **override}, 0, days, 5)

    def test_only_retrospective_exploration_skips_later_uncertain_days(self):
        dates = [date.fromisoformat(r["date"]) for r in sample_rows()[:8]]
        rows = {date.fromisoformat(row["date"]): row for row in sample_rows()[:8]}
        rows[dates[2]]["news_status"] = "unknown"
        sessions = [Session(d, dates[i-1] if i else d-timedelta(days=1), 100+i, 102+i, "open", True)
                    for i, d in enumerate(dates)]
        transitions = srv.exploration(sessions, rows, 11)
        self.assertEqual(transitions, srv.exploration(sessions, rows, 11))
        self.assertEqual(transitions, srv.exploration(sessions, rows, 11, price_only=True))
        self.assertTrue(all(math.isfinite(t.reward) for t in transitions))
        self.assertIn(dates[2], [sessions[t.start].previous_session for t in transitions])
        matched = srv.exploration(sessions, rows, 11, complete_news_only=True)
        self.assertNotIn(dates[2], [sessions[t.start].previous_session for t in matched])
        self.assertEqual(len(matched), len(transitions) - 1)
        self.assertTrue(any(t.steps == 2 and t.end is not None for t in matched))
        self.assertIsNone(transitions[-1].end)
        self.assertEqual(transitions[-1].steps, 2)  # includes the mandatory last open sale

    def test_hysteresis_ties_and_selection(self):
        example = sample_rows()[0]
        self.assertEqual(srv.values(example, (*srv.FEATURES, *srv.NEWS)),
                         srv.values(example, (*srv.FEATURES, *srv.CONDITIONS['market'])))
        self.assertTrue(srv.threshold(.48, True))
        self.assertFalse(srv.threshold(.55-.001, False))
        self.assertIsNone(srv.threshold(None, True))
        self.assertTrue(srv.greedy((1, 1), True))
        self.assertFalse(srv.greedy((2, 1), True))
        ranked = {(name, year, news): {"book_return": .2, "book_max_drawdown": .1}
                  for name in ("forest", "logistic") for year in srv.YEARS for news in srv.CONDITIONS}
        self.assertEqual(srv.select_supervised(ranked), "logistic")
        ranked["logistic", 2022, "market"]["book_max_drawdown"] = .3
        self.assertEqual(srv.select_supervised(ranked), "forest")
