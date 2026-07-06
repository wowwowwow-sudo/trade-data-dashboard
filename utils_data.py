"""
trade_history_long.csv 로딩/정규화, YoY/MoM 계산, item_mapping.csv/favorites.json
관리를 담당하는 유틸리티 모듈. app.py와 check_data.py가 공용으로 사용한다.

원칙: 파일에 없는 값(단가, 기업별/지역별 수출액 등)을 임의로 만들어내지 않는다.
컬럼이 없으면 해당 기능은 조용히 생략하거나 NaN을 반환한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent
HISTORY_PATH = BASE_DIR / "trade_history_long.csv"
MAPPING_PATH = BASE_DIR / "config" / "item_mapping.csv"
FAVORITES_PATH = BASE_DIR / "favorites.json"

# 최소 이 개월수 미만의 데이터만 있는 품목은 "수집이 제대로 안 된 것"으로 간주한다.
# 실제 관측치 기준: 정상 수집된 품목은 최소 54개월 이상, 수집 실패 품목은 3개월뿐이라
# 12개월(1년)을 기준으로 삼으면 명확히 구분된다. 특정 품목명을 하드코딩하지 않기 위한 일반 규칙.
MIN_HEALTHY_MONTHS = 12

# 한/영 컬럼명 별칭 -> 표준 컬럼명
COLUMN_ALIASES: dict[str, list[str]] = {
    "item_name": ["품목명", "item_name", "product_name"],
    "category": ["대분류", "category"],
    "date": ["기준일", "date", "날짜"],
    "export_amount": ["수출금액", "export_amount", "amount"],
    "unit_price": ["수출단가", "단가", "unit_price"],
    "company": ["기업", "company", "related_company"],
    "region": ["지역", "region", "country"],
}

REQUIRED_COLUMNS = ["item_name", "date", "export_amount"]


class DataLoadError(Exception):
    """CSV 컬럼명이 예상과 달라 정규화에 실패했을 때 - app.py에서 st.error로 안내."""


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in df.columns and canonical not in rename_map.values():
                rename_map[alias] = canonical
                break
    return df.rename(columns=rename_map)


def clean_numeric(series: pd.Series) -> pd.Series:
    """쉼표, 달러/원 표시, 공백, % 기호가 섞여 있어도 숫자로 변환. 안 되는 값은 NaN."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype(str)
        .str.replace(r"[,$₩%\s]", "", regex=True)
        .replace({"": np.nan, "-": np.nan, "nan": np.nan, "None": np.nan})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def has_decade_columns(raw_df: pd.DataFrame) -> bool:
    """1~10일/11~20일/21~말일(순旬) 구간 컬럼이 있는지 확인.
    현재 데이터 소스(trade_history_long.csv)는 월말 기준만 있어 항상 False.
    이후 다른 데이터 소스에 순(旬) 단위 컬럼이 추가되면 이 함수만 갱신하면 된다."""
    pattern = re.compile(r"(상순|중순|하순|순旬|1.?10일|11.?20일|21.?말일)")
    return any(pattern.search(str(c)) for c in raw_df.columns)


def load_history() -> tuple[pd.DataFrame, bool]:
    """trade_history_long.csv를 로딩/정규화한다.
    반환: (정규화된 DataFrame, 순(旬)구간 컬럼 존재 여부)
    컬럼명이 예상과 다르면 DataLoadError를 발생시켜 app.py가 안내 메시지를 보여주게 한다.
    """
    if not HISTORY_PATH.exists():
        raise DataLoadError(f"{HISTORY_PATH.name} 파일을 찾을 수 없습니다. (경로: {HISTORY_PATH})")

    try:
        raw = pd.read_csv(HISTORY_PATH)
    except Exception as e:
        raise DataLoadError(f"{HISTORY_PATH.name}을 읽는 중 오류가 발생했습니다: {e}") from e

    decade = has_decade_columns(raw)
    df = normalize_columns(raw)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataLoadError(
            f"{HISTORY_PATH.name}에서 다음 필수 컬럼을 찾지 못했습니다: {missing}. "
            f"실제 컬럼: {list(raw.columns)}. "
            "utils_data.py의 COLUMN_ALIASES에 해당 컬럼명을 추가해주세요."
        )

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["export_amount"] = clean_numeric(df["export_amount"])
    if "unit_price" in df.columns:
        df["unit_price"] = clean_numeric(df["unit_price"])
    if "category" not in df.columns:
        df["category"] = df["item_name"].astype(str).str.split("_").str[0]

    df = df.dropna(subset=["date", "item_name"]).copy()
    df = df.sort_values(["item_name", "date"]).reset_index(drop=True)
    return df, decade


