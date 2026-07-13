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

## 데이터 갱신
1. bigfinance.co.kr에 로그인 후 "잠정 수출 품목 리스트" 화면 확인
2. 새 값을 `append_snapshot.py`의 `append_snapshot()`에 넘겨서 실행
   (품목명/기준일 조합이 이미 있으면 최신 값으로 덮어쓰고, 없으면 새 행 추가)
3. `trade_history_long.csv`가 갱신되고, 앱을 새로고침하면 반영됨

품목이 쌓일수록 카드의 추이 막대(스파크라인)가 자동으로 길어집니다.

## 품목 커스텀 설정 데이터 (10/20일 단위)

사이드바 "품목 커스텀 설정(10/20일)" 페이지는 EPIC Finance "품목 커스텀 설정" 화면
(상순/중순/하순 갱신)을 별도로 스크래핑한다. "품목 및 지역 커스텀 설정" 화면
(`scrape_bigfinance.py`)의 다운로드는 매월 1일 갱신되는 월 1회 값뿐이라(2026-07-13
확인), 10/20일 단위 데이터는 이 화면에서 가져올 수 없다.

```
python scrape_bigfinance_items.py
```

`trade_history_decade_long.csv`에 품목별 전체 히스토리(월말로 합치지 않은 10일/20일/월말
스냅샷 그대로)가 누적된다. 로그인/모달 다운로드 방식은 `scrape_bigfinance.py`
("품목 및 지역 커스텀 설정" 화면용)와 동일하다.
