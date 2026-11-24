"""Single-stock KOSPI cash account; costs and rights are applied to unadjusted prices."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

D = Decimal
TAX = (
    (date(2024, 12, 27), D(".0015")), (date(2023, 12, 27), D(".0018")),
    (date(2022, 12, 28), D(".0020")), (date(2020, 12, 29), D(".0023")),
    (date(2020, 1, 1), D(".0025")),
)


def number(value):
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("Expected a finite amount")
    return result


def kospi_sell_tax(day):
    if not 2020 <= day.year <= 2024:
        raise ValueError(f"No verified sell-tax rate for {day}")
    return next(rate for start, rate in TAX if start <= day)


@dataclass(frozen=True)
class Dividend:
    per_share: Decimal | None
    pay_day: date | None
    known_day: date | None


@dataclass(frozen=True)
class Session:
    day: date
    previous_session: date
    open_price: Decimal | None
    close_price: Decimal | None
    status: str
    actions_verified: bool
    split_ratio: Decimal = D(1)
    dividends: tuple[Dividend, ...] = ()
    net_settlement_per_share: Decimal | None = None


@dataclass(frozen=True)
class Entry:
    day: date
    decision_day: date
    prior_shares: int
    target: bool | None
    terminal: bool
    order: str
    reference_open: Decimal | None
    filled_qty: int
    fill_price: Decimal | None
    fee: Decimal
    sell_tax: Decimal
    slippage_cost: Decimal
    shares: int
    cash: Decimal
    mark: Decimal | None
    receivable: Decimal
    paid_dividend: Decimal
    pending_rights: int
    booked_equity: Decimal
    equity: Decimal | None
    locked_days: int


def sma20_target(closes):
    window = closes[-20:]
    if len(window) < 20 or None in window:
        return None
    prices = [number(x) for x in window]
    if min(prices) <= 0:
        raise ValueError("Nonpositive close")
    gap = 20 * prices[-1] - sum(prices)
    return gap > 0 if gap else None


def run_account(sessions, targets, *, starting_cash=10_000_000, fee_rate=".00015", slippage=".0005"):
    """Targets keyed by decision date. Missing or None retains the current position.

    Dividend amounts can be incremental (a later correction to an initial announcement).
    booked_equity is the amount verifiable as of close. equity stays unknown while
    any entitled dividend amount awaits confirmation. Rights survive selling the shares.
    """
    cash, fee_rate, slippage = map(number, (starting_cash, fee_rate, slippage))
    if cash <= 0 or not 0 <= fee_rate < 1 or not 0 <= slippage < 1 or not sessions:
        raise ValueError("Invalid funding, costs or empty calendar")
    rows, rights = [], []
    shares = locked_days = 0
    mark = None
    settled = False
    for i, bar in enumerate(sessions):
        day, previous = bar.day, bar.previous_session
        if previous >= day or (i and previous != sessions[i-1].day):
            raise ValueError(f"Broken trading calendar at {day}")
        rate = kospi_sell_tax(day)
        target = targets(previous, bool(shares)) if callable(targets) else targets.get(previous)
        if target is not None and type(target) is not bool:
            raise ValueError("Target must be True, False, or None")
        if not bar.actions_verified or bar.status not in ("open", "no_open", "halted", "settled"):
            raise ValueError(f"Unknown rights or trading status on {day}")
        if settled and bar.status != "settled":
            raise ValueError("Trading after settlement")
        op = number(bar.open_price) if bar.open_price is not None else None
        close = number(bar.close_price) if bar.close_price is not None else None
        if (bar.status == "open") != (op is not None) or (op is not None and op <= 0):
            raise ValueError("Invalid executable open")
        if (bar.status in ("open", "no_open")) != (close is not None) or (close is not None and close <= 0):
            raise ValueError("Invalid close")
        if (bar.status == "settled") != (bar.net_settlement_per_share is not None):
            raise ValueError("Missing or unexpected net settlement")
        prior = shares
        for event in bar.dividends:
            if event.per_share is None:
                if event.known_day is not None or event.pay_day is not None:
                    raise ValueError("Partially specified unknown dividend")
                if shares:
                    rights.append((None, None, D(0)))
            else:
                amount = number(event.per_share)
                if amount < 0 or not event.pay_day or not event.known_day or event.pay_day < max(day, event.known_day):
                    raise ValueError("Invalid dividend timeline")
                if shares:
                    rights.append((max(day, event.known_day), event.pay_day, shares * amount))
        ratio = number(bar.split_ratio)
        if ratio <= 0 or shares * ratio != (shares * ratio).to_integral_value():
            raise ValueError("Unverified fractional-share settlement")
        shares = int(shares * ratio)
        if mark is not None:
            mark /= ratio
        paid = sum((v for _, pay, v in rights if pay and pay < day), D(0))
        cash += paid
        rights = [r for r in rights if r[1] is None or r[1] >= day]
        terminal = i == len(sessions)-1
        desired = False if terminal else target
        order, qty, fill = "hold", 0, None
        fee = tax = slip = D(0)
        if bar.status == "settled":
            value = number(bar.net_settlement_per_share)
            if value < 0:
                raise ValueError("Negative settlement")
            cash += shares * value
            shares, mark, order, settled = 0, None, "settled", True
        elif desired is not None and desired != bool(shares):
            side = "buy" if desired else "sell"
            if op is None:
                order = "expired_" + side
            else:
                price = op * (1 + slippage if desired else 1 - slippage)
                qty = int(cash // (price * (1 + fee_rate))) if desired else shares
                if qty:
                    fill, order = price, side
                    gross = qty * price
                    fee, tax, slip = gross * fee_rate, D(0) if desired else gross * rate, qty * abs(price-op)
                    cash += -gross-fee if desired else gross-fee-tax
                    shares += qty if desired else -qty
                else:
                    order = "insufficient_cash"
        if close is not None:
            mark = close
        if shares and bar.status == "halted":
            locked_days += 1
        today = sum((v for _, pay, v in rights if pay == day), D(0))
        cash += today
        rights = [r for r in rights if r[1] is None or r[1] > day]
        receivable = sum((v for known, _, v in rights if known and known <= day), D(0))
        pending = sum(known is None or known > day for known, _, _ in rights)
        booked = cash + (shares * mark if shares else 0) + receivable
        rows.append(Entry(day, previous, prior, target, terminal, order, op, qty, fill, fee, tax,
                          slip, shares, cash, mark, receivable, paid+today, pending, booked,
                          None if pending else booked, locked_days))
    return rows
