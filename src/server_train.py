"""Prepare/check development folds; only an explicit --train fits any models."""
import argparse
import csv
import math
import random
from dataclasses import asdict, dataclass
from datetime import date
from statistics import mean, median

from src.execution import run_account
from src.fetch_market import ROOT, SYMBOLS, write_csv, write_json
from src.market_data import FEATURES, HORIZONS

YEARS = (2022, 2023, 2024)
SEEDS = (11, 23, 37, 53, 71)
NEWS = ("contract_signed", "contract_terminated", "contract_reports")
PREPARED = ROOT / "data/local/prepared"
RESULTS = ROOT / "data/local/server-runs"
GAMMA = 0.95
TREND = ("ret_5", "ma_gap_20")
TREND_VOL = (*TREND, "volatility_20")
FEATURE_SETS = {
    "trend": TREND,
    "trend-vol": TREND_VOL,
    "trend-vol-market": (*TREND_VOL, "market_ret_20", "relative_strength_20"),
    "trend-vol-volume": (*TREND_VOL, "relative_volume_20"),
    "all": FEATURES,
}


def values(row, columns):
    if any(row[c] in (None, "") for c in columns if c is not None):
        return None
    # A masked news column is a constant 0 in the paired control. Equal dimension
    # keeps random feature subsampling identical if every observed news count is 0.
    numbers = tuple(0.0 if c is None else float(row[c]) for c in columns)
    if not all(math.isfinite(v) for v in numbers):
        raise ValueError(f"Nonfinite input {row['date']}")
    return numbers


def observable(row, price_only=False):
    if price_only:
        return values(row, FEATURES) is not None
    return row["news_status"] == "complete" and values(row, (*FEATURES, *NEWS)) is not None


