"""
scrape_company_breakdowns()를 실행해서 실제로 company_trade_history_long.csv에
저장하는 프로덕션 스크립트 (test_company_scrape.py는 저장하지 않는 드라이런용으로
남겨둔다).

품목 집계 데이터(trade_history_long.csv)는 이미 최신 상태이거나 별도 스케줄로
갱신되므로 이 스크립트는 건드리지 않는다. 전체 파이프라인(품목 집계 + 기업 세부)을
한 번에 갱신하려면 scrape_bigfinance.py를 실행한다 - 그 스크립트도 이제
scrape_company_breakdowns()를 자동으로 포함해서 실행한다.

실행: python run_company_scrape.py
"""

from playwright.sync_api import sync_playwright

from append_snapshot import append_company_snapshot
from scrape_bigfinance import PROFILE_DIR, scrape_company_breakdowns


def main() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            records = scrape_company_breakdowns(page)
        finally:
            context.close()

    if not records:
        print("[정보] 하위 기업이 설정된 품목이 없어 저장할 데이터가 없습니다.")
        return

    seen = {}
    for r in records:
        seen[(r["품목명"], r["기업명"], r["기준일"])] = r
    unique_records = list(seen.values())
    append_company_snapshot(unique_records)


if __name__ == "__main__":
    main()
