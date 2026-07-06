"""
품목 모달(차트+다운로드 아이콘) 구조를 확인하기 위한 1회성 탐색 스크립트.

scrape_bigfinance.py의 로그인/이동 로직을 재사용해서 목록의 첫 번째 품목
모달을 열고 스크린샷+HTML을 .auth/debug에 저장한다. 이 구조를 실제로 본 뒤
scrape_bigfinance.py에 항목별 다운로드 로직을 붙일 예정 (구조를 추측해서
먼저 구현하지 않기 위한 중간 단계).

실행: python probe_modal.py
"""

from pathlib import Path

from scrape_bigfinance import BASE_DIR, GRID_SELECTOR, PROFILE_DIR, _dump_debug, ensure_data_ready
from playwright.sync_api import sync_playwright

DOWNLOAD_PROBE_DIR = BASE_DIR / ".auth" / "debug" / "downloads"


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
            ensure_data_ready(page)

            body = page.locator(f"{GRID_SELECTOR}__body")
            first_row = body.locator(".label__columns table tbody tr").first
            item_name = first_row.locator(".group__item__name").first.inner_text().strip()
            print(f"[정보] 첫 번째 품목: {item_name}")

            first_row.hover()
            page.wait_for_timeout(300)
            first_row.locator(".button__modal").first.click(force=True)
            page.wait_for_timeout(2000)

            _dump_debug(page, "item_modal_probe")
            print("[정보] 모달을 열고 .auth/debug에 스크린샷/HTML을 저장했습니다.")

            # 수출금액/단가 전환 드롭다운의 실제 옵션 텍스트 확인
            modal = page.locator(".hs-codes-chart-modal")
            modal.locator(".data-selector .input-list-selector__control").first.click()
            page.wait_for_timeout(500)
            options = modal.locator('[class*="menu"] [class*="option"]')
            n_opts = options.count()
            print(f"[정보] 드롭다운 옵션 {n_opts}개:")
            for i in range(n_opts):
                print(f"  - {options.nth(i).inner_text().strip()!r}")
            _dump_debug(page, "item_modal_dropdown_probe")

            # 드롭다운 닫기 (Escape) 후 "All" 범위 선택, 다운로드 실행
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            modal.get_by_text("All", exact=True).first.click()
            page.wait_for_timeout(500)

            DOWNLOAD_PROBE_DIR.mkdir(parents=True, exist_ok=True)
            with page.expect_download() as download_info:
                modal.locator(".chart-module__download-chart").click()
            download = download_info.value
            save_path = DOWNLOAD_PROBE_DIR / f"probe_{download.suggested_filename}"
            download.save_as(str(save_path))
            print(f"[정보] 다운로드 파일 저장: {save_path} (제안된 파일명: {download.suggested_filename})")

            # 이번엔 '수출 단가'로 전환해서 다운로드
            modal.locator(".data-selector .input-list-selector__control").first.click()
            page.wait_for_timeout(500)
            modal.get_by_text("수출 단가", exact=True).first.click()
            page.wait_for_timeout(500)
            with page.expect_download() as download_info2:
                modal.locator(".chart-module__download-chart").click()
            download2 = download_info2.value
            save_path2 = DOWNLOAD_PROBE_DIR / f"probe_{download2.suggested_filename}"
            download2.save_as(str(save_path2))
            print(f"[정보] 다운로드 파일 저장: {save_path2} (제안된 파일명: {download2.suggested_filename})")

            input("확인했으면 이 창(터미널)에서 Enter를 눌러 종료...")
        finally:
            context.close()


if __name__ == "__main__":
    main()