def read_panel(symbol, horizon):
    if symbol not in SYMBOLS or horizon not in HORIZONS:
        raise ValueError("Unknown stock or horizon")
    with (PREPARED / f"{symbol}.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"Empty panel for {symbol}")
    dates = [row["date"] for row in rows]
    if dates != sorted(set(dates)) or dates[0] < "2020-01-01" or dates[-1] > "2024-12-31":
        raise ValueError("Dates must be unique, ordered and in development period")
    for i, row in enumerate(rows):
        if row["symbol"] != symbol or row["news_status"] not in ("complete", "unknown"):
            raise ValueError("Unexpected stock or news coverage code")
        check_input(row, i, dates, horizon)
    return rows


def check_input(row, i, dates, horizon):
    expected_next = dates[i+1] if i+1 < len(dates) else ""
    expected_end = dates[i+1+horizon] if i+1+horizon < len(dates) else ""
    if row["next_open_date"] != expected_next or row[f"label_end_date_{horizon}"] != expected_end:
        raise ValueError(f"Misaligned forward-date columns on {row['date']}")
    target = row[f"target_up_{horizon}"]
    if target not in ("0", "1", "") or (target and not expected_end):
        raise ValueError("Unrealized or nonbinary target")
    if row["news_status"] == "complete":
        v = values(row, NEWS)
        if v is None or any(n < 0 or n != int(n) for n in v):
            raise ValueError("Complete news coverage requires integer counts")


def panel_fold(rows, year, horizon, price_only=False):
    if year not in YEARS:
        raise ValueError("Development validation years: 2022–2024")
    boundary = f"{year}-01-01"
    future = f"{year+1}-01-01"
    past = [r for r in rows if r["date"] < boundary]
    valid = [r for r in rows if boundary <= r["date"] < future]
    if not past or not valid:
        raise ValueError("Both expanding history and validation sessions are required")
    train = [r for r in past if observable(r, price_only) and r[f"target_up_{horizon}"] != ""
             and r[f"label_end_date_{horizon}"] < boundary]
    # The prior year's last close can decide the first execution in the validation year.
    decisions = [r for r in (past[-1:] + valid[:-1]) if observable(r, price_only)]
    return train, decisions


def supervised(train, decisions, name, columns, horizon):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    target = [int(row[f"target_up_{horizon}"]) for row in train]
    if len(set(target)) < 2:
        raise ValueError("A supervised training fold needs both target classes")
    if name == "logistic":
        model = make_pipeline(StandardScaler(), LogisticRegression(C=1, max_iter=1000, random_state=11))
    elif name == "forest":
        model = RandomForestClassifier(n_estimators=300, max_depth=4, min_samples_leaf=20, random_state=11, n_jobs=-1)
    else:
        raise ValueError(name)
    model.fit([values(r, columns) for r in train], target)
    probabilities = model.predict_proba([values(r, columns) for r in decisions])[:, 1]
    return {date.fromisoformat(r["date"]): float(p) for r, p in zip(decisions, probabilities)}


def threshold(probability, holding):
    if probability is None:
        return None
    if probability >= .55:
        return True
    if probability <= .45:
        return False
    return holding


@dataclass
class Transition:
    start: int
    holding: bool
    action: bool
    reward: float
    end: int | None
    end_holding: bool
    steps: int


def exploration(sessions, by_day, seed, price_only=False):
    """One continuous historical account from cash. Collapse mandatory holds in gaps."""
    rng = random.Random(seed)
    choices = {}
    for bar in sessions[:-1]:
        decision = bar.previous_session
        row = by_day.get(decision)
        if row is not None and observable(row, price_only):
            choices[decision] = bool(rng.getrandbits(1))
    ledger = run_account(sessions, choices)
    before = [10_000_000.] + [float(e.booked_equity) for e in ledger[:-1]]
    rewards = [math.log(float(e.booked_equity) / v) for e, v in zip(ledger, before)]
    starts = [i for i, e in enumerate(ledger) if e.decision_day in choices and not e.terminal]
    transitions = []
    for k, i in enumerate(starts):
        end = starts[k+1] if k+1 < len(starts) else None
        stop = len(ledger) if end is None else end
        reward = sum(GAMMA ** j * rewards[i+j] for j in range(stop-i))
        transitions.append(Transition(i, bool(ledger[i].prior_shares), choices[ledger[i].decision_day],
                                      reward, end, bool(ledger[stop-1].shares), stop-i))
    return transitions


def q_scores(sessions, by_day, transitions, decisions, columns, seed):
    import numpy as np
    from sklearn.ensemble import ExtraTreesRegressor

    groups = [[t for t in transitions if t.action == bool(action)] for action in (0, 1)]
    if not all(groups):
        raise ValueError("Training history needs both actions for fitted Q iteration")

    def state(i, holding):
        d = sessions[i].previous_session
        return (*values(by_day[d], columns), int(holding))

    xs = [[state(t.start, t.holding) for t in group] for group in groups]
    ys = [np.array([t.reward for t in group]) for group in groups]
    ends = [[i for i, t in enumerate(group) if t.end is not None] for group in groups]
    next_x = [[state(group[i].end, group[i].end_holding) for i in idx] for group, idx in zip(groups, ends)]
    estimators = []
    for iteration in range(15):
        fitted = []
        for action, group in enumerate(groups):
            y = ys[action].copy()
            if iteration and ends[action]:
                future = np.maximum(estimators[0].predict(next_x[action]), estimators[1].predict(next_x[action]))
                discounts = [GAMMA ** group[i].steps for i in ends[action]]
                y[ends[action]] += future * discounts
            model = ExtraTreesRegressor(n_estimators=100, max_depth=4, random_state=seed+action, n_jobs=-1)
            model.fit(xs[action], y)
            fitted.append(model)
        estimators = fitted
    dates = [date.fromisoformat(row["date"]) for row in decisions]
    result = {}
    for holding in (False, True):
        states = [(*values(row, columns), int(holding)) for row in decisions]
        q0, q1 = (model.predict(states) for model in estimators)
        for d, v0, v1 in zip(dates, q0, q1):
            result[(d, holding)] = (float(v0), float(v1))
    return result


def greedy(q, holding):
    if q is None:
        return None
    if q[0] == q[1]:
        return holding
    return q[1] > q[0]


def record_result(directory, ledger, scores, slippage_bps):
    """Keep both decision evidence and the orders that the common engine actually filled."""
    rows = []
    peak = 10_000_000.
    max_drawdown = 0.
    for e in ledger:
        record = asdict(e)
        value = scores(e.decision_day, bool(e.prior_shares))
        record["score"], record["q_cash"], record["q_long"] = ("", "", "")
        if isinstance(value, tuple):
            record["q_cash"], record["q_long"] = value
        elif value is not None:
            record["score"] = value
        rows.append(record)
        wealth = float(e.booked_equity)
        peak = max(peak, wealth)
        max_drawdown = max(max_drawdown, 1-wealth/peak)
    write_csv(directory / "ledger.csv", list(rows[0]), rows)
    result = {"execution_slippage_bps": slippage_bps, "fqi_exploration_slippage_bps": 5,
              "book_return": float(ledger[-1].booked_equity)/10_000_000-1, "book_max_drawdown": max_drawdown,
              "trades": sum(e.filled_qty > 0 for e in ledger), "pending_end": ledger[-1].pending_rights,
              "unvalued_days": sum(e.equity is None for e in ledger)}
    write_json(directory / "summary.json", result)
    return result


def replay_policy(directory, sessions, scores, policy, sensitivity):
    """Replay one set of already fitted scores. Only execution prices vary.
    Realized holdings may differ across scenarios; each account decides on its own holdings.
    """
    def target(day, holding):
        return policy(scores(day, holding), holding)

    base = run_account(sessions, target, slippage=".0005")
    summary = record_result(directory, base, scores, 5)
    if sensitivity:
        for bps, slippage in ((0, "0"), (10, ".001")):
            alternate = run_account(sessions, target, slippage=slippage)
            record_result(directory / f"slippage-{bps}bp", alternate, scores, bps)
    return summary


def select_supervised(results, news_conditions=(False, True)):
    """Pick within stock using both news conditions at each year's paired validation."""
    scores = {}
    for name in ("logistic", "forest"):
        by_year = [mean(results[(name, y, n)]["book_return"] for n in news_conditions) for y in YEARS]
        worst_drawdown = max(results[(name, y, n)]["book_max_drawdown"] for y in YEARS for n in news_conditions)
        scores[name] = (median(by_year), -worst_drawdown, name == "logistic")
    return max(scores, key=scores.get)


def run_stock(symbol, years, horizon, method, train_models, price_only, feature_set="all", sensitivity=False):
    rows = read_panel(symbol, horizon)
    by_day = {date.fromisoformat(r["date"]): r for r in rows}
    results = {}
    conditions = (False,) if price_only else (False, True)
    mode = "price-only" if price_only else "paired"
    out = RESULTS / mode / f"h{horizon}" / symbol
    if feature_set != "all":
        out = out / "features" / feature_set
    market_columns = FEATURE_SETS[feature_set]
    if train_models:
        from src.rights import load_sessions
        sessions = load_sessions(symbol)
        if [s.day for s in sessions] != list(by_day):
            raise ValueError("Account and prepared stock do not share a session calendar")
    for y in years:
        train, decisions = panel_fold(rows, y, horizon, price_only)
        coverage = "not used" if price_only else sum(
            bool(int(r["contract_signed"]) or int(r["contract_terminated"])) for r in train)
        print(f"{symbol} {y} {mode} inputs={feature_set} execution_bps={'0,5,10' if sensitivity else '5'} "
              f"training_labels={len(train)} eligible_validation_decisions={len(decisions)} "
              f"training_days_with_contracts={coverage}")
        if not train_models:
            continue
        if not train or not decisions:
            raise ValueError(f"Insufficient {mode} inputs in {y}. Check coverage before fitting.")
        history = [s for s in sessions if s.day.year < y]
        validation = [s for s in sessions if s.day.year == y]
        for n in conditions:
            news_columns = NEWS if n else (None,) * len(NEWS)
            columns = market_columns if price_only else (*market_columns, *news_columns)
            tag = "with-news" if n else "market"
            if method in ("both", "supervised"):
                for name in ("logistic", "forest"):
                    probabilities = supervised(train, decisions, name, columns, horizon)
                    results[(name, y, n)] = replay_policy(out / str(y) / tag / name, validation,
                                                         lambda d, held: probabilities.get(d), threshold, sensitivity)
            if method in ("both", "fqi"):
                for seed in SEEDS:
                    transitions = exploration(history, by_day, seed, price_only)
                    qs = q_scores(history, by_day, transitions, decisions, columns, seed)
                    replay_policy(out / str(y) / tag / f"fqi-{seed}", validation,
                                  lambda d, held: qs.get((d, held)), greedy, sensitivity)
    if train_models and set(years) == set(YEARS) and method in ("supervised", "both"):
        write_json(out / "selected.json",
                   {"stock": symbol, "horizon": horizon, "mode": mode, "features": feature_set,
                    "selection_execution_bps": 5, "metric": "median annual booked net return",
                    "supervised": select_supervised(results, conditions)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=SYMBOLS, help="default: all three, fitted separately")
    parser.add_argument("--year", type=int, choices=YEARS, help="default: all expanding development folds")
    parser.add_argument("--horizon", type=int, choices=HORIZONS, default=5, help="supervised horizon only")
    parser.add_argument("--method", choices=("supervised", "fqi", "both"), default="both")
    parser.add_argument("--features", choices=FEATURE_SETS, default="all",
                        help="input ablation; each variant uses the same eligible sessions")
    parser.add_argument("--slippage-sensitivity", action="store_true",
                        help="replay fixed fitted scores at 0/5/10 bp per fill; FQI training cost stays at 5 bp")
    parser.add_argument("--price-only", action="store_true", help="independent comparison on all usable price days")
    parser.add_argument("--train", action="store_true", help="opt in to fitting on the training server")
    args = parser.parse_args(argv)
    if args.train and args.horizon != 5 and args.method != "supervised":
        parser.error("Only supervised sensitivity checks use --horizon 1 or 10")
    for symbol in (args.symbol,) if args.symbol else SYMBOLS:
        run_stock(symbol, (args.year,) if args.year else YEARS, args.horizon, args.method, args.train, args.price_only,
                  args.features, args.slippage_sensitivity)


if __name__ == "__main__":
    main()
