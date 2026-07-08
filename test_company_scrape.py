"""
scrape_bigfinance.py에 새로 추가한 scrape_company_breakdowns()만 격리해서 검증하는
드라이런 스크립트. CSV에는 아무것도 쓰지 않고 콘솔에 요약만 출력한다.

기존 scrape_all_items()(68개 품목 x 2회 다운로드)까지 같이 돌리면 오래 걸리므로,
새로 추가한 기업 순회 로직만 먼저 확인하기 위한 것.

실행: python test_company_scrape.py
"""

from playwright.sync_api import sync_playwright

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

    print(f"\n[결과] 총 {len(records)}개 레코드")
    items_with_data = sorted({(r["품목명"], r["기업명"]) for r in records})
    print(f"[결과] 품목-기업 조합 {len(items_with_data)}개:")
    for item_name, company_name in items_with_data:
        print(f"  - {item_name} / {company_name}")

    if records:
        print("\n[샘플] 처음 5개 레코드:")
        for r in records[:5]:
            print(f"  {r}")
        print("\n[샘플] 마지막 5개 레코드:")
        for r in records[-5:]:
            print(f"  {r}")


if __name__ == "__main__":
    main()
