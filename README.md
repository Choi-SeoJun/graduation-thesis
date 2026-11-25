# 국내 주식 매수·매도 타이밍 연구

**연구 질문**　과거 가격·거래량·시장 흐름으로 추정한 5거래일 상승 가능성을 매매에 사용할 때, 기업 계약 보도를 더 빨리 또는 더 많이 확보하면 세 종목의 매수일·매도일과 거래비용 차감 후 성과가 어떻게 달라지는가? 지도학습을 주 분석으로 삼고, 시간이 남으면 강화학습을 추가한다.

[연구 설계 전체 읽기](./졸업논문%20연구설계.md) · [종목 선정표와 근거](./졸업논문%20연구설계.md#연구-대상-종목) · [선행연구 6편 비교](./선행연구%20검토.md) · [설계 변경 기록](https://github.com/Choi-SeoJun/graduation-thesis/commits/main/)

## 무엇을 비교하나

**주 비교는 분기 실적을 이투데이로 고정하고 계약 뉴스의 출처만 바꾼 두 계좌다.** 각 종목에서 같은 거래일·모델·비용으로 매수일과 매도일, 거래비용 차감 후 수익률, 최대낙폭의 차이를 계산한다. 같은 계약의 후속 보도는 한 사건으로 묶고, 여러 매체에서 확인되면 먼저 이용할 수 있었던 거래일에 한 번만 반영한다.

| 서버 조건 | 계약 뉴스 | 분기 실적 |
|---|---|---|
| `contracts-earnings` · 주 비교 | 이투데이 | 이투데이 |
| `union-contracts-earnings` · 주 비교 | 이투데이 + 빅카인즈 수록 타 매체 | 이투데이 |
| `contracts` | 이투데이 | 사용하지 않음 |
| `union-contracts` | 이투데이 + 빅카인즈 수록 타 매체 | 사용하지 않음 |
| `market` | 사용하지 않음 | 사용하지 않음 |

다섯 조건 모두 같은 가격·거래량·시장 입력을 사용한다. **삼성전자(`005930`), SK하이닉스(`000660`), NAVER(`035420`)**를 각각 자기 과거 자료로 학습하고 다음 기간에 검증한다. 로지스틱 회귀와 랜덤포레스트가 추세·변동성·상대거래량 등의 지표와 조건별 뉴스를 보고, 다음 거래일 시가부터 5거래일 뒤 시가가 더 높을 가능성을 추정한다. 현금 보유 중 점수 0.55 이상이면 매수하고, 주식 보유 중 0.45 이하면 매도한다. 두 문턱 사이에서는 보유 상태를 유지한다. 매일 종가에 판단하고 다음 거래일 시가에 주문을 집행한다. 현금 유지·매수 후 보유·20일 이동평균 매매와 동일한 비용 기준으로 비교한다. [모델·거래 규칙](./졸업논문%20연구설계.md) · [계약 뉴스 출처별 대조](docs/bigkinds-audit.md)

## 어느 기간으로 검증하나

| 기간 | 쓰임 |
|---|---|
| 2019년 12월 30일 | 당시 코스피 보통주 시가총액 상위 3종목으로 표본 확정 |
| 2020~2024년 | 2020~2021년부터 학습을 시작해 2022년, 2023년, 2024년을 차례로 검증. 매번 검증 연도보다 앞선 자료로만 학습 |
| 2025년 | 최종 시험용으로 봉인. 모델과 매매 규칙을 확정한 뒤 한 번만 성과를 평가 |
| 2026년 | 연구 자료로 사용하지 않음 |

## 현재 결과

각 종목의 2020~2024년 거래일 **1,231일**에 시장 입력과 출처별 계약 사건, 이투데이 분기 발표 20건을 연결했다. 각 사건의 첫 가용 시각과 종가 판단일을 추적할 수 있다. 아래 자료는 학습 전 입력 점검 결과다. [종목·연도별 집계](docs/pretraining.md) · [사건별 근거](research/contract_source_events.psv) · [계약 뉴스 일별 입력](research/contract_source_daily.csv) · [분기 실적·자료 공백 일별 입력](research/etoday_daily_context.csv)

## 서버에서 시작하기

새 서버에서는 Python 3.11을 사용한다. 프로젝트를 내려받고 가상환경을 만든 뒤 다음 명령으로 개발 자료를 복원한다. 준비 명령은 시세를 내려받고 입력 파일 해시를 확인한다. 뉴스 판정과 분기 실적은 저장소에 있어 회원 로그인이나 기사 재수집이 필요하지 않다.

```sh
git clone https://github.com/Choi-SeoJun/graduation-thesis.git
cd graduation-thesis
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-server.txt
python -m src.server_setup
python -m src.server_train
```

입력을 점검한 뒤 다음 명령 하나로 **세 종목의 지도학습**을 실행한다. 종목마다 로지스틱 회귀와 랜덤포레스트를 다섯 입력 조건에서 각각 학습하고 2022·2023·2024년을 차례로 검증한다. 일정에 여유가 생겨 강화학습(Fitted Q Iteration)을 추가할 때에는 `python -m src.server_train --train --method fqi`를 별도로 실행한다.

```sh
python -m src.server_train --train
```

종목별로 실행할 때는 다음 중 원하는 줄을 쓴다. 각 종목의 결과는 별도 디렉터리에 저장된다.

```sh
python -m src.server_train --train --symbol 005930
python -m src.server_train --train --symbol 000660
python -m src.server_train --train --symbol 035420
```

시세 재다운로드 없이 시작하려면 연구자용 `data/local/server-handoff.zip`을 서버의 **빈 디렉터리**에 푼다. 세 종목의 준비된 입력과 실행 코드가 들어 있다. 그 디렉터리에서 Python 3.11 환경에 같은 패키지를 설치한 뒤 아래 명령을 실행한다.

```sh
python -m src.server_setup --verify-only
python -m src.server_train --train
```

기본 결과는 `data/local/server-runs/contract-sources-v1/paired-observed/h5/<종목코드>/<검증연도>/source-comparison/`에 저장된다. `supervised.csv`에 지도학습 후보별 결과가 담긴다. 선택 실험인 강화학습을 실행하면 `fqi.csv`에는 시드별 결과, `fqi-seeds.csv`에는 같은 시드 쌍에서 구한 차이의 중앙값과 범위가 저장된다. **주 비교는 `contrast=with-earnings`를 읽는다.** `net_return_delta_pp`가 양수이면 다매체 조건의 비용 차감 후 수익률이 높았고, `drawdown_delta_pp`가 양수이면 다매체 계좌의 최대낙폭이 더 컸다. `with-earnings/<모델>/daily.csv`에서 판단일과 체결일, 목표 보유 상태, 주문 방향·수량을 확인할 수 있다. 연말 강제청산은 별도 표시한다.

종목·연도 일부를 검증하려면 `--symbol 005930 --year 2022`를 추가한다. `--method supervised --horizon 1` 또는 `--horizon 10`은 지도학습의 목표 기간을 바꾸는 보조 실험이다. `--features`는 시장 변수 묶음, `--complete-news-only`는 이투데이 자료 공백일을 제외한 사후 비교, `--slippage-sensitivity`는 같은 점수에 편도 슬리피지 0·5·10bp를 적용한다. `--price-only`는 시장 입력으로만 실행한다. [변수 정의와 표본](docs/data-notes.md)

## 로컬에서 개발 자료 다시 만들기

프로젝트 루트에서 순서대로 실행한다. 이투데이의 일별·월별 목록은 로컬에 캐시된다. 원자료의 제공처가 과거 값을 갱신할 수 있으므로 동일 시점 결과를 재현할 때는 보관한 서버용 묶음과 해시를 사용한다.

```sh
python -m src.fetch_market
python -m src.exchange_archive
python -m src.market_data
python -m src.news_archive
python -m src.contract_review collect
python -m src.contract_review queue
python -m src.contract_review finish
python -m src.earnings
python -m src.news_events
python -m src.contract_sources
python -m src.prepare
python -m src.baselines
python -m unittest discover -s tests
python -m pip install matplotlib==3.10.8
python -m src.report
python -m src.bundle
```

GitHub에는 연구자가 판정한 사건·거래일별 뉴스 입력, 실행 코드와 파일 해시를 기록한다. 시세 원본, 회원 엑셀, 기사 본문과 모의계좌 거래원장은 로컬의 `data/local/`에 보관한다.
