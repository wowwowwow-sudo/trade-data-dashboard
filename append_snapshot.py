"""
trade_history_long.csv에 새 스냅샷을 누적하는 유틸리티.

사용 흐름:
  1. bigfinance.co.kr에 로그인 후 "잠정 수출 품목 리스트" 화면 확인
  2. 읽어온 값을 아래 records 형식으로 정리
  3. append_snapshot(records) 호출 -> trade_history_long.csv에 중복 없이 누적

(품목명, 기준일) 조합이 이미 있으면 새 값으로 덮어쓰고, 없으면 새 행을 추가한다.
몇 달 쌓이면 스파크라인이 자연히 길어진다.
"""

from pathlib import Path

import pandas as pd

HISTORY_PATH = Path(__file__).parent / "trade_history_long.csv"


def append_snapshot(records: list[dict]) -> pd.DataFrame:
    """
    records 예시 (대분류는 생략 가능 - 품목명 첫 '_' 앞부분에서 자동 추출):
    [
        {"품목명": "방산_유도무기", "기준일": "2026-07-31",
         "수출금액": 12000000, "단가": 950.0},
    ]
    """
    new_df = pd.DataFrame(records)
    if "대분류" not in new_df.columns or new_df["대분류"].isna().any():
        auto = new_df["품목명"].str.split("_").str[0]
        new_df["대분류"] = new_df["대분류"].fillna(auto) if "대분류" in new_df else auto

    required = {"품목명", "대분류", "기준일", "수출금액", "단가"}
    missing = required - set(new_df.columns)
    if missing:
        raise ValueError(f"records에 컬럼이 빠졌습니다: {missing}")

    if HISTORY_PATH.exists():
        history = pd.read_csv(HISTORY_PATH)
    else:
        history = pd.DataFrame(columns=list(required))

    combined = pd.concat([history, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["품목명", "기준일"], keep="last")
    combined = combined.sort_values(["품목명", "기준일"]).reset_index(drop=True)
    combined.to_csv(HISTORY_PATH, index=False)
    print(f"저장 완료: {HISTORY_PATH} (총 {len(combined)}행, {combined['품목명'].nunique()}개 품목)")
    return combined


if __name__ == "__main__":
    example_records = [
        {"품목명": "반도체_메모리", "대분류": "반도체", "기준일": "2026-07-31",
         "수출금액": 31000000000, "단가": 81000.0},
    ]
    append_snapshot(example_records)
