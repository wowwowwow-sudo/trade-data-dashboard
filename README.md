# 수출입 데이터 대시보드

EPIC Finance(bigfinance.co.kr) 커스텀 워치리스트 기반 수출입 품목 카드 대시보드.

## 실행
```
pip install -r requirements.txt
streamlit run app.py
```

## 데이터 갱신
1. bigfinance.co.kr에 로그인 후 "잠정 수출 품목 리스트" 화면 확인
2. 새 값을 `append_snapshot.py`의 `append_snapshot()`에 넘겨서 실행
   (품목명/기준일 조합이 이미 있으면 최신 값으로 덮어쓰고, 없으면 새 행 추가)
3. `trade_history_long.csv`가 갱신되고, 앱을 새로고침하면 반영됨

품목이 쌓일수록 카드의 추이 막대(스파크라인)가 자동으로 길어집니다.

## 품목 커스텀 설정 데이터 (10/20일 단위)

사이드바 "품목 커스텀 설정(10/20일)" 페이지는 별도 화면을 방문하지 않는다.
`scrape_bigfinance.py`("품목 및 지역 커스텀 설정" 화면)가 품목별로 받는 모달 다운로드
파일 자체가 이미 2016년부터의 10일/20일/월말 전체 히스토리라, 한 번 방문으로
`trade_history_long.csv`(월말 기준)와 `trade_history_decade_long.csv`(10일/20일/월말
스냅샷 그대로)를 동시에 갱신한다.

```
python scrape_bigfinance.py
```
