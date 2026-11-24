# 국내 주식 매수·매도 타이밍 연구

**연구 질문**　장 마감 후 다음 거래일에 주식을 살지, 팔지, 그대로 둘지를 결정할 때 지도학습과 강화학습 중 어느 쪽의 거래비용 차감 후 성과가 더 좋은가? 같은 시세·시장 정보에 기업 사건 뉴스를 더하면 각 방법의 매매 판단과 성과가 어떻게 달라지는가?

[연구 설계 전체 읽기](./졸업논문%20연구설계.md) · [종목 선정표와 근거](./졸업논문%20연구설계.md#연구-대상-종목) · [선행연구 6편 비교](./선행연구%20검토.md) · [설계 변경 기록](https://github.com/Choi-SeoJun/graduation-thesis/commits/main/)

## 무엇을 비교하나

| 학습 방법 | 가격·거래량·시장 | 왼쪽 입력 + 계약 | 왼쪽 입력 + 분기 실적 |
|---|---|---|---|
| 지도학습 | 5거래일 상승 확률로 매매 | 체결·해지와 보도 수 추가 | 분기 실적 발표와 영업이익 변화 추가 |
| 강화학습 | 다음 시가의 주식·현금 보유 선택 | 같은 계약 정보로 별도 학습 | 같은 분기 실적까지 포함해 별도 학습 |

여섯 조건 모두 **삼성전자(`005930`), SK하이닉스(`000660`), NAVER(`035420`)**의 2020~2025년 자료를 대상으로 한다. 각 종목의 과거 자료로 따로 학습한 뒤 같은 종목의 이후 기간에서 매매 성과를 평가한다. 주문은 다음 거래일 시가에 체결된다고 가정하고 같은 거래비용을 적용한다. 각 종목의 매수 후 보유, 이동평균 매매 등 단순한 전략과도 비교한다.

계약은 보도 본문에서 거래 당사자와 확정 여부를 확인한다. 실적은 각 분기 실제 영업이익을 공개한 첫 이투데이 보도를 추적한다. 빅카인즈는 이투데이가 빠뜨린 사건과 앞선 보도를 대조하는 데 쓴다. 매매 결정에는 당시 확인된 정보만 쓴다. [빅카인즈 대조 결과](docs/bigkinds-audit.md)

## 어느 기간으로 검증하나

| 기간 | 쓰임 |
|---|---|
| 2019년 12월 30일 | 당시 코스피 보통주 시가총액 상위 3종목으로 표본 확정 |
| 2020~2024년 | 2020~2021년부터 학습을 시작해 2022년, 2023년, 2024년을 차례로 검증. 매번 검증 연도보다 앞선 자료로만 학습 |
| 2025년 | 최종 시험용으로 봉인. 모델과 매매 규칙을 확정한 뒤 한 번만 성과를 평가 |
| 2026년 | 연구 자료로 사용하지 않음 |

## 현재 결과

2020~2024년 개발 기간의 일봉과 이투데이 기업 사건을 거래일별로 연결했다. 종목·연도별 계약과 분기 실적, 공통 표본의 크기, 단순 전략의 장부수익률과 거래원장은 [학습 전 데이터 점검 결과](docs/pretraining.md)에 정리했다. [자료와 검증 방법](docs/data-notes.md) · [계약 본문 판정](research/contract_events.json) · [분기 실적 원장](research/earnings.json)

## 서버에서 시작하기

연구자용 로컬 파일 `data/local/server-handoff.zip`에는 개발 입력, 배당 원장, 실행 코드와 파일별 SHA-256이 들어 있다. 서버의 빈 작업 디렉터리에 풀면 공개 GitHub에 올리지 않은 시세·입력표가 함께 배치된다. Python 3.11 기준으로 아래 명령은 입력과 시간 경계만 검사한다.

```sh
python -m src.server_train
python -m src.server_train --complete-news-only
python -m src.server_train --price-only
```

서버에서 모델을 학습하는 단계에서는 다음 명령을 사용한다. 주 실험은 5거래일 목표와 세 입력 조건으로 종목별 전체 개발 거래일에 판단한다. 확인할 수 있는 기사 시점까지 관측된 사건만 제공한다. 사후에 자료 공백이 발견된 날을 제외하는 민감도 분석은 `--complete-news-only`, 가격 열만 사용하는 보조 비교는 `--price-only`로 별도 저장한다.

```sh
python -m pip install -r requirements-server.txt
python -m src.server_train --train
python -m src.server_train --train --complete-news-only
python -m src.server_train --train --price-only
```

연도·종목 단위로 먼저 실행하려면 `--year 2022 --symbol 005930`을 추가한다. 지도학습의 목표 기간 민감도는 `--method supervised --horizon 1` 또는 `--method supervised --horizon 10`으로 바꾼다. `--features`로 `trend`, `trend-vol`, `trend-vol-market`, `trend-vol-volume`, `all` 중 하나를 골라 변수 묶음의 기여도를 비교한다. `--slippage-sensitivity`는 같은 모델 점수를 사용해 편도 슬리피지 0%, 0.05%, 0.10%에서 계좌를 재생한다. 서버 출력은 `data/local/server-runs/`에 남는다. 2025년 최종 시험은 모델과 설정을 확정한 뒤 진행한다.

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
python -m src.prepare
python -m src.baselines
python -m unittest discover -s tests
python -m pip install matplotlib==3.10.8
python -m src.report
python -m src.bundle
```

시세·기사 목록·서버용 입력·모의계좌 원장은 로컬의 `data/local/`에 보관한다. GitHub에는 코드, 출처, 기사별 자체 판정과 집계 결과를 기록한다.
