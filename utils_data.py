"""
trade_history_long.csv 로딩/정규화, YoY/MoM 계산, item_mapping.csv/favorites.json
관리를 담당하는 유틸리티 모듈. app.py와 check_data.py가 공용으로 사용한다.

원칙: 파일에 없는 값(단가, 기업별/지역별 수출액 등)을 임의로 만들어내지 않는다.
컬럼이 없으면 해당 기능은 조용히 생략하거나 NaN을 반환한다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from stock_codes import resolve_stock_code

BASE_DIR = Path(__file__).parent
HISTORY_PATH = BASE_DIR / "trade_history_long.csv"
COMPANY_HISTORY_PATH = BASE_DIR / "company_trade_history_long.csv"
MAPPING_PATH = BASE_DIR / "config" / "item_mapping.csv"
FAVORITES_PATH = BASE_DIR / "favorites.json"
MAPPING_COLUMNS = ["item_name", "category", "related_companies", "hs_code"]

# company_trade_history_long.csv 컬럼 별칭. item_mapping.csv의 related_companies(참고용
# 텍스트, HS코드 매핑 137개 품목 전체)와는 다른 데이터다 - 이건 EPIC Finance "품목 및
# 지역 커스텀 설정"에서 하위 기업(지역) 행이 실제로 설정된 일부 품목에만 존재하는
# 실측 수출 데이터다 (scrape_bigfinance.py의 scrape_company_breakdowns() 참고).
COMPANY_COLUMN_ALIASES: dict[str, list[str]] = {
    "item_name": ["품목명", "item_name"],
    "company_name": ["기업명", "company_name"],
    "date": ["기준일", "date"],
    "export_amount": ["수출금액", "export_amount"],
    "unit_price": ["수출단가", "단가", "unit_price"],
}
COMPANY_REQUIRED_COLUMNS = ["item_name", "company_name", "date", "export_amount"]

# 최소 이 개월수 미만의 데이터만 있는 품목은 "수집이 제대로 안 된 것"으로 간주한다.
# 실제 관측치 기준: 정상 수집된 품목은 최소 54개월 이상, 수집 실패 품목은 3개월뿐이라
# 12개월(1년)을 기준으로 삼으면 명확히 구분된다. 특정 품목명을 하드코딩하지 않기 위한 일반 규칙.
MIN_HEALTHY_MONTHS = 12

# 자동 코멘트/시그널보드 판단 기준 (퍼센트). "강함/약함"을 가르는 임계값이라 조정 가능하게 상수로 뺌.
STRONG_YOY_PCT = 15.0
WEAK_YOY_PCT = 0.0

# 이 데이터는 bigfinance.co.kr의 "잠정 수출" 트래커(TRASS-BF)에서만 수집한다.
# 별도의 "확정치" 소스가 없어 전량 잠정치로 취급한다 (특정 행만 확정으로 표시할 근거가 없음).
DATA_SOURCE_LABEL = "BigFinance(bigfinance.co.kr) TRASS-BF 수출입 데이터"
DATA_IS_PRELIMINARY = True
# scrape_bigfinance.py를 등록한 Windows 작업 스케줄러 주기(10일)와 맞춘 값 - "다음 업데이트 예정" 추정에만 사용.
SCRAPE_INTERVAL_DAYS = 10

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
    """품목별로 정렬 후, 같은 (연,월) 기준으로 아래 지표를 다시 계산한다.
    원본에 YoY/MoM 컬럼이 있어도 무시하고 date/export_amount(+unit_price)로만 재계산한다.
    필요한 시점 데이터가 없으면 전부 NaN.

    - yoy/mom: 수출금액 YoY/MoM
    - export_volume: 수출물량 추정치 = 수출금액 / 단가 (단가 컬럼이 있을 때만; 직접 제공되는
      물량 컬럼이 없어 금액/단가로 역산한다 - 임의 추정이 아니라 두 실측값의 산술 변환)
    - volume_yoy: 수출물량 YoY
    - price_yoy: 수출단가 YoY
    - ma3_amount: 수출금액 3개월 이동평균
    - ma3_yoy: 3개월 이동평균 YoY
    - ma3_yoy_prev: 직전월의 ma3_yoy (추세 개선/저점통과 판단용)
    """
    has_price = "unit_price" in df.columns

    def _pct(by_period: dict, period, offset) -> float:
        base = by_period.get(period - offset)
        current = by_period.get(period)
        if base is None or pd.isna(base) or base == 0 or current is None or pd.isna(current):
            return np.nan
        return (current - base) / base * 100

    def _per_item(g: pd.DataFrame) -> pd.DataFrame:
        g = g.sort_values("date").copy()
        g["period"] = g["date"].dt.to_period("M")

        if has_price:
            g["export_volume"] = np.where(
                g["unit_price"].notna() & (g["unit_price"] != 0),
                g["export_amount"] / g["unit_price"],
                np.nan,
            )
        else:
            g["export_volume"] = np.nan
        g["ma3_amount"] = g["export_amount"].rolling(3, min_periods=3).mean()

        amount_bp = dict(zip(g["period"], g["export_amount"]))
        price_bp = dict(zip(g["period"], g["unit_price"])) if has_price else {}
        volume_bp = dict(zip(g["period"], g["export_volume"]))
        ma3_bp = dict(zip(g["period"], g["ma3_amount"]))

        g["yoy"] = g["period"].apply(lambda p: _pct(amount_bp, p, 12))
        g["mom"] = g["period"].apply(lambda p: _pct(amount_bp, p, 1))
        g["prev_month_amount"] = g["period"].apply(lambda p: amount_bp.get(p - 1))
        g["price_yoy"] = g["period"].apply(lambda p: _pct(price_bp, p, 12)) if has_price else np.nan
        g["volume_yoy"] = g["period"].apply(lambda p: _pct(volume_bp, p, 12))
        g["ma3_yoy"] = g["period"].apply(lambda p: _pct(ma3_bp, p, 12))

        ma3_yoy_bp = dict(zip(g["period"], g["ma3_yoy"]))
        g["ma3_yoy_prev"] = g["period"].apply(lambda p: ma3_yoy_bp.get(p - 1))
        return g

    parts = [_per_item(g) for _, g in df.groupby("item_name")]
    return pd.concat(parts, ignore_index=True)


def get_latest_snapshot(df_with_metrics: pd.DataFrame) -> pd.DataFrame:
    """품목별 최신 시점 1행만 추출 (카드/요약용)."""
    latest = df_with_metrics.sort_values("date").groupby("item_name", as_index=False).tail(1)
    return latest.reset_index(drop=True)


# ---------- company_trade_history_long.csv (기업별 수출 - 일부 품목만 존재) ----------
_COMPANY_EMPTY_COLUMNS = ["item_name", "company_name", "date", "export_amount", "unit_price"]


def load_company_history() -> pd.DataFrame:
    """company_trade_history_long.csv를 로딩/정규화한다.

    EPIC Finance "품목 및 지역 커스텀 설정"에서 하위 기업(지역) 행이 실제로 설정된
    품목만 데이터가 존재한다 - 137개 품목 중 일부뿐이다. 파일이 없거나 비어 있어도
    에러가 아니라 "이 자산운용사가 아직 기업별 커스텀 설정을 하지 않았다"는 정상
    상태이므로, load_history()와 달리 DataLoadError를 던지지 않고 조용히 빈
    DataFrame을 반환한다 (호출부가 품목별 기업 카드 섹션을 그냥 숨기면 된다).
    """
    empty = pd.DataFrame(columns=_COMPANY_EMPTY_COLUMNS)
    if not COMPANY_HISTORY_PATH.exists():
        return empty
    try:
        raw = pd.read_csv(COMPANY_HISTORY_PATH)
    except Exception:
        return empty
    if raw.empty:
        return empty

    rename_map = {}
    for canonical, aliases in COMPANY_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in raw.columns and canonical not in rename_map.values():
                rename_map[alias] = canonical
                break
    df = raw.rename(columns=rename_map)

    if any(c not in df.columns for c in COMPANY_REQUIRED_COLUMNS):
        return empty

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["export_amount"] = clean_numeric(df["export_amount"])
    if "unit_price" in df.columns:
        df["unit_price"] = clean_numeric(df["unit_price"])
    df = df.dropna(subset=["date", "item_name", "company_name"]).copy()
    df = df.sort_values(["item_name", "company_name", "date"]).reset_index(drop=True)
    return df


def compute_company_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """(품목, 기업) 조합별로 정렬 후 YoY/MoM 등을 계산한다.
    compute_item_metrics와 동일한 계산식이지만 item_name 단독이 아니라
    (item_name, company_name) 조합 단위로 그룹핑한다."""
    if df.empty:
        return df.assign(
            period=pd.Series(dtype="object"),
            export_volume=pd.Series(dtype="float64"),
            ma3_amount=pd.Series(dtype="float64"),
            yoy=pd.Series(dtype="float64"),
            mom=pd.Series(dtype="float64"),
            price_yoy=pd.Series(dtype="float64"),
            volume_yoy=pd.Series(dtype="float64"),
            ma3_yoy=pd.Series(dtype="float64"),
        )

    has_price = "unit_price" in df.columns

    def _pct(by_period: dict, period, offset) -> float:
        base = by_period.get(period - offset)
        current = by_period.get(period)
        if base is None or pd.isna(base) or base == 0 or current is None or pd.isna(current):
            return np.nan
        return (current - base) / base * 100

    def _per_group(g: pd.DataFrame) -> pd.DataFrame:
        g = g.sort_values("date").copy()
        g["period"] = g["date"].dt.to_period("M")

        if has_price:
            g["export_volume"] = np.where(
                g["unit_price"].notna() & (g["unit_price"] != 0),
                g["export_amount"] / g["unit_price"],
                np.nan,
            )
        else:
            g["export_volume"] = np.nan
        g["ma3_amount"] = g["export_amount"].rolling(3, min_periods=3).mean()

        amount_bp = dict(zip(g["period"], g["export_amount"]))
        price_bp = dict(zip(g["period"], g["unit_price"])) if has_price else {}
        volume_bp = dict(zip(g["period"], g["export_volume"]))
        ma3_bp = dict(zip(g["period"], g["ma3_amount"]))

        g["yoy"] = g["period"].apply(lambda p: _pct(amount_bp, p, 12))
        g["mom"] = g["period"].apply(lambda p: _pct(amount_bp, p, 1))
        g["price_yoy"] = g["period"].apply(lambda p: _pct(price_bp, p, 12)) if has_price else np.nan
        g["volume_yoy"] = g["period"].apply(lambda p: _pct(volume_bp, p, 12))
        g["ma3_yoy"] = g["period"].apply(lambda p: _pct(ma3_bp, p, 12))
        return g

    parts = [_per_group(g) for _, g in df.groupby(["item_name", "company_name"])]
    return pd.concat(parts, ignore_index=True)


def get_company_breakdown(company_metrics_df: pd.DataFrame, item_name: str) -> pd.DataFrame:
    """특정 품목의 기업별 최신 시점 데이터(카드 섹션용)만 반환.
    하위 기업 데이터가 없는 품목이면 빈 DataFrame - 호출부는 이 경우 카드 섹션
    자체를 렌더링하지 않아야 한다."""
    if company_metrics_df.empty:
        return company_metrics_df
    item_view = company_metrics_df[company_metrics_df["item_name"] == item_name]
    if item_view.empty:
        return item_view
    latest = item_view.sort_values("date").groupby("company_name", as_index=False).tail(1)
    return latest.reset_index(drop=True)


def get_company_history(company_metrics_df: pd.DataFrame, item_name: str, company_name: str) -> pd.DataFrame:
    """기업 상세 화면용 - 해당 품목x기업의 전체 월별 시계열."""
    if company_metrics_df.empty:
        return company_metrics_df
    view = company_metrics_df[
        (company_metrics_df["item_name"] == item_name) & (company_metrics_df["company_name"] == company_name)
    ]
    return view.sort_values("date").reset_index(drop=True)


def get_company_latest_snapshot(company_metrics_df: pd.DataFrame) -> pd.DataFrame:
    """(품목, 기업) 조합별 최신 시점 1행만 추출 - 기업 검색 결과 카드용."""
    if company_metrics_df.empty:
        return company_metrics_df
    latest = company_metrics_df.sort_values("date").groupby(["item_name", "company_name"], as_index=False).tail(1)
    return latest.reset_index(drop=True)


def search_company_data(company_metrics_df: pd.DataFrame, query: str) -> pd.DataFrame:
    """company_trade_history_long.csv 기반 실제 수출 데이터에서 기업명을 검색한다
    (부분일치, 대소문자 무시). 결과가 없으면 빈 DataFrame - 호출부가 "실제 데이터 없음"으로
    처리해야 한다."""
    latest = get_company_latest_snapshot(company_metrics_df)
    if latest.empty or not query.strip():
        return latest.iloc[0:0]
    q = query.strip().lower()
    return latest[latest["company_name"].str.lower().str.contains(q, na=False, regex=False)].reset_index(drop=True)


def search_related_company_items(mapping_df: pd.DataFrame, query: str) -> list[dict]:
    """item_mapping.csv의 related_companies(참고용 텍스트, 실제 수출 데이터 아님)에서
    검색어를 포함하는 기업이 언급된 품목 목록을 반환한다. HS코드 매핑 137개 품목 전체가
    대상이라 search_company_data보다 훨씬 폭넓게 매칭될 수 있다."""
    if not query.strip() or mapping_df.empty:
        return []
    q = query.strip().lower()
    results = []
    for _, row in mapping_df.iterrows():
        raw = str(row.get("related_companies", "") or "")
        companies = [c.strip() for c in re.split(r"[;,]", raw) if c.strip()]
        matched = [c for c in companies if q in c.lower()]
        if matched:
            results.append(
                {"item_name": row["item_name"], "category": row.get("category", ""), "matched_companies": matched}
            )
    return results


# ---------- item_mapping.csv ----------
def load_item_mapping(df: pd.DataFrame) -> pd.DataFrame:
    """config/item_mapping.csv를 로딩한다.
    - 파일이 없으면: 현재 데이터의 품목으로 새로 만든다 (category=품목명 첫 '_' 앞부분,
      related_companies/hs_code는 빈 값 - 실제 매핑 정보가 없어 임의로 채우지 않는다).
    - 파일이 있으면: 거기 없는 신규 품목만 추가하고, 기존 행은 절대 덮어쓰지 않는다.
    - hs_code 컬럼이 옛 파일에 없으면(이전 버전에서 만든 파일) 빈 값으로 추가만 한다
      (기존 item_name/category/related_companies 값은 그대로 유지).
    """
    MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    current_items = sorted(df["item_name"].dropna().unique().tolist())

    if MAPPING_PATH.exists():
        mapping = pd.read_csv(MAPPING_PATH, dtype=str).fillna("")
        changed = False
        for col in MAPPING_COLUMNS:
            if col not in mapping.columns:
                mapping[col] = ""
                changed = True

        existing_items = set(mapping["item_name"])
        new_items = [i for i in current_items if i not in existing_items]
        if new_items:
            new_rows = pd.DataFrame(
                {
                    "item_name": new_items,
                    "category": [i.split("_")[0] for i in new_items],
                    "related_companies": ["" for _ in new_items],
                    "hs_code": ["" for _ in new_items],
                }
            )
            mapping = pd.concat([mapping, new_rows], ignore_index=True)
            changed = True

        if changed:
            mapping = mapping[MAPPING_COLUMNS]
            mapping.to_csv(MAPPING_PATH, index=False)
        return mapping

    mapping = pd.DataFrame(
        {
            "item_name": current_items,
            "category": [i.split("_")[0] for i in current_items],
            "related_companies": ["" for _ in current_items],
            "hs_code": ["" for _ in current_items],
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


def get_hs_code(mapping: pd.DataFrame, item_name: str) -> str:
    """HS 코드 매핑은 아직 정리되지 않아 대부분 빈 값이다. 임의로 채우지 않는다."""
    if "hs_code" not in mapping.columns:
        return ""
    row = mapping[mapping["item_name"] == item_name]
    if row.empty:
        return ""
    return str(row.iloc[0].get("hs_code", "") or "").strip()


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


# ---------- 데이터 기준 정보 (모든 화면 상단에 고정 표시) ----------
def get_data_status(df: pd.DataFrame, missing_items: list[dict]) -> dict:
    """데이터 기준월/잠정치 여부/마지막 업데이트 시각/출처/다음 업데이트 예정일을 계산.
    '마지막 업데이트'는 별도 로그가 없어 trade_history_long.csv의 실제 파일 수정 시각을 쓴다
    (파일이 실제로 언제 갱신됐는지를 그대로 반영하는 값이라 임의 추정이 아니다).
    '다음 업데이트 예정'은 작업 스케줄러 주기(10일)를 더한 추정치일 뿐, 확정 일정이 아니다."""
    latest_period = df["date"].max().to_period("M")
    last_updated = datetime.fromtimestamp(HISTORY_PATH.stat().st_mtime) if HISTORY_PATH.exists() else None
    next_update_estimate = (last_updated + timedelta(days=SCRAPE_INTERVAL_DAYS)) if last_updated else None
    return {
        "latest_period": str(latest_period),
        "is_preliminary": DATA_IS_PRELIMINARY,
        "last_updated": last_updated,
        "source_label": DATA_SOURCE_LABEL,
        "next_update_estimate": next_update_estimate,
        "missing_count": len(missing_items),
    }


# ---------- Top N / 자동 코멘트 ----------
def get_top_n(latest_df: pd.DataFrame, col: str, n: int = 10, ascending: bool = False) -> pd.DataFrame:
    """latest_df(품목별 최신 시점)를 col 기준으로 정렬해 상위 n개를 반환. NaN은 항상 제외."""
    valid = latest_df[latest_df[col].notna()]
    return valid.sort_values(col, ascending=ascending).head(n)


def _fmt_signed(v: float) -> str:
    return f"{v:+.1f}%" if pd.notna(v) else "N/A"


def generate_comment(row: pd.Series) -> str:
    """규칙 기반 자동 코멘트. row는 compute_item_metrics() 결과의 한 행(보통 최신 시점)이어야 한다.
    yoy/ma3_yoy/price_yoy/volume_yoy가 없으면(NaN) 해당 판단은 건너뛰고 보수적으로 서술한다."""
    yoy = row.get("yoy")
    ma3_yoy = row.get("ma3_yoy")
    ma3_yoy_prev = row.get("ma3_yoy_prev")
    price_yoy = row.get("price_yoy")
    volume_yoy = row.get("volume_yoy")

    if pd.isna(yoy):
        return "최근월 수출금액 YoY를 계산할 전년동월 데이터가 없어 판단을 보류합니다."

    if pd.notna(ma3_yoy) and yoy >= STRONG_YOY_PCT and ma3_yoy < WEAK_YOY_PCT:
        headline = (
            f"최근월 수출금액은 YoY {_fmt_signed(yoy)}로 급증했지만, 3개월 이동평균 기준으로는 "
            f"YoY {_fmt_signed(ma3_yoy)}에 그쳐 일시적 급증 가능성이 있습니다. 지속성 확인이 필요합니다."
        )
    elif pd.notna(ma3_yoy) and yoy >= STRONG_YOY_PCT and ma3_yoy >= STRONG_YOY_PCT:
        headline = (
            f"최근월 수출금액은 YoY {_fmt_signed(yoy)} 증가했고, 3개월 이동평균 기준으로도 "
            f"YoY {_fmt_signed(ma3_yoy)}를 기록해 추세 개선이 확인됩니다."
        )
    elif (
        yoy < WEAK_YOY_PCT
        and pd.notna(ma3_yoy)
        and pd.notna(ma3_yoy_prev)
        and ma3_yoy > ma3_yoy_prev
        and ma3_yoy >= WEAK_YOY_PCT
    ):
        headline = (
            f"최근월 수출금액은 YoY {_fmt_signed(yoy)}로 약하지만, 3개월 이동평균 YoY가 "
            f"{_fmt_signed(ma3_yoy_prev)}에서 {_fmt_signed(ma3_yoy)}로 개선되고 있어 저점 통과 가능성이 있습니다."
        )
    elif (
        yoy < WEAK_YOY_PCT
        and pd.notna(price_yoy)
        and pd.notna(volume_yoy)
        and price_yoy < WEAK_YOY_PCT
        and volume_yoy < WEAK_YOY_PCT
    ):
        headline = (
            f"최근월 수출금액은 YoY {_fmt_signed(yoy)}이고 수출단가(YoY {_fmt_signed(price_yoy)})와 "
            f"수출물량(YoY {_fmt_signed(volume_yoy)}) 모두 둔화되어 업황 둔화 가능성이 있습니다."
        )
    else:
        headline = f"최근월 수출금액은 YoY {_fmt_signed(yoy)}를 기록했습니다."

    decomposition = ""
    if pd.notna(price_yoy):
        if yoy >= STRONG_YOY_PCT and price_yoy < WEAK_YOY_PCT:
            decomposition = (
                f" 수출단가는 YoY {_fmt_signed(price_yoy)}로 하락해 물량 중심의 성장으로 보이며, "
                "기업 실적 추정 시 마진 개선 여부는 추가 확인이 필요합니다."
            )
        elif yoy >= STRONG_YOY_PCT and price_yoy >= STRONG_YOY_PCT:
            decomposition = (
                f" 수출단가도 YoY {_fmt_signed(price_yoy)} 상승해 단순 물량 증가뿐 아니라 "
                "ASP 또는 믹스 개선 가능성이 있습니다."
            )
        elif price_yoy >= STRONG_YOY_PCT:
            decomposition = f" 수출단가가 YoY {_fmt_signed(price_yoy)} 상승 중이어서 ASP 상승 흐름을 주시할 필요가 있습니다."
        elif price_yoy < WEAK_YOY_PCT:
            decomposition = f" 수출단가는 YoY {_fmt_signed(price_yoy)}로 하락세입니다."

    return (headline + decomposition).strip()


# ---------- 투자 시그널 보드 - Signal Score / 해석 태그 (Phase 2) ----------
# generate_comment()와 같은 성격의 "이미 계산된 지표를 해석만 하는" 순수 추가 함수들.
# compute_item_metrics() 등 원본 계산 로직은 건드리지 않는다.
STRONG_MOM_PCT = 30.0  # "단기 급등" 판정용 MoM 임계값
SMALL_AMOUNT_PERCENTILE = 0.2  # "노이즈 가능" 판정 - 최근월 수출액이 전체 품목 중 하위 몇 %인지
HIGH_YOY_NOISE_PCT = 100.0  # "노이즈 가능" 판정 - 이 YoY(%)를 넘으면 "과도하게 높다"로 간주

SIGNAL_SCORE_WEIGHTS = {"yoy": 0.35, "mom": 0.25, "ma3_yoy": 0.25, "price_yoy": 0.15}
NEUTRAL_RANK_PCT = 50.0  # 결측/inf는 순위 중간값으로 대체해 점수를 왜곡하지 않는다.

TAG_NEGATIVE_TURN = "마이너스 전환"
TAG_NOISE = "노이즈 가능"
TAG_CHECK_NEEDED = "확인 필요"
TAG_SHORT_SPIKE = "단기 급등"
TAG_TREND_IMPROVING = "추세 개선"
TAG_PRICE_DRIVEN = "단가 주도"
TAG_VOLUME_DRIVEN = "물량 주도"
TAG_NONE = "–"

# 화면에 태그 범례로 그대로 노출하는 설명 - _classify_tag()의 실제 판정 순서/조건과
# 반드시 같은 순서로 유지한다 (표시용 텍스트일 뿐 판정 로직은 아님).
TAG_DESCRIPTIONS: dict[str, str] = {
    TAG_NEGATIVE_TURN: "YoY 또는 MoM이 마이너스로 전환된 품목 (주의 필요)",
    TAG_NOISE: "최근월 수출액이 하위 20% 수준으로 작은데 YoY가 100%를 넘어 통계적 착시일 수 있는 품목",
    TAG_CHECK_NEEDED: "단가 YoY와 물량 YoY의 방향이 서로 엇갈려 원인 확인이 필요한 품목",
    TAG_SHORT_SPIKE: "이번 달 MoM은 크게 뛰었지만 3개월 평균 YoY로는 아직 추세로 확인되지 않은 품목",
    TAG_TREND_IMPROVING: "YoY·3개월 평균 YoY·MoM이 모두 플러스로, 추세 개선이 뚜렷한 품목",
    TAG_PRICE_DRIVEN: "수출 증가가 물량보다 단가(ASP) 상승에서 주로 비롯된 품목",
    TAG_VOLUME_DRIVEN: "수출 증가가 단가보다 물량 증가에서 주로 비롯된 품목",
    TAG_NONE: "위 조건에 뚜렷하게 해당하지 않는 품목",
}


def _percentile_rank(series: pd.Series) -> pd.Series:
    """0~100 백분위 순위. 극단치(YoY +5000% 등)가 점수를 왜곡하지 않도록 raw 값 대신
    순위를 쓴다. 결측/inf는 중립값(50)으로 대체해 계산이 깨지지 않게 한다."""
    cleaned = series.replace([np.inf, -np.inf], np.nan)
    ranked = cleaned.rank(pct=True) * 100
    return ranked.fillna(NEUTRAL_RANK_PCT)


def _classify_tag(row: pd.Series, small_amount_threshold: float) -> str:
    """단순 규칙 기반 해석 태그 판정. 경고성 태그(마이너스 전환/노이즈 가능/확인 필요)를
    먼저 체크하고, 없으면 추세/기여도 태그를 판정한다. 조건에 필요한 값이 NaN이면
    해당 규칙은 건너뛴다(에러 대신 다음 규칙으로)."""
    yoy, mom, ma3_yoy = row.get("yoy"), row.get("mom"), row.get("ma3_yoy")
    price_yoy, volume_yoy, amount = row.get("price_yoy"), row.get("volume_yoy"), row.get("export_amount")

    if (pd.notna(yoy) and yoy < 0) or (pd.notna(mom) and mom < 0):
        return TAG_NEGATIVE_TURN

    if (
        pd.notna(amount)
        and pd.notna(yoy)
        and pd.notna(small_amount_threshold)
        and amount <= small_amount_threshold
        and yoy > HIGH_YOY_NOISE_PCT
    ):
        return TAG_NOISE

    if pd.notna(price_yoy) and pd.notna(volume_yoy):
        if (price_yoy > 0 and volume_yoy < 0) or (price_yoy < 0 and volume_yoy > 0):
            return TAG_CHECK_NEEDED

    if pd.notna(mom) and pd.notna(ma3_yoy) and mom > STRONG_MOM_PCT and ma3_yoy < STRONG_YOY_PCT:
        return TAG_SHORT_SPIKE

    if pd.notna(yoy) and pd.notna(ma3_yoy) and pd.notna(mom) and yoy > 0 and ma3_yoy > 0 and mom > 0:
        return TAG_TREND_IMPROVING

    if pd.notna(price_yoy) and pd.notna(volume_yoy) and price_yoy > volume_yoy and price_yoy > 0:
        return TAG_PRICE_DRIVEN

    if pd.notna(volume_yoy) and pd.notna(price_yoy) and volume_yoy > price_yoy and volume_yoy > 0:
        return TAG_VOLUME_DRIVEN

    return TAG_NONE


def enrich_signal_board(latest_df: pd.DataFrame) -> pd.DataFrame:
    """투자 시그널 보드용 signal_score(0~100)와 해석 태그(tag) 컬럼을 추가한 복사본을
    반환한다. latest_df는 품목별 최신 시점 1행(예: get_latest_snapshot 결과)이어야 한다."""
    if latest_df.empty:
        return latest_df.assign(
            signal_score=pd.Series(dtype="float64"),
            tag=pd.Series(dtype="object"),
        )

    df = latest_df.copy()
    weighted = pd.Series(0.0, index=df.index)
    for col, weight in SIGNAL_SCORE_WEIGHTS.items():
        rank = _percentile_rank(df[col]) if col in df.columns else pd.Series(NEUTRAL_RANK_PCT, index=df.index)
        weighted = weighted + rank * weight
    df["signal_score"] = weighted.round(1)

    amount_cleaned = df["export_amount"].replace([np.inf, -np.inf], np.nan)
    small_amount_threshold = amount_cleaned.quantile(SMALL_AMOUNT_PERCENTILE) if amount_cleaned.notna().any() else np.nan
    df["tag"] = df.apply(lambda r: _classify_tag(r, small_amount_threshold), axis=1)
    return df


# ---------- 품목 상세 페이지 - 투자 해석 박스 / 관련 기업 테이블 (Phase 3) ----------
DETAIL_COMPANY_MENTION_LIMIT = 2  # 투자 해석 문장에 언급할 관련 기업 최대 개수


def generate_detail_commentary(row: pd.Series, related_companies: list[str]) -> str:
    """품목 상세 페이지의 "투자 해석" 박스용 문장 생성. generate_comment()(카드/테이블의
    짧은 자동 코멘트)와는 별도 함수 - 관련 기업명까지 포함해 더 긴 서술을 만든다.
    데이터 값에 따라 문장이 자동으로 바뀌고, 필요한 값이 NaN이면 해당 부분은 생략한다."""
    yoy = row.get("yoy")
    ma3_yoy = row.get("ma3_yoy")
    price_yoy = row.get("price_yoy")
    volume_yoy = row.get("volume_yoy")

    if pd.isna(yoy):
        return "최근월 수출금액 YoY를 계산할 전년동월 데이터가 없어 투자 해석을 보류합니다."

    direction = "증가했고" if yoy >= 0 else "감소했고"
    headline = f"최근월 수출금액은 YoY {_fmt_signed(yoy)} {direction}"
    if pd.notna(ma3_yoy):
        if yoy >= STRONG_YOY_PCT and ma3_yoy < WEAK_YOY_PCT:
            headline += f", 다만 3개월 이동평균 기준으로는 YoY {_fmt_signed(ma3_yoy)}에 그쳐 일시적 급증 가능성이 있습니다."
        elif ma3_yoy >= WEAK_YOY_PCT:
            headline += f", 3개월 이동평균 기준으로도 YoY {_fmt_signed(ma3_yoy)}를 기록해 추세 개선이 확인됩니다."
        else:
            headline += f", 3개월 이동평균 기준으로는 YoY {_fmt_signed(ma3_yoy)}로 추세 둔화가 우려됩니다."
    else:
        headline += "."

    decomposition = ""
    if pd.notna(price_yoy) and pd.notna(volume_yoy):
        price_word = "상승" if price_yoy >= 0 else "하락"
        volume_word = "증가" if volume_yoy >= 0 else "감소"
        if abs(price_yoy) >= abs(volume_yoy) * 1.3 and abs(price_yoy) > 5:
            interp = "가격(ASP) 중심으로 해석됩니다"
        elif abs(volume_yoy) >= abs(price_yoy) * 1.3 and abs(volume_yoy) > 5:
            interp = "가격보다는 물량 중심으로 해석됩니다"
        else:
            interp = "가격과 물량이 함께 기여한 것으로 해석됩니다"
        trend_word = "성장" if yoy >= 0 else "감소"
        decomposition = (
            f" 수출단가는 YoY {_fmt_signed(price_yoy)}로 {price_word}했지만 물량은 YoY {_fmt_signed(volume_yoy)} "
            f"{volume_word}해, 현재 {trend_word}은 {interp}."
        )
    elif pd.notna(price_yoy):
        decomposition = f" 수출단가는 YoY {_fmt_signed(price_yoy)}입니다 (물량 데이터 없음)."

    company_note = ""
    if related_companies:
        shown = related_companies[:DETAIL_COMPANY_MENTION_LIMIT]
        suffix = " 등" if len(related_companies) > DETAIL_COMPANY_MENTION_LIMIT else ""
        company_note = f" 관련 기업 중 {', '.join(shown)}{suffix}의 해외 매출 흐름을 추가 확인할 필요가 있습니다."

    return (headline + decomposition + company_note).strip()


def build_related_company_table(
    item_name: str, mapping_df: pd.DataFrame, company_metrics_df: pd.DataFrame, item_export_amount: float
) -> pd.DataFrame:
    """품목 상세 페이지의 "관련 기업" 테이블용 데이터.
    item_mapping.csv의 참고용 관련 기업(A) 전체를 행으로 두고, company_trade_history_long.csv에
    실측 데이터(B)가 있는 기업만 최근월 수출액/YoY/MoM을 채운다. 실측이 없으면 값은 NaN이고
    note에 "실측 데이터 없음"으로 표시한다 (임의로 값을 채우지 않는다).
    related_items 컬럼은 해당 기업이 관련된 전체 품목 목록(현재 품목 포함)을 빠짐없이 보여준다.
    stock_code 컬럼(주식 리서치 대시보드 딥링크용)은 stock_codes.resolve_stock_code()로 채우며,
    확인 불가한 기업은 빈 값 - 임의로 만들어내지 않는다."""
    companies = get_related_companies(mapping_df, item_name)
    if not companies:
        return pd.DataFrame(
            columns=["company_name", "related_items", "export_amount", "yoy", "mom", "note", "stock_code"]
        )

    company_latest = (
        get_company_latest_snapshot(company_metrics_df) if not company_metrics_df.empty else company_metrics_df
    )

    rows = []
    for company in companies:
        hits = search_related_company_items(mapping_df, company)
        related_item_names = sorted({h["item_name"] for h in hits if company in h["matched_companies"]})
        if not related_item_names:
            related_item_names = [item_name]

        real_row = None
        if company_latest is not None and not company_latest.empty:
            match = company_latest[
                (company_latest["item_name"] == item_name) & (company_latest["company_name"] == company)
            ]
            if not match.empty:
                real_row = match.iloc[0]

        if real_row is not None:
            amount, yoy, mom = real_row["export_amount"], real_row["yoy"], real_row["mom"]
            if pd.notna(amount) and item_export_amount:
                ratio = amount / item_export_amount * 100
                note = f"이 품목 수출의 약 {ratio:.0f}% 차지 (실측)"
            else:
                note = "실측 데이터 있음"
        else:
            amount, yoy, mom = np.nan, np.nan, np.nan
            note = "실측 데이터 없음 - 참고용 매핑"

        stock_code, _stock_note = resolve_stock_code(company)

        rows.append(
            {
                "company_name": company,
                "related_items": ", ".join(related_item_names),
                "export_amount": amount,
                "yoy": yoy,
                "mom": mom,
                "note": note,
                "stock_code": stock_code,
            }
        )
    return pd.DataFrame(rows)


# ---------- Watchlist - 알림 사유 (Phase 5) ----------
# _classify_tag(해석 태그)와는 다른 관점 - Watchlist는 "이번 달에 왜 다시 봐야 하는지"에
# 초점을 맞춘 판정이라 임계값/우선순위를 별도로 둔다. 기존 계산 로직(compute_item_metrics 등)은
# 건드리지 않고 이미 계산된 ma3_yoy_prev 등을 그대로 재사용한다.
ALERT_MOM_DROP_PCT = -15.0  # "MoM 급락" 판정 임계값

ALERT_MOM_CRASH = "MoM 급락"
ALERT_YOY_SLOWDOWN = "YoY 둔화"
ALERT_SURGE = "수출액 급증"
ALERT_TREND_IMPROVING = "3개월 추세 개선"
ALERT_PRICE_TURN = "단가 상승 전환"
ALERT_VOLUME_REBOUND = "물량 반등"
ALERT_CHECK_NEEDED = "확인 필요"


def classify_alert_reason(row: pd.Series) -> str:
    """Watchlist 표의 "알림 사유" 판정. 위에서부터 먼저 맞는 조건 하나만 채택한다."""
    yoy, mom = row.get("yoy"), row.get("mom")
    ma3_yoy, ma3_yoy_prev = row.get("ma3_yoy"), row.get("ma3_yoy_prev")
    price_yoy, volume_yoy = row.get("price_yoy"), row.get("volume_yoy")

    if pd.isna(yoy):
        return ALERT_CHECK_NEEDED

    if pd.notna(mom) and mom <= ALERT_MOM_DROP_PCT:
        return ALERT_MOM_CRASH

    if pd.notna(ma3_yoy) and pd.notna(ma3_yoy_prev) and ma3_yoy < ma3_yoy_prev and yoy < STRONG_YOY_PCT:
        return ALERT_YOY_SLOWDOWN

    if yoy >= STRONG_YOY_PCT:
        return ALERT_SURGE

    if pd.notna(ma3_yoy) and pd.notna(ma3_yoy_prev) and ma3_yoy > WEAK_YOY_PCT and ma3_yoy > ma3_yoy_prev:
        return ALERT_TREND_IMPROVING

    if pd.notna(price_yoy) and price_yoy > WEAK_YOY_PCT and (pd.isna(volume_yoy) or volume_yoy <= WEAK_YOY_PCT):
        return ALERT_PRICE_TURN

    if pd.notna(volume_yoy) and pd.notna(mom) and volume_yoy > WEAK_YOY_PCT and mom > WEAK_YOY_PCT:
        return ALERT_VOLUME_REBOUND

    return ALERT_CHECK_NEEDED


def build_pm_summary(latest_df: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    """PM Summary 다운로드용 요약 테이블. 기업/섹터/컨센서스 등은 매핑 데이터가 정리되면 추가."""
    rows = []
    for _, r in latest_df.iterrows():
        rows.append(
            {
                "기준월": str(r["period"]),
                "품목명": r["item_name"],
                "HS코드": get_hs_code(mapping, r["item_name"]),
                "최근월_수출금액": r["export_amount"],
                "수출금액_MoM(%)": r["mom"],
                "수출금액_YoY(%)": r["yoy"],
                "3개월이동평균_YoY(%)": r["ma3_yoy"],
                "수출단가_YoY(%)": r["price_yoy"],
                "수출물량_YoY(%)": r["volume_yoy"],
                "잠정확정여부": "잠정치" if DATA_IS_PRELIMINARY else "확정치",
                "자동코멘트": generate_comment(r),
            }
        )
    return pd.DataFrame(rows)


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
