"""Build the development-only descriptive report. Matplotlib is needed only for the figure."""
import csv
import hashlib
import json
from datetime import date
from decimal import Decimal
from statistics import median

from src.fetch_market import ROOT, MARKET, SYMBOLS, atomic_text, write_json
from src.market_data import FEATURES
from src.prepare import PREPARED, ETODAY_CONTEXT, CONTRACT_DAILY
from src.baselines import OUTPUT

NAMES = {"005930":"삼성전자","000660":"SK하이닉스","035420":"NAVER"}


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def plot_monthly(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8.4,4.0), layout="constrained")
    for code, name, color in zip(SYMBOLS, ("Samsung Electronics","SK hynix","NAVER"), ("#12657a","#ae4e20","#3758a3")):
        rows = read(MARKET/"exchange"/f"{code}.csv")
        base = next(float(r["close"]) for r in rows if r["date"] == "2019-12-30")
        series = {r["date"][:7]:r for r in rows if r["date"] >= "2019-12-30"}
        ax.plot([date.fromisoformat(r["date"]) for r in series.values()],
                [100*float(r["close"])/base for r in series.values()], label=name, lw=2, color=color)
    ax.axhline(100,color="#777777",ls=":",lw=.8)
    ax.set_title("Month-end share prices, 2020–2024",loc="left",fontsize=14)
    ax.set_ylabel("Index (30 Dec 2019 = 100)")
    ticks = [date(y,1,1) for y in range(2020,2025)] + [date(2024,12,30)]
    ax.set_xticks(ticks, [str(y) for y in range(2020,2025)] + ["Dec 2024"])
    ax.set_xlim(date(2019,11,1), date(2024,12,31))
    ax.spines[["top","right"]].set_visible(False)
    ax.grid(axis="y",alpha=.2)
    ax.legend(loc="upper left",frameon=False,ncols=3,fontsize=9)
    fig.savefig(out,dpi=170)
    plt.close(fig)


