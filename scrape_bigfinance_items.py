"""
bigfinance.co.kr(EPIC Finance) "잠정 수출 품목 리스트"("품목 커스텀 설정") 화면에서
품목별로 모달(차트)을 열어 "수출 금액"/"수출 단가"를 전체 기간(All) 엑셀로 다운로드하고,
그 히스토리를 trade_history_decade_long.csv에 누적하는 스크립트.

scrape_bigfinance.py("품목 및 지역 커스텀 설정" 화면 스크래퍼)와 로그인/모달 다운로드
로직을 그대로 공유한다(중복 구현 금지) - 이 파일에서 새로 정의하는 건 화면 식별
정보(메뉴 라벨/헤딩 텍스트)와, 품목만 순회하고(하위 기업 펼치기 없음) 다운로드한 값을
월말로 collapse하지 않고 전체(10일/20일/월말) 그대로 저장하는 부분뿐이다.

이 화면은 하위 기업(지역) 펼치기가 없는 순수 품목 목록이라 "품목 및 지역 커스텀 설정"보다
빠르게 순회된다. scrape_bigfinance.py의 comment에 따르면 두 화면의 상위 품목 행 목록과
모달 다운로드 버튼은 동일하지만, 사용자 확인에 따라 이 화면을 별도로 방문해서 10일 단위
원본을 보존한다(월말만 남기는 "품목 및 지역 커스텀 설정" 파이프라인과 분리).

화면 식별 정보(ITEM_MENU_LABEL/ITEM_PAGE_HEADING_TEXT)는 기존 코드의 REGION_* 상수를
참고해 추정한 값이다. 최초 실행 시 화면을 못 찾으면 .auth/debug에 스크린샷/HTML이
자동 저장되니, 실제 화면 텍스트를 확인해서 이 값들을 조정하면 된다.

실행: python scrape_bigfinance_items.py
"""

from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from append_snapshot import append_decade_snapshot
from scrape_bigfinance import (
    BASE_URL,
    DOWNLOAD_DIR,
    EXPORT_METRIC_LABEL,
    GRID_SELECTOR,
    MAX_LOGIN_ATTEMPTS,
    MAX_NAV_ATTEMPTS,
    PRICE_METRIC_LABEL,
    PROFILE_DIR,
    _click_through_to_target,
    _close_item_modal,
    _download_series,
    _dismiss_landing_page,
    _dump_debug,
    _handle_login_prompt,
    _looks_like_login,
    _open_item_modal,
    _validate_latest_yoy,
)

BASE_DIR = Path(__file__).parent

# "잠정 수출" 하위 메뉴 중 "품목 커스텀 설정" - 하위 기업(지역) 펼치기가 없는 순수 품목
# 목록 화면. REGION_MENU_LABEL/REGION_PAGE_HEADING_TEXT와 대응되는 값 (scrape_bigfinance.py 참고).
ITEM_MENU_LABEL = "품목 커스텀 설정"
ITEM_PAGE_HEADING_TEXT = "잠정 수출 품목 리스트"


def _item_page_ready(page) -> bool:
    try:
        page.wait_for_selector(f"text={ITEM_PAGE_HEADING_TEXT}", timeout=10000)
        page.wait_for_selector(f"{GRID_SELECTOR}__body .label__columns table tbody tr", timeout=10000)
        return True
    except PWTimeoutError:
        return False


def ensure_item_data_ready(page) -> None:
    """목표 페이지("품목 커스텀 설정")가 뜰 때까지 기다린다.
    ensure_region_data_ready()와 동일한 재시도/재로그인 흐름."""
    login_attempts = 0
    for attempt in range(1, MAX_NAV_ATTEMPTS + 1):
        page.goto(BASE_URL, wait_until="domcontentloaded")
        _dismiss_landing_page(page)
        if _looks_like_login(page):
            login_attempts += 1
            if login_attempts >= MAX_LOGIN_ATTEMPTS:
                break
            _handle_login_prompt(page)
            continue

        try:
            _click_through_to_target(page, target_label=ITEM_MENU_LABEL)
        except PWTimeoutError:
            pass

        if _item_page_ready(page):
            print(f"로그인 확인 완료. 목표 페이지({ITEM_PAGE_HEADING_TEXT})를 찾았습니다.")
            return

        if _looks_like_login(page):
            login_attempts += 1
            if login_attempts >= MAX_LOGIN_ATTEMPTS:
                break
            _handle_login_prompt(page)
            continue

        print(f"[정보] {attempt}번째 시도에서 목표 페이지를 찾지 못했습니다 (현재 URL: {page.url}). 재시도합니다.")
        if attempt >= MAX_NAV_ATTEMPTS:
            break
        page.wait_for_timeout(1500)

    _dump_debug(page, "item_data_not_ready_exhausted")
    raise RuntimeError(
        "여러 번 재시도했지만 목표 페이지를 찾지 못했습니다. "
        ".auth/debug 폴더를 확인해주세요."
    )


