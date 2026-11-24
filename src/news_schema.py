"""Fields shared by the three matched trading experiments."""
from math import isfinite

CONTRACT = ("contract_signed", "contract_terminated", "contract_reports")
EARNINGS = ("earnings_release", "earnings_yoy_available", "earnings_op_yoy_pct")
NEWS = (*CONTRACT, *EARNINGS)
CONDITIONS = {
    "market": (None,) * len(NEWS),
    "contracts": CONTRACT + (None,) * len(EARNINGS),
    "contracts-earnings": NEWS,
}


def earnings_values(row):
    release, available = (int(row[key]) for key in EARNINGS[:2])
    change = float(row[EARNINGS[2]])
    if not (0 <= available <= release <= 1 and isfinite(change)) or (not available and change != 0):
        raise ValueError(f"Invalid earnings pulse: {row}")
    return dict(zip(EARNINGS, (release, available, change)))
