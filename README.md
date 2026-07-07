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