def scrape_items(page) -> list[dict]:
    """"품목 커스텀 설정" 화면을 순회하며 품목별 전체 히스토리(10일/20일/월말)를 그대로
    수집한다. scrape_bigfinance.py의 scrape_items_and_companies()와 달리 하위 기업 펼치기가
    없고, _month_end_rows()로 월말만 남기지도 않는다 - 다운로드된 모든 행을 레코드로 만든다."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ensure_item_data_ready(page)
    body = page.locator(f"{GRID_SELECTOR}__body")

    top_rows = body.locator(".label__columns table tbody tr").filter(has=page.locator(".group__item__name.main-text"))
    n_top = top_rows.count()
    if n_top == 0:
        _dump_debug(page, "no_item_rows_found")
        raise RuntimeError("품목 커스텀 설정 화면에서 품목 행을 찾지 못했습니다. .auth/debug 폴더를 확인해주세요.")

    item_names = []
    for i in range(n_top):
        name_el = top_rows.nth(i).locator(".group__item__name.main-text").first
        item_names.append(name_el.inner_text().strip())

    print(f"[정보] {n_top}개 품목을 순회하며 다운로드합니다 (품목당 수출금액+단가 2회 다운로드, 10일 단위 전체 보존).")

    records: list[dict] = []
    n_done = 0
    for i, item_name in enumerate(item_names):
        if not item_name:
            continue

        row = body.locator(".label__columns table tbody tr").filter(
            has=page.locator(".group__item__name.main-text", has_text=item_name)
        ).first
        if row.count() == 0:
            print(f"[경고] {item_name}: 행을 다시 찾지 못해 건너뜁니다.")
            continue

        print(f"[{i + 1}/{n_top}] {item_name} 다운로드 중...")
        row.scroll_into_view_if_needed()
        try:
            _open_item_modal(page, row)
            modal = page.locator(".hs-codes-chart-modal")
            modal.get_by_text("All", exact=True).first.click(timeout=5000)
            page.wait_for_timeout(400)

            export_df = _download_series(page, modal, EXPORT_METRIC_LABEL)
            price_df = _download_series(page, modal, PRICE_METRIC_LABEL)

            _close_item_modal(page)
        except PWTimeoutError as e:
            print(f"[경고] {item_name}: 모달/다운로드 처리 중 시간 초과({e.__class__.__name__}). 이 품목은 건너뜁니다.")
            _dump_debug(page, "item_download_timeout")
            try:
                _close_item_modal(page)
            except PWTimeoutError:
                pass
            continue

        export_df = export_df.sort_values("date")
        price_by_date = dict(zip(price_df["date"], price_df["value"]))

        for _, r in export_df.iterrows():
            records.append(
                {
                    "품목명": item_name,
                    "기준일": r["date"].strftime("%Y-%m-%d"),
                    "수출금액": r["value"],
                    "단가": price_by_date.get(r["date"]),
                }
            )

        _validate_latest_yoy(export_df, item_name)
        n_done += 1
        latest_date = export_df.iloc[-1]["date"].strftime("%Y-%m-%d")
        print(f"  -> {len(export_df)}개 스냅샷 확보 (~{latest_date})")

    print(f"[정보] {n_done}/{n_top}개 품목 처리, 총 {len(records)}개 스냅샷 레코드.")
    if n_done == 0:
        raise RuntimeError("품목을 하나도 처리하지 못했습니다. 위 경고 메시지와 .auth/debug 폴더를 확인해주세요.")
    return records


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
            records = scrape_items(page)
        finally:
            context.close()

    seen = {}
    for r in records:
        seen[(r["품목명"], r["기준일"])] = r
    unique_records = list(seen.values())

    append_decade_snapshot(unique_records)


if __name__ == "__main__":
    main()
