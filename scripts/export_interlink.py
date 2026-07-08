"""
수출입 대시보드(이 리포)의 데이터를, 별도 리포인 주식 리서치 대시보드가
GitHub raw URL로 그대로 소비할 수 있는 고정 스키마로 data/interlink/에 발행한다.

연계 계약 (두 리포 공통 - 임의 변경 금지):
  data/interlink/stock_trade_map.csv   종목코드,종목명,hs코드,품목명,카테고리,관계유형,비고
  data/interlink/trade_monthly.csv     hs코드,품목명,연월,수출금액_usd,수출yoy (최근 48개월)
  data/interlink/company_exports.csv   hs코드,품목명,기업명,종목코드,연월,수출금액_usd (기업별 데이터가 있는 품목만)
  data/interlink/meta.json             generated_at/data_through/row_counts
  data/interlink/quality_report.txt    품질 게이트에 걸린 행도 저장은 하되 사유를 기록

멱등성: 실행할 때마다 5개 파일을 통째로 재생성한다 (append 없음).
단독 실행: python scripts/export_interlink.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from utils_data import (  # noqa: E402
    compute_company_metrics,
    compute_item_metrics,
    load_company_history,
    load_history,
)
from stock_codes import resolve_stock_code  # noqa: E402

OUT_DIR = BASE_DIR / "data" / "interlink"
MAPPING_PATH = BASE_DIR / "config" / "item_mapping.csv"
DETAIL_PATH = BASE_DIR / "config" / "item_hscode_detail.csv"

TRADE_MONTHLY_MAX_MONTHS = 48
YOY_OUTLIER_ABS_PCT = 500.0
KST = timezone(timedelta(hours=9))
DELIM = "; "


class QualityReport:
    """품질 게이트에 걸린 행의 사유를 모은다. 걸려도 행 자체는 그대로 저장한다."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, section: str, message: str) -> None:
        self.lines.append(f"[{section}] {message}")

    def write(self, path: Path) -> None:
        header = [
            "수출입 대시보드 <-> 주식 리서치 대시보드 연계 데이터 품질 리포트",
            f"생성 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST",
            f"총 {len(self.lines)}건",
            "=" * 60,
        ]
        path.write_text("\n".join(header + self.lines) + "\n", encoding="utf-8-sig")


def _split_semicolon(raw: str) -> list[str]:
    return [c.strip() for c in str(raw or "").split(";") if c.strip()]


def _resolve_stock_code(company: str, qr: QualityReport, section: str) -> tuple[str, str]:
    """(종목코드 또는 빈 문자열, 비고) 반환. 실제 매핑/검증은 stock_codes.resolve_stock_code
    (utils_data.py의 관련 기업 딥링크와 공용)에 맡기고, 여기서는 확인 불가 사유를 quality_report에 남긴다."""
    code, note = resolve_stock_code(company)
    if not code and note:
        qr.add(section, f"{company}: 종목코드 확인 불가 - {note}")
    return code, note


def _validate_hs_code(hs_code: str, item_name: str, qr: QualityReport, section: str) -> str:
    code = str(hs_code or "").strip()
    if not code:
        return code
    if not code.isdigit():
        qr.add(section, f"{item_name}: HS코드가 숫자가 아님 ({code!r})")
        return code
    if len(code) not in (6, 10):
        qr.add(section, f"{item_name}: HS코드 길이가 6/10자리가 아님 ({code}, {len(code)}자리)")
    return code


# ---------- 품목 -> HS코드 목록 ----------
def build_item_hs_map(qr: QualityReport) -> dict[str, list[str]]:
    """item_name -> [hs_code, ...]. config/item_hscode_detail.csv(정제된 엑셀 워크북 유래,
    품목당 여러 행의 세부 HS코드)를 1순위로 쓰고, 거기 없는 품목은 item_mapping.csv의
    hs_code(세미콜론 join, 더 굵은 단위)로 보충한다."""
    item_hs: dict[str, list[str]] = {}

    if DETAIL_PATH.exists():
        detail = pd.read_csv(DETAIL_PATH, dtype=str).fillna("")
        for item_name, g in detail.groupby("item_name"):
            codes = []
            seen = set()
            for raw in g["hs_code"]:
                code = _validate_hs_code(raw, item_name, qr, "trade_monthly/hs_map")
                if code and code not in seen:
                    seen.add(code)
                    codes.append(code)
            if codes:
                item_hs[item_name] = codes

    # hs코드가 같은데 서로 다른 item_name에 걸쳐 있으면(1:N 충돌) 기록만 하고 그대로 둔다.
    code_to_items: dict[str, set[str]] = {}
    for item_name, codes in item_hs.items():
        for code in codes:
            code_to_items.setdefault(code, set()).add(item_name)
    for code, items in code_to_items.items():
        if len(items) > 1:
            qr.add("trade_monthly/hs_map", f"HS코드 {code}가 여러 품목에 걸침 (1:N 충돌): {sorted(items)}")

    if MAPPING_PATH.exists():
        mapping = pd.read_csv(MAPPING_PATH, dtype=str).fillna("")
        for _, row in mapping.iterrows():
            item_name = row["item_name"]
            if item_name in item_hs:
                continue
            codes = []
            seen = set()
            for raw in _split_semicolon(row.get("hs_code", "")):
                code = _validate_hs_code(raw, item_name, qr, "trade_monthly/hs_map")
                if code and code not in seen:
                    seen.add(code)
                    codes.append(code)
            if codes:
                item_hs[item_name] = codes

    return item_hs