def main():
    docs = ROOT/"docs"
    docs.mkdir(exist_ok=True)
    lines = ["# 학습 전 데이터 점검 결과", "",
             "개발 기간은 2020년 첫 거래일부터 2024년 마지막 거래일까지다. 아래 표는 데이터와 기준 전략을 점검한 결과다. 지도학습·강화학습 성과는 서버 실험에서 산출한다.", "",
             "## 종목별 입력", "",
             "| 종목 | 거래일 | 시세 입력 6개 완비 | 5일 목표 확정 | 상승 목표 비율 | 일간 변동성의 중앙값¹ |",
             "|---|---:|---:|---:|---:|---:|"]
    panels = {s:read(PREPARED/f"{s}.csv") for s in SYMBOLS}
    for s, rows in panels.items():
        targets = [int(r["target_up_5"]) for r in rows if r["target_up_5"] != ""]
        ready = sum(all(r[c] != "" for c in FEATURES) for r in rows)
        vol = 100*median(float(r["volatility_20"]) for r in rows if r["volatility_20"])
        lines.append(f"| {NAMES[s]} | {len(rows):,} | {ready:,} | {len(targets):,} | {100*sum(targets)/len(targets):.1f}% | {vol:.2f}% |")
    lines.extend(["", "¹ 거래일마다 직전 20개 일간 종가수익률의 표본표준편차를 구하고 그 값들의 중앙값을 냈다. 상승 목표는 오늘 판단 후 다음 시가에 매수해 5거래일 뒤 시가가 더 높았던 경우다. 마지막 6거래일은 목표 만기가 2024년을 넘어간다.", "",
                  "![2019년 말 종가를 100으로 환산한 종목별 월말 가격](monthly-prices.png)", "",
                  "각 선은 2019년 말 종가를 100으로 둔 월말 가격이다. 배당·거래비용을 포함한 계좌 수익률은 별도로 계산한다.", "",
                  "## 연도별 시세 및 뉴스", "",
                  "주 실험은 모든 개발 거래일에서 판단한다. 계약은 그 시각까지 확인할 수 있는 판본에서만 세고, 같은 계약의 반복 보도는 한 사건으로 묶는다. ‘자료 공백’은 현재 남은 기사에서 원래 시점의 내용을 완전히 복원할 수 없는 날이다. 이 표시는 사후 민감도 분석에서만 사용한다.", "",
                  "| 종목 | 연도 | 거래일 | 이투데이 자료 공백일 | 이투데이 체결 / 해지 / 발표 | 이투데이+빅카인즈 체결 / 해지 / 발표 | 계약 입력이 달라진 날 | 이투데이 실적 발표 |",
                  "|---|---:|---:|---:|---|---|---:|---:|"])
    for s, rows in panels.items():
        for y in range(2020,2025):
            yr = [r for r in rows if r["date"].startswith(str(y))]
            complete = [r for r in yr if r["news_status"] == "complete"]
            columns = ("contract_signed", "contract_terminated", "contract_reports")
            counts = [" / ".join(str(sum(int(r[prefix + k]) for r in yr)) for k in columns)
                      for prefix in ("", "union_")]
            changed = sum(any(r[k] != r["union_" + k] for k in columns) for r in yr)
            earnings = sum(int(r['earnings_release']) for r in yr)
            lines.append(f"| {NAMES[s]} | {y} | {len(yr)} | {len(yr)-len(complete)} | "
                         f"{counts[0]} | {counts[1]} | {changed} | {earnings} |")
    archive = json.loads((ROOT / 'research/contract_audit.json').read_text(encoding='utf-8'))
    counts = archive['counts']
    raw = archive['unique_counts_before_close_alignment']
    lines.extend(["", "체결에는 계약 당사자로 최종 선정된 경우를 포함한다. 같은 발표로 여러 계약을 알렸다면 발표 수는 한 번만 센다. 두 자료원의 계약 입력이 달라진 날은 위 세 열 중 하나라도 다른 날이다. 선행 보도로 날짜가 이동한 사건은 앞당겨진 날짜와 원래 날짜의 입력을 모두 바꿀 수 있다. 모델이 실제로 다른 주문을 낸 날짜는 서버 실험 결과에서 따로 센다. [거래일별 두 조건의 입력](../research/contract_source_daily.csv) · [사건별 근거](../research/contract_source_events.psv)",
                  "", f"아래는 비교를 확장하기 전의 이투데이 자료 수집 이력이다. 회사명이 제목에 등장하는 이투데이 기사 {counts['articles']:,}건 중 {counts['nonempty_bodies']:,}건의 보존 본문에서 후보를 찾았다. 위의 실험 입력에는 공급사명·공시 모음으로 역검색한 이투데이 보도와 공시 원문 재판정도 반영했다. [초기 판정 방법과 사례](../research/contract_method.md)", "",
                  "| 종목 | 본문에서 확인한 고유 체결 | 고유 해지 | 분기 실적 발표 | 최초 기사에서 전년 동기 증감률 확인 |",
                  "|---|---:|---:|---:|---:|"])
    for symbol, rows in panels.items():
        lines.append(f"| {NAMES[symbol]} | {raw[symbol]['signed']} | {raw[symbol]['terminated']} | "
                     f"{sum(int(r['earnings_release']) for r in rows)} | {sum(int(r['earnings_yoy_available']) for r in rows)} |")
    total = sum(len(rows) for rows in panels.values())
    eligible = [r for rows in panels.values() for r in rows if r['news_status'] == 'complete']
    releases_used = sum(int(r['earnings_release']) for r in eligible)
    lines.extend(["", f"주 실험은 모든 입력 조건에서 동일한 {total:,}개 종목·거래일에 판단 기회를 두며 분기 실적 60회를 모두 보존한다. 뉴스 0은 그때까지 보존 판본에서 확인한 사건이 0건이라는 뜻이다. 실제 언론이 보도한 전체 사건 수는 알 수 없다. 사후에 발견한 자료 공백을 제외하는 별도 비교에서는 {len(eligible):,}개 종목·거래일과 분기 실적 {releases_used}회가 남는다. 나중에 발견한 공백으로 과거 주문이 사라지는 문제를 주 실험에서 피했다.", "",
                  "본문 누락·수정 시각과 공통 표본의 기준은 [자료와 검증 방법](data-notes.md)에 기록했다. [계약 판정표](../research/contract_events.json) · [실적 원장 검증](earnings.md) · [빅카인즈 대조](bigkinds-audit.md)", "",
                  "## 학습 자료의 시간 경계", "",
                  "| 검증 연도 | 학습 연도 | 주 실험 가격·뉴스 공통 표본(종목당, 1일 / 5일 / 10일 목표) | 사후 공백 제외 시 5일 표본(삼성전자 / SK하이닉스 / NAVER) |",
                  "|---:|---|---|---|"])
    folds = read(PREPARED/"folds.csv")
    for y in range(2022,2025):
        group = [r for r in folds if int(r["validation_year"]) == y]
        price = [r["train_price"] for r in group if r["symbol"] == SYMBOLS[0]]
        paired = [r["train_complete_news_only"] for r in group if r["horizon"] == "5"]
        lines.append(f"| {y} | 2020~{y-1} | {' / '.join(price)} | {' / '.join(paired)} |")
    lines.extend(["","각 연도의 첫 검증 주문은 직전 거래일 마감에 산출 가능한 신호를 쓴다. 학습 목표가 검증 기간의 시가를 참조하는 행은 학습에서 제외한다.", "",
                  "## 삼성전자 거래원장 예시", "",
                  "2020년 1월 15일 16:14, 엘오티베큠이 삼성전자에 장비를 공급한다는 보도가 나왔다. 삼성전자는 구매자다. 장 마감 후 보도여서 16일 마감에 첫 입력이 되었고, 그때 삼성전자 종가는 60,700원, 직전 20일 평균 대비 거래량은 1.029배였다. 이 예시에서는 16일 마감에 매수, 17일 마감에 보유, 20일 마감에 매도를 지정했다. 시작 자금은 1,000만 원이다. [이투데이 당시 보도](https://www.etoday.co.kr/news/view/1845590)", "",
                  "| 판단일 → 체결일 | 행동 | 기준 시가 | 모의 체결가 | 체결 수량 | 수수료·매도세 | 마감 장부자산 |",
                  "|---|---|---:|---:|---:|---:|---:|"])
    for r in read(OUTPUT/"samsung-example.csv"):
        action = {"buy":"매수","hold":"보유","sell":"매도"}[r["order"]]
        fill = f'{Decimal(r["fill_price"]):,.2f}원' if r["fill_price"] else "—"
        lines.append(f"| {r['decision_day']} → {r['day']} | {action} | {Decimal(r['reference_open']):,.0f}원 | {fill} | {r['filled_qty']}주 | "
                     f"{Decimal(r['fee'])+Decimal(r['sell_tax']):,.2f}원 | {Decimal(r['booked_equity']):,.2f}원 |")
    lines.extend(["","슬리피지는 시가 대비 편도 0.05%를 체결가에 반영했다. 매도는 마지막 날 청산 규칙과도 일치한다. 이 예시는 매매 규칙의 수익 예측력을 평가하는 데 사용하지 않는다.", "",
                  "## 단순 전략 재생", "",
                  "1,000만 원을 종목·연도·전략별로 새로 배정했다. 전액 현금, 첫 시가 매수 후 보유, 20일 이동평균, 사후에 자료 공백이 없다고 확인한 날만 판단하는 보조 이동평균을 같은 계좌에서 재생했다. 아래는 3개 검증 연도의 거래비용 차감 후 장부수익률이다. 각 연도의 마지막 거래일 시가에 보유분을 청산한다.", "",
                  "| 종목 | 연도 | 현금 | 매수 후 보유 | 이동평균 | 자료 공백 제외 이동평균(사후) | 연말 미확정 배당 권리가 있는 전략 |",
                  "|---|---:|---:|---:|---:|---:|---|"])
    baselines = read(OUTPUT/"annual.csv")
    for s in SYMBOLS:
        for y in (2022,2023,2024):
            selected = [r for r in baselines if r["symbol"] == s and r["year"] == str(y)]
            values = " | ".join(f"{float(r['booked_return_pct']):+.2f}%" for r in selected)
            names = {"cash":"현금","buy_hold":"보유","ma20":"이동평균","ma20_same_coverage":"확인일 이동평균"}
            pending = ", ".join(names[r["strategy"]] for r in selected if int(r["unpriced_rights_at_end"])) or "없음"
            lines.append(f"| {NAMES[s]} | {y} | {values} | {pending} |")
    lines.extend(["","배당은 개인 소득세 차감 전 금액을 사용했다. 권리가 생겨도 금액이 발표되기 전에는 장부자산에 더하지 않는다. ‘미확정 배당 권리’가 표시된 칸의 경제적 최종 수익은 나중에 확정된 배당에 따라 달라진다. 로컬 기준선 표 `annual.csv`에는 2024년 말까지 뒤늦게 확인된 권리를 더한 사후 수익도 별도 열로 표시했다. 끝까지 미확정이면 빈칸이다. 이 사후 값은 매매 판단·학습 보상에 사용하지 않는다. `cost-sensitivity.csv`에는 편도 슬리피지를 0%, 0.05%, 0.10%로 바꿨을 때의 장부수익·낙폭·거래 횟수를 기록했다.", ""])
    plot_monthly(docs/"monthly-prices.png")
    inputs = [PREPARED/f"{s}.csv" for s in SYMBOLS]
    inputs += [PREPARED/"folds.csv", MARKET/"raw/KOSPI.csv", ETODAY_CONTEXT, CONTRACT_DAILY,
               ROOT/"research/contract_source_events.psv", ROOT/"research/market_action_audit.json",
               ROOT/"research/dividends.psv", ROOT/"research/etoday_events.json", ROOT/"research/earnings.json",
               ROOT/"research/contract_events.json", ROOT/"research/contract_gaps.json", ROOT/"research/contract_audit.json"]
    inputs += [MARKET/"exchange"/f"{s}.csv" for s in SYMBOLS]
    inputs += [MARKET/"actions"/f"{s}.json" for s in SYMBOLS]
    write_json(docs/"input-fingerprints.json",
               {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    atomic_text(docs/"pretraining.md","\n".join(lines))


if __name__ == "__main__":
    main()
