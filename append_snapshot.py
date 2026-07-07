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
COMPANY_HISTORY_PATH = Path(__file__).parent / "company_trade_history_long.csv"


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


def append_company_snapshot(records: list[dict]) -> pd.DataFrame:
    """
    company_trade_history_long.csv에 새 스냅샷을 누적한다. append_snapshot()과 같은
    방식(중복 키가 있으면 최신 값으로 덮어쓰기)이지만, 품목 집계 데이터(trade_history_long.csv)와
    완전히 분리된 별도 파일에 저장한다 - EPIC Finance "품목 및 지역 커스텀 설정"에서
    하위 기업(지역) 행이 실제로 설정된 품목만 데이터가 존재한다.

    records 예시:
    [
        {"품목명": "전기전자_CCL", "기업명": "두산", "기준일": "2026-06-30",
         "수출금액": 63881391, "단가": 106.57},
    ]
    """
    new_df = pd.DataFrame(records)
    required = ["품목명", "기업명", "기준일", "수출금액", "단가"]
    missing = set(required) - set(new_df.columns)
    if missing:
        raise ValueError(f"records에 컬럼이 빠졌습니다: {missing}")

    if COMPANY_HISTORY_PATH.exists():
        history = pd.read_csv(COMPANY_HISTORY_PATH)
    else:
        history = pd.DataFrame(columns=required)

    combined = pd.concat([history, new_df[required]], ignore_index=True)
    combined = combined.drop_duplicates(subset=["품목명", "기업명", "기준일"], keep="last")
    combined = combined.sort_values(["품목명", "기업명", "기준일"]).reset_index(drop=True)
    combined.to_csv(COMPANY_HISTORY_PATH, index=False)
    n_pairs = combined.drop_duplicates(subset=["품목명", "기업명"]).shape[0]
    print(f"저장 완료: {COMPANY_HISTORY_PATH} (총 {len(combined)}행, {n_pairs}개 품목-기업 조합)")
    return combined


if __name__ == "__main__":
    example_records = [
        {"품목명": "반도체_메모리", "대분류": "반도체", "기준일": "2026-07-31",
         "수출금액": 31000000000, "단가": 81000.0},
    ]
    append_snapshot(example_records)
