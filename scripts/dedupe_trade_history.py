"""
trade_history_long.csv에 2017년경부터 쌓인 (품목,월) 중복 행을 정리하는 1회성 스크립트.

원인: 과거 어느 시점에 기준일이 "월 1일"로 기록된 행과, scrape_bigfinance.py가
정상적으로 기록하는 "월말" 행이 append_snapshot()의 (품목명,기준일) 문자열 매칭
dedup을 통과하지 못해(날짜 문자열 자체가 다르므로) 같은 달에 행이 2개씩 남아있었다.

정리 규칙: 같은 (품목명, 연-월)에 행이 2개면 월말(day>=28) 행만 남기고 월 1일 행은
버린다 - scrape_bigfinance.py가 지금도 월말 기준으로 기록하므로 그게 정본이다.
행이 1개뿐인 달(자동화 이전/이후로 한쪽 소스만 있던 달)은 그대로 둔다.

실행: python scripts/dedupe_trade_history.py
"""
from pathlib import Path

import pandas as pd

HISTORY_PATH = Path(__file__).parent.parent / "trade_history_long.csv"


def main() -> None:
    df = pd.read_csv(HISTORY_PATH, encoding="utf-8-sig")
    cols = list(df.columns)  # 품목명, 대분류, 기준일, 수출금액, 단가
    date_col, item_col = cols[2], cols[0]

    df["_date"] = pd.to_datetime(df[date_col])
    df["_ym"] = df["_date"].dt.to_period("M")
    df["_day"] = df["_date"].dt.day

    before = len(df)
    # 같은 (품목,연월) 그룹 내에서 day가 가장 큰(월말) 행만 남긴다.
    df = df.sort_values("_day").groupby([item_col, "_ym"], as_index=False).tail(1)
    df = df.drop(columns=["_date", "_ym", "_day"]).sort_values([item_col, date_col]).reset_index(drop=True)
    after = len(df)

    df.to_csv(HISTORY_PATH, index=False, encoding="utf-8-sig")
    print(f"완료: {before}행 -> {after}행 ({before - after}개 중복 행 제거)")


if __name__ == "__main__":
    main()
