import unittest

from research.chart_pattern_screen import pattern_days


def double_bottom(gap=22, delay=2, first=1000, second=1000, neckline=1040):
    end = 3 + gap
    closes = [1020] * (end + 15)
    closes[3], closes[end], closes[3 + gap // 2] = first, second, neckline
    closes[end + delay] = neckline + 10
    return closes


class ChartPatternScreenTest(unittest.TestCase):
    def test_causal_first_crossing_only_for_w_and_m(self):
        closes = double_bottom()
        closes[30] = 1050  # A second crossing belongs to the same pair.
        for shape, prices in (("W", closes), ("M", [2100 - v for v in closes])):
            expected = {(27, shape)}
            self.assertEqual(pattern_days(prices), expected)
            for n in range(1, len(prices) + 1):
                self.assertEqual(pattern_days(prices[:n]), {p for p in expected if p[0] < n})

    def test_spacing_and_time_boundaries(self):
        for gap in (21, 22, 45, 46):
            for delay in (1, 2, 10, 11):
                closes = double_bottom(gap, delay)
                if delay == 1:
                    closes[3 + gap + 5] = 1050  # Even a second crossing is ineligible.
                expected = {(3 + gap + delay, "W")} if 22 <= gap <= 45 and 2 <= delay <= 10 else set()
                self.assertEqual(pattern_days(closes), expected, (gap, delay))

    def test_similarity_depth_ties_and_consecutive_pivots(self):
        self.assertEqual(pattern_days(double_bottom(first=990, second=1010, neckline=1041)), {(27,"W")})
        self.assertFalse(pattern_days(double_bottom(first=990, second=1011, neckline=1042)))
        self.assertEqual(pattern_days(double_bottom(neckline=1030)), {(27,"W")})
        self.assertFalse(pattern_days(double_bottom(neckline=1029)))
        for i in (4, 18):
            closes = double_bottom()
            closes[i] = 1000  # Equal neighbour, or an intervening local low.
            self.assertFalse(pattern_days(closes))

    def test_audit_counts_candidates_without_turning_them_into_signals(self):
        closes = double_bottom(delay=1)
        audit = {}
        self.assertFalse(pattern_days(closes, first_report=6, audit=audit))
        self.assertEqual([audit["W"][column] for column in
                          ("pivots", "consecutive_pairs", "gap_22_45", "similar_2pct",
                           "neck_3pct", "confirmed_days")], [1, 1, 1, 1, 1, 0])