# ---------- ① stock_trade_map.csv ----------
def build_stock_trade_map(item_hs_map: dict[str, list[str]], qr: QualityReport) -> pd.DataFrame:
    section = "stock_trade_map"
    if not MAPPING_PATH.exists():
        qr.add(section, f"{MAPPING_PATH.name}이 없어 stock_trade_map.csv를 빈 파일로 만듭니다.")
        return pd.DataFrame(columns=["종목코드", "종목명", "hs코드", "품목명", "카테고리", "관계유형", "비고"])

    mapping = pd.read_csv(MAPPING_PATH, dtype=str).fillna("")

    # (item_name, category, [company,...]) 원천 1: item_mapping.csv (참고용 매핑, 64개 품목 전체)
    item_company_rows: list[tuple[str, str, str]] = []  # (item_name, category, company)
    for _, row in mapping.iterrows():
        item_name, category = row["item_name"], row["category"]
        for company in _split_semicolon(row.get("related_companies", "")):
            item_company_rows.append((item_name, category, company))

    # 원천 2: company_trade_history_long.csv에만 있는 품목(아직 item_mapping.csv에 없는,
    # 기업별 커스텀 설정 추가 중인 품목) - 실측 데이터가 있는 만큼 관계가 더 확실하다.
    # 콤마로 묶인 복합 라벨("HD건설기계, 두산밥캣" 등)은 단일 기업으로 쪼갤 근거가 없어 제외한다.
    company_hist = load_company_history()
    mapped_items = set(mapping["item_name"])
    extra_items = sorted(set(company_hist["item_name"].unique()) - mapped_items) if not company_hist.empty else []
    for item_name in extra_items:
        category = item_name.split("_")[0]
        companies = sorted(company_hist.loc[company_hist["item_name"] == item_name, "company_name"].unique())
        for company in companies:
            if "," in company or company.strip().endswith("등"):
                qr.add(section, f"{item_name}: 복합 기업 라벨('{company}')이라 종목 매핑에서 제외")
                continue
            item_company_rows.append((item_name, category, company))

    # 관계유형: 종목코드가 매핑된 품목이 1개뿐이면 주력품목, 여러 개면 전부 관련품목
    # (item_mapping.csv/company_trade_history_long.csv 어디에도 주력/관련 구분 원천 데이터가 없어
    # 정한 규칙 - 사용자 확인 완료, 2026-07-08).
    resolved_cache: dict[str, tuple[str, str]] = {}
    stock_items: dict[str, set[str]] = {}
    for item_name, _category, company in item_company_rows:
        if company not in resolved_cache:
            resolved_cache[company] = _resolve_stock_code(company, qr, section)
        code, _note = resolved_cache[company]
        if code:
            stock_items.setdefault(code, set()).add(item_name)

    rows = []
    for item_name, category, company in item_company_rows:
        code, note = resolved_cache[company]
        relation = "주력품목" if code and len(stock_items.get(code, set())) == 1 else "관련품목"
        hs_codes = item_hs_map.get(item_name, [])
        if not hs_codes:
            qr.add(section, f"{item_name}: HS코드 없음 (hs코드 빈 값으로 저장)")
            hs_codes = [""]
        for hs_code in hs_codes:
            rows.append(
                {
                    "종목코드": code,
                    "종목명": company,
                    "hs코드": hs_code,
                    "품목명": item_name,
                    "카테고리": category,
                    "관계유형": relation,
                    "비고": note,
                }
            )

    df = pd.DataFrame(rows, columns=["종목코드", "종목명", "hs코드", "품목명", "카테고리", "관계유형", "비고"])
    return df.sort_values(["카테고리", "품목명", "종목명"]).reset_index(drop=True)