def compute_item_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """품목별로 정렬 후, 같은 (연,월) 기준으로 YoY/MoM을 다시 계산한다.
    원본에 YoY/MoM 컬럼이 있어도 무시하고 date/export_amount로만 재계산한다.
    전년동월/전월 데이터가 없으면 NaN."""

    def _per_item(g: pd.DataFrame) -> pd.DataFrame:
        g = g.sort_values("date").copy()
        g["period"] = g["date"].dt.to_period("M")
        amount_by_period = dict(zip(g["period"], g["export_amount"]))

        def _pct_change(period, offset):
            base = amount_by_period.get(period - offset)
            if base is None or pd.isna(base) or base == 0:
                return np.nan
            current = amount_by_period[period]
            if pd.isna(current):
                return np.nan
            return (current - base) / base * 100

        g["yoy"] = g["period"].apply(lambda p: _pct_change(p, 12))
        g["mom"] = g["period"].apply(lambda p: _pct_change(p, 1))
        g["prev_month_amount"] = g["period"].apply(lambda p: amount_by_period.get(p - 1))
        return g

    parts = [_per_item(g) for _, g in df.groupby("item_name")]
    return pd.concat(parts, ignore_index=True)


def get_latest_snapshot(df_with_metrics: pd.DataFrame) -> pd.DataFrame:
    """품목별 최신 시점 1행만 추출 (카드/요약용)."""
    latest = df_with_metrics.sort_values("date").groupby("item_name", as_index=False).tail(1)
    return latest.reset_index(drop=True)


# ---------- item_mapping.csv ----------
def load_item_mapping(df: pd.DataFrame) -> pd.DataFrame:
    """config/item_mapping.csv를 로딩한다.
    - 파일이 없으면: 현재 데이터의 품목으로 새로 만든다 (category=품목명 첫 '_' 앞부분,
      related_companies는 빈 값 - 실제 기업 매핑 정보가 없어 임의로 채우지 않는다).
    - 파일이 있으면: 거기 없는 신규 품목만 추가하고, 기존 행은 절대 덮어쓰지 않는다.
    """
    MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    current_items = sorted(df["item_name"].dropna().unique().tolist())

    if MAPPING_PATH.exists():
        mapping = pd.read_csv(MAPPING_PATH, dtype=str).fillna("")
        existing_items = set(mapping["item_name"])
        new_items = [i for i in current_items if i not in existing_items]
        if new_items:
            new_rows = pd.DataFrame(
                {
                    "item_name": new_items,
                    "category": [i.split("_")[0] for i in new_items],
                    "related_companies": ["" for _ in new_items],
                }
            )
            mapping = pd.concat([mapping, new_rows], ignore_index=True)
            mapping.to_csv(MAPPING_PATH, index=False)
        return mapping

    mapping = pd.DataFrame(
        {
            "item_name": current_items,
            "category": [i.split("_")[0] for i in current_items],
            "related_companies": ["" for _ in current_items],
        }
    )
    mapping.to_csv(MAPPING_PATH, index=False)
    return mapping


def get_related_companies(mapping: pd.DataFrame, item_name: str) -> list[str]:
    row = mapping[mapping["item_name"] == item_name]
    if row.empty:
        return []
    raw = str(row.iloc[0].get("related_companies", "") or "")
    return [c.strip() for c in re.split(r"[;,]", raw) if c.strip()]


def get_categories(mapping: pd.DataFrame, df: pd.DataFrame) -> list[str]:
    """카테고리 목록을 하드코딩하지 않고 데이터에서 동적으로 뽑는다."""
    cats = set(mapping["category"].dropna().unique().tolist()) if not mapping.empty else set()
    cats |= set(df["category"].dropna().unique().tolist())
    return sorted(c for c in cats if c)


def get_missing_items(df: pd.DataFrame, mapping: pd.DataFrame) -> list[dict]:
    """수집 실패/누락 품목 계산.
    1) item_mapping.csv에는 있는데 trade_history_long.csv엔 아예 없는 품목
    2) trade_history_long.csv에는 있지만 관측치가 MIN_HEALTHY_MONTHS개월 미만인 품목
       (예: 다운로드 도중 시간 초과로 예전 스냅샷 몇 개만 남아있는 경우)
    두 경우 모두 실제 파일 상태만 보고 판단하며, 특정 품목명을 하드코딩하지 않는다.
    """
    counts = df.groupby("item_name").size()
    mapping_items = set(mapping["item_name"]) if not mapping.empty else set()
    data_items = set(counts.index)

    issues = []
    for item in sorted(mapping_items - data_items):
        issues.append({"item_name": item, "reason": "데이터 없음"})
    for item, n in counts.items():
        if n < MIN_HEALTHY_MONTHS:
            issues.append({"item_name": item, "reason": f"히스토리 {n}개월뿐 (수집 실패 의심)"})
    return issues


# ---------- favorites (로컬 전용 - Streamlit Cloud 등에 배포 시 파일시스템이 초기화되어 유지 안 됨) ----------
def load_favorites() -> set[str]:
    if not FAVORITES_PATH.exists():
        return set()
    try:
        data = json.loads(FAVORITES_PATH.read_text(encoding="utf-8"))
        return set(data.get("favorites", []))
    except Exception:
        return set()


def save_favorites(favorites: set[str]) -> None:
    FAVORITES_PATH.write_text(
        json.dumps({"favorites": sorted(favorites)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def toggle_favorite(item_name: str) -> set[str]:
    favorites = load_favorites()
    if item_name in favorites:
        favorites.discard(item_name)
    else:
        favorites.add(item_name)
    save_favorites(favorites)
    return favorites
