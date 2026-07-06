"""
trade_history_long.csv / config/item_mapping.csv 상태 점검 스크립트.
데이터를 고치거나 새로 만들지 않고, 현재 상태를 그대로 보고만 한다.

실행: python check_data.py
"""

from utils_data import (
    HISTORY_PATH,
    MAPPING_PATH,
    MIN_HEALTHY_MONTHS,
    DataLoadError,
    compute_item_metrics,
    get_missing_items,
    load_history,
    load_item_mapping,
)


def main() -> None:
    print(f"=== {HISTORY_PATH.name} 점검 ===")
    try:
        df, decade = load_history()
    except DataLoadError as e:
        print(f"[에러] {e}")
        return

    print(f"총 행 수: {len(df)}")
    print(f"품목 수: {df['item_name'].nunique()}")
    print(f"날짜 범위: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"순(旬)/열흘 구간 컬럼 존재 여부: {decade}")

    if "unit_price" in df.columns:
        ratio = df["unit_price"].notna().mean() * 100
        print(f"단가 컬럼 존재 비율: {ratio:.1f}% ({df['unit_price'].notna().sum()}/{len(df)}행)")
    else:
        print("단가 컬럼: 없음 (단가 차트는 생략됨)")

    dup_mask = df.duplicated(subset=["item_name", "date"], keep=False)
    print(f"(품목명, 기준일) 중복 행: {dup_mask.sum()}개")

    for col in ["item_name", "date", "export_amount"]:
        n_missing = df[col].isna().sum()
        print(f"결측치 - {col}: {n_missing}개 ({n_missing / len(df) * 100:.2f}%)")

    latest_period = df["date"].max().to_period("M")
    latest_counts = df.groupby("item_name")["date"].max().dt.to_period("M")
    n_latest = (latest_counts == latest_period).sum()
    print(f"최신월({latest_period})에 데이터가 있는 품목 수: {n_latest}/{df['item_name'].nunique()}")

    print()
    print("품목별 관측치 개수 분포:")
    counts = df.groupby("item_name").size().sort_values()
    print(counts.describe().to_string())
    n_unhealthy = (counts < MIN_HEALTHY_MONTHS).sum()
    print(f"{MIN_HEALTHY_MONTHS}개월 미만 데이터만 있는 품목: {n_unhealthy}개")
    if n_unhealthy:
        print(counts[counts < MIN_HEALTHY_MONTHS].to_string())

    print()
    print(f"=== {MAPPING_PATH} 매칭 점검 ===")
    mapping = load_item_mapping(df)
    mapping_items = set(mapping["item_name"])
    data_items = set(df["item_name"].unique())
    matched = mapping_items & data_items
    print(f"item_mapping.csv 품목 수: {len(mapping_items)}")
    print(f"trade_history 품목 수: {len(data_items)}")
    print(
        f"매칭률: {len(matched)}/{len(mapping_items)} "
        f"({len(matched) / len(mapping_items) * 100:.1f}% of mapping)"
    )

    issues = get_missing_items(df, mapping)
    print()
    print(f"수집 실패/누락 의심 품목: {len(issues)}개")
    for issue in issues:
        print(f"  - {issue['item_name']}: {issue['reason']}")

    print()
    print("=== YoY/MoM 재계산 점검 (품목당 최신 행) ===")
    with_metrics = compute_item_metrics(df)
    latest = with_metrics.sort_values("date").groupby("item_name", as_index=False).tail(1)
    n_yoy = latest["yoy"].notna().sum()
    n_mom = latest["mom"].notna().sum()
    print(f"최신 시점 YoY 계산 가능: {n_yoy}/{len(latest)}개 품목")
    print(f"최신 시점 MoM 계산 가능: {n_mom}/{len(latest)}개 품목")


if __name__ == "__main__":
    main()
