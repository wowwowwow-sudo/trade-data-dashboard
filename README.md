# 수출입 데이터 대시보드

EPIC Finance(bigfinance.co.kr) 커스텀 워치리스트 기반 수출입 품목 카드 대시보드.

## 실행
```
pip install -r requirements.txt
streamlit run app.py
```

`requirements.txt`는 배포되는 대시보드(app.py)가 실제로 쓰는 패키지만 담는다
(streamlit/pandas/numpy/plotly). Streamlit Cloud 무료 티어는 메모리가 넉넉하지
않아, playwright처럼 무거운 스크래핑 전용 패키지를 여기 섞으면 안 된다. 로컬에서
스크래핑 스크립트(`scrape_bigfinance.py`, `scrape_bigfinance_items.py`,
`import_item_mapping.py`)를 돌리려면:
```
pip install -r requirements-scrape.txt
```

## 데이터 소스와 화면 구성

두 종류의 스크래핑 소스가 있다.

- **`scrape_bigfinance.py`** — EPIC Finance "품목 및 지역 커스텀 설정" 화면. 품목별 + 그
  하위 기업별 수출액을 함께 가져온다. 이 화면 다운로드는 매월 1일 갱신되는 월 1회
  값뿐이라(2026-07-13 확인), 여기서만 **기업별 breakdown**을 얻을 수 있다.
  → `trade_history_long.csv`(품목,월별), `company_trade_history_long.csv`(기업,월별)
- **`scrape_bigfinance_items.py`** — EPIC Finance "품목 커스텀 설정" 화면. 품목만
  나오지만 **10일/20일/월말(상순/중순/하순) 단위**로 갱신된다.
  → `trade_history_decade_long.csv`(월말로 합치지 않은 순旬 스냅샷 그대로 누적)

투자 시그널 보드/전체 품목 등 앱의 품목 계산은 `trade_history_decade_long.csv`를
월별로 롤업한 값(월말 스냅샷)을 기본 소스로 쓴다 - 더 세분화됐고 항상 한 달 더
최신인 상위 호환 데이터라서다. 품목 상세 화면의 "10/20일별 / 월별로 묶어보기"
토글에서 순旬 단위 원본까지 파고들 수 있다. (사이드바에 별도 "품목 커스텀 설정"
페이지를 두지 않고 상세 화면 토글로 통합했다.)

기업별 데이터는 이 통합과 무관하게 품목 상세의 "관련 기업" 테이블 → 기업 상세
드릴다운에서 그대로 쓴다.

## 데이터 갱신

로컬에서 아래 래퍼를 돌리면 git pull → 두 스크래퍼 실행 → interlink 발행 →
변경분 commit/push까지 한 번에 처리된다.

```
python run_update.py
```

`scrape_bigfinance_items.py`는 아직 작업 스케줄러 자동 실행에는 넣지 않았다
(안정화 후 등록 예정). 개별 스크래퍼를 직접 돌리려면:

```
python scrape_bigfinance.py        # 품목+기업, 월 1회
python scrape_bigfinance_items.py  # 품목, 10/20/30일
```

품목이 쌓일수록 카드의 추이 막대(스파크라인)가 자동으로 길어집니다.
