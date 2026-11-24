"""Build the development-only descriptive report. Matplotlib is needed only for the figure."""
import csv
import hashlib
from datetime import date
from decimal import Decimal
from statistics import median

from src.fetch_market import ROOT, MARKET, SYMBOLS, atomic_text, write_json
from src.market_data import FEATURES
from src.prepare import PREPARED
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
                  "뉴스 완료는 정해 둔 단일 매체·검색 범위에서 해당 종목과 거래일의 집계가 가능하다는 뜻이다. 신규 사건은 같은 계약의 반복 보도를 합쳐 한 번만 센다.", "",
                  "| 종목 | 연도 | 시세 거래일 | 뉴스 완료 | 뉴스 미확인 | 체결 사건 | 해지 사건 | 계약 보도 |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for s, rows in panels.items():
        for y in range(2020,2025):
            yr = [r for r in rows if r["date"].startswith(str(y))]
            complete = [r for r in yr if r["news_status"] == "complete"]
            counts = [sum(int(r[k]) for r in complete) if complete else "—"
                      for k in ("contract_signed","contract_terminated","contract_reports")]
            lines.append(f"| {NAMES[s]} | {y} | {len(yr)} | {len(complete)} | {len(yr)-len(complete)} | " +
                         " | ".join(map(str,counts)) + " |")
    event_counts = {s: sum(int(r["contract_signed"]) for r in rows if r["news_status"] == "complete")
                    for s, rows in panels.items()}
    terminations = sum(int(r["contract_terminated"]) for rows in panels.values() for r in rows if r["news_status"] == "complete")
    lines.extend(["", f"확인된 입력에 포함한 신규 체결은 삼성전자 {event_counts['005930']}건, SK하이닉스 {event_counts['000660']}건, NAVER {event_counts['035420']}건이다. 해지 보도는 세 종목 합계 {terminations}건이다. NAVER는 학습·검증 구간별로 뉴스 변수가 거의 항상 0이어서 뉴스 효과를 결론 내리기 어렵다. 해지 변수도 관측 사건이 충분해야 방향이나 효과를 해석할 수 있다.",
                  "", "일별 범위, 수정 기사 처리와 판단 보류 기준은 [자료·검증 방법](data-notes.md)에 기록했다.", "",
                  "## 학습 자료의 시간 경계", "",
                  "| 검증 연도 | 학습 연도 | 뉴스 제약 없는 가격 표본(종목당, 1일 / 5일 / 10일 목표) | 뉴스 조건과 짝지은 5일 표본(삼성전자 / SK하이닉스 / NAVER) |",
                  "|---:|---|---|---|"])
    folds = read(PREPARED/"folds.csv")
    for y in range(2022,2025):
        group = [r for r in folds if int(r["validation_year"]) == y]
        price = [r["train_price"] for r in group if r["symbol"] == SYMBOLS[0]]
        paired = [r["train_paired"] for r in group if r["horizon"] == "5"]
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
                  "1,000만 원을 종목·연도·전략별로 새로 배정했다. 전액 현금, 첫 시가 매수 후 보유, 20일 이동평균, 뉴스가 확인된 날만 판단하는 20일 이동평균을 같은 계좌에서 재생했다. 아래는 3개 검증 연도의 거래비용 차감 후 장부수익률이다. 각 연도의 마지막 거래일 시가에 보유분을 청산한다.", "",
                  "| 종목 | 연도 | 현금 | 매수 후 보유 | 이동평균 | 뉴스 확인일 이동평균 | 연말 미확정 배당 권리가 있는 전략 |",
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
    inputs += [PREPARED/"folds.csv", MARKET/"raw/KOSPI.csv",
               ROOT/"data/local/news/etoday/development/news_daily.csv",
               ROOT/"research/dividends.psv", ROOT/"research/etoday_events.json"]
    inputs += [MARKET/"exchange"/f"{s}.csv" for s in SYMBOLS]
    write_json(docs/"input-fingerprints.json",
               {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    atomic_text(docs/"pretraining.md","\n".join(lines))


if __name__ == "__main__":
    main()
