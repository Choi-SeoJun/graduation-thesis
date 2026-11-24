import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from statistics import stdev

from src.execution import Dividend, Session, kospi_sell_tax, run_account, sma20_target
from src.market_data import make_rows
from src.prepare import trainable


def sample_prices():
    days = [date(2019,11,1) + timedelta(days=i) for i in range(105)]
    dates = [d.isoformat() for d in days if d.weekday() < 5]
    def bars(base):
        return {d: dict(open=base+i, high=base+i+1, low=base+i-1, close=base+i, volume=100+i)
                for i,d in enumerate(dates)}
    return dates, bars(2000), bars(100)


class FeaturesTest(unittest.TestCase):
    def test_inputs_and_horizon(self):
        days, market, stock = sample_prices()
        i = days.index("2020-01-10")
        rows = {r["date"]:r for r in make_rows("test",stock,market)}
        r = rows[days[i]]
        self.assertEqual((r["next_open_date"],r["label_end_date_5"]),(days[i+1],days[i+6]))
        self.assertAlmostEqual(r["ret_5"],(100+i)/(100+i-5)-1)
        self.assertAlmostEqual(r["ma_gap_20"],(100+i)/(100+i-9.5)-1)
        self.assertAlmostEqual(r["relative_volume_20"],(100+i)/(100+i-10.5))
        self.assertAlmostEqual(r["volatility_20"],stdev(1/(100+j-1) for j in range(i-19,i+1)))
        self.assertIsNone(rows[days[-6]]["target_up_5"])

    def test_split_and_missing_session(self):
        days, market, stock = sample_prices()
        i = days.index("2020-01-10")
        for d in days[i+3:]:
            for field in ("open","high","low","close"):
                stock[d][field] /= 2
            stock[d]["volume"] *= 2
        rows = {r["date"]:r for r in make_rows("test",stock,market,{days[i+3]:2})}
        self.assertEqual(rows[days[i]]["target_up_5"],1)
        del stock[days[i+1]]
        rows = {r["date"]:r for r in make_rows("test",stock,market)}
        self.assertEqual(rows[days[i]]["next_open_date"],days[i+1])
        self.assertIsNone(rows[days[i]]["target_up_5"])

    def test_label_purging(self):
        _, market, stock = sample_prices()
        row = list(make_rows("test",stock,market))[6] | {"news_status":"unknown"}
        self.assertTrue(trainable(row,2022,5))
        self.assertFalse(trainable(row,2022,5,True))
        self.assertFalse(trainable(row | {"label_end_date_5":"2022-01-03"},2022,5))


P,A,B,C,E = map(date.fromisoformat,("2024-01-31","2024-02-01","2024-02-02","2024-02-05","2024-02-06"))
BARS = [Session(A,P,100,110,"open",True), Session(B,A,120,115,"open",True),
        Session(C,B,120,119,"open",True), Session(E,C,130,125,"open",True)]


class ExecutionTest(unittest.TestCase):
    def test_hand_computed_orders(self):
        ledger = run_account(BARS,{P:True,A:True,B:False,C:True},starting_cash=10201,fee_rate=".01",slippage=".01")
        buy,hold,sell,end = ledger
        self.assertEqual([e.order for e in ledger],["buy","hold","sell","hold"])
        self.assertEqual((buy.shares,buy.cash,buy.fee,buy.slippage_cost),(100,0,101,100))
        self.assertEqual(hold.equity,11500)
        self.assertEqual((sell.fill_price,sell.sell_tax,end.cash),(D("118.8"),D("21.384"),D("11739.816")))

    def test_late_dividend_and_split(self):
        bars = list(BARS)
        bars[1] = replace(bars[1],split_ratio=2,dividends=(Dividend(D(7),E,C),))
        rows = run_account(bars,{P:True,A:False},starting_cash=100,fee_rate=0,slippage=0)
        self.assertEqual((rows[1].pending_rights,rows[1].equity,rows[1].filled_qty),(1,None,2))
        self.assertEqual((rows[2].receivable,rows[3].paid_dividend),(7,7))
        self.assertEqual(rows[3].cash,D("246.568"))

    def test_expired_order_and_first_close(self):
        bars = list(BARS)
        bars[1] = replace(bars[1],status="halted",open_price=None,close_price=None)
        rows = run_account(bars,{P:True,A:False},starting_cash=100,fee_rate=0,slippage=0)
        self.assertEqual([r.order for r in rows],["buy","expired_sell","hold","sell"])
        self.assertEqual(rows[1].equity,110)
        self.assertEqual(run_account(BARS,{A:True},starting_cash=240,fee_rate=0,slippage=0)[0].shares,0)

    def test_invalid_inputs(self):
        for bars in ([replace(BARS[0],actions_verified=False)],[BARS[0],BARS[2]],
                     [replace(BARS[0],open_price=None)]):
            with self.assertRaises(ValueError):
                run_account(bars,{})
        self.assertEqual(kospi_sell_tax(date(2024,12,26)),D(".0018"))
        self.assertEqual(kospi_sell_tax(date(2024,12,27)),D(".0015"))
        self.assertIsNone(sma20_target([10]*20))
        self.assertTrue(sma20_target([10]*19+[11]))