# ---------- ② trade_monthly.csv ----------
def build_trade_monthly(item_hs_map: dict[str, list[str]], qr: QualityReport) -> pd.DataFrame:
    section = "trade_monthly"
    history_df, _decade = load_history()
    metrics_df = compute_item_metrics(history_df)

    rows = []
    for item_name, g in metrics_df.groupby("item_name"):
        g = g.sort_values("date").tail(TRADE_MONTHLY_MAX_MONTHS)
        hs_codes = item_hs_map.get(item_name, [])
        if not hs_codes:
            qr.add(section, f"{item_name}: HS코드 없음 (hs코드 빈 값으로 저장)")
            hs_codes = [""]

        for _, r in g.iterrows():
            yearmonth = str(r["period"])
            if not re.match(r"^\d{4}-\d{2}$", yearmonth):
                qr.add(section, f"{item_name}: 연월 포맷 이상 ({yearmonth!r})")

            yoy = r["yoy"]
            if pd.notna(yoy) and abs(yoy) > YOY_OUTLIER_ABS_PCT:
                qr.add(section, f"{item_name} {yearmonth}: 수출yoy 이상치 ({yoy:+.1f}%, ±{YOY_OUTLIER_ABS_PCT:.0f}% 초과)")

            for hs_code in hs_codes:
                rows.append(
                    {
                        "hs코드": hs_code,
                        "품목명": item_name,
                        "연월": yearmonth,
                        "수출금액_usd": r["export_amount"],
                        "수출yoy": round(yoy, 1) if pd.notna(yoy) else "",
                    }
                )

    df = pd.DataFrame(rows, columns=["hs코드", "품목명", "연월", "수출금액_usd", "수출yoy"])
    return df.sort_values(["품목명", "hs코드", "연월"]).reset_index(drop=True)


# ---------- ③ company_exports.csv ----------
def build_company_exports(item_hs_map: dict[str, list[str]], qr: QualityReport) -> pd.DataFrame:
    section = "company_exports"
    company_df = load_company_history()
    if company_df.empty:
        return pd.DataFrame(columns=["hs코드", "품목명", "기업명", "종목코드", "연월", "수출금액_usd"])

    company_metrics = compute_company_metrics(company_df)
    resolved_cache: dict[str, tuple[str, str]] = {}

    rows = []
    for (item_name, company_name), g in company_metrics.groupby(["item_name", "company_name"]):
        hs_codes = item_hs_map.get(item_name, [])
        if not hs_codes:
            qr.add(section, f"{item_name}: HS코드 없음 (hs코드 빈 값으로 저장)")
            hs_codes = [""]

        is_combined_label = "," in company_name or company_name.strip().endswith("등")
        if is_combined_label:
            code = ""
        else:
            if company_name not in resolved_cache:
                resolved_cache[company_name] = _resolve_stock_code(company_name, qr, section)
            code, _note = resolved_cache[company_name]

        g = g.sort_values("date")
        for _, r in g.iterrows():
            yearmonth = str(r["period"]) if "period" in r and pd.notna(r.get("period")) else r["date"].strftime("%Y-%m")
            if not re.match(r"^\d{4}-\d{2}$", yearmonth):
                qr.add(section, f"{item_name}/{company_name}: 연월 포맷 이상 ({yearmonth!r})")
            for hs_code in hs_codes:
                rows.append(
                    {
                        "hs코드": hs_code,
                        "품목명": item_name,
                        "기업명": company_name,
                        "종목코드": code,
                        "연월": yearmonth,
                        "수출금액_usd": r["export_amount"],
                    }
                )

    df = pd.DataFrame(rows, columns=["hs코드", "품목명", "기업명", "종목코드", "연월", "수출금액_usd"])
    return df.sort_values(["품목명", "기업명", "hs코드", "연월"]).reset_index(drop=True)


# ---------- ④ meta.json ----------
def build_meta(trade_monthly: pd.DataFrame, row_counts: dict[str, int]) -> dict:
    data_through = trade_monthly["연월"].max() if not trade_monthly.empty else None
    return {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "data_through": data_through or "",
        "row_counts": row_counts,
    }


def main() -> None:
    qr = QualityReport()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    item_hs_map = build_item_hs_map(qr)
    stock_trade_map = build_stock_trade_map(item_hs_map, qr)
    trade_monthly = build_trade_monthly(item_hs_map, qr)
    company_exports = build_company_exports(item_hs_map, qr)

    stock_trade_map.to_csv(OUT_DIR / "stock_trade_map.csv", index=False, encoding="utf-8-sig")
    trade_monthly.to_csv(OUT_DIR / "trade_monthly.csv", index=False, encoding="utf-8-sig")
    company_exports.to_csv(OUT_DIR / "company_exports.csv", index=False, encoding="utf-8-sig")

    row_counts = {
        "stock_trade_map": len(stock_trade_map),
        "trade_monthly": len(trade_monthly),
        "company_exports": len(company_exports),
    }
    meta = build_meta(trade_monthly, row_counts)
    (OUT_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8-sig"
    )

    qr.write(OUT_DIR / "quality_report.txt")

    print(f"발행 완료: {OUT_DIR}")
    print(f"  stock_trade_map.csv   {row_counts['stock_trade_map']}행")
    print(f"  trade_monthly.csv     {row_counts['trade_monthly']}행")
    print(f"  company_exports.csv   {row_counts['company_exports']}행")
    print(f"  meta.json             data_through={meta['data_through']}")
    print(f"  quality_report.txt    {len(qr.lines)}건")
    print(f"DATA_THROUGH={meta['data_through']}")  # 배치 파이프라인이 커밋 메시지에 쓰려고 파싱하는 라인


if __name__ == "__main__":
    main()
