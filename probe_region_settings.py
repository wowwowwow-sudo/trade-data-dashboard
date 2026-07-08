"""
"품목 및 지역 커스텀 설정" 화면(잠정 수출 품목 지역별 리스트)의 DOM 구조를 확인하기
위한 1회성 탐색 스크립트.

이 화면은 scrape_bigfinance.py가 지금 가는 "품목 커스텀 설정"과는 다른 메뉴다.
스크린샷으로 확인한 내용: 각 품목 행은 "전체" 배지 + 품목명 + 펼침/접힘 화살표를
갖고, 일부 품목(예: 전기전자_CCL, 건설·운송·상사_굴삭기)은 이미 펼쳐진 상태로 그
밑에 번호가 매겨진 기업 행(예: "1  두산")과 데이터가 보인다. 이 스크립트는:

1. 이 화면으로 이동해서 전체 행 구조(클래스명/배지/화살표/번호)를 JSON으로 덤프
2. 화면상 접혀 있는 품목(반도체_IC칩, 웨이퍼)을 실제로 펼쳐봐서 "접힘 = 하위 데이터
   없음"이라는 가정이 맞는지 확인 (펼쳤을 때 '+지역 추가'만 나오는지, 숨겨진 번호
   행이 나오는지)
3. 이미 펼쳐진 기업 행(두산)에 마우스를 올렸을 때 품목 행과 같은 차트/다운로드
   모달 버튼(.button__modal)이 뜨는지 확인 (기업별로도 전체 히스토리 다운로드가
   가능한지가 12개월 시계열 확보 방법을 결정한다)

실행: python probe_region_settings.py
결과: .auth/debug/region_rows_probe_<타임스탬프>.json 및 콘솔 출력
"""

import json
from datetime import datetime

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from scrape_bigfinance import (
    BASE_DIR,
    DEBUG_DIR,
    GRID_SELECTOR,
    MAX_LOGIN_ATTEMPTS,
    MAX_NAV_ATTEMPTS,
    PROFILE_DIR,
    _dump_debug,
    _looks_like_login,
)

BASE_URL = "https://bigfinance.co.kr/"
REGION_PAGE_HEADING_TEXT = "잠정 수출 품목 지역별 리스트"
REGION_MENU_LABEL = "품목 및 지역 커스텀 설정"

COLLAPSED_TEST_ITEM = "반도체_IC칩"  # 스크린샷상 접힌 상태로 보였던 품목 (하위 행 없음 가정)
COMPANY_MODAL_TEST_NAME = "두산"  # 스크린샷상 이미 하위 행으로 보였던 기업명

ROWS_JS = """
(sel) => {
  function tdChildInfo(td) {
    if (!td) return [];
    return Array.from(td.children).map(c => ({
      tag: c.tagName,
      className: c.className,
      text: (c.innerText || "").trim().slice(0, 60),
    }));
  }
  const labelRows = Array.from(document.querySelectorAll(sel.label + " table tbody tr"));
  const dataRows = Array.from(document.querySelectorAll(sel.data + " table tbody tr"));
  return labelRows.map((row, i) => {
    const rowText = row.innerText.replace(/\\s+/g, " ").trim();
    const wholeBadge = Array.from(row.querySelectorAll("*")).find(el => el.children.length === 0 && el.innerText.trim() === "전체");
    const toggleCandidates = Array.from(row.querySelectorAll("*"))
      .filter(el => /chevron|arrow|toggle|expand|collapse|caret/i.test(el.className || ""))
      .map(el => ({ tag: el.tagName, className: el.className }));
    const leadingNumberMatch = rowText.match(/^(\\d+)\\s+(.*)$/);
    const firstTd = row.querySelector("td");
    const dRow = dataRows[i] || null;
    return {
      idx: i,
      rowClass: row.className,
      rowText: rowText,
      hasWholeBadge: !!wholeBadge,
      wholeBadgeClass: wholeBadge ? wholeBadge.className : null,
      leadingNumber: leadingNumberMatch ? leadingNumberMatch[1] : null,
      leadingNumberRest: leadingNumberMatch ? leadingNumberMatch[2] : null,
      toggleCandidates: toggleCandidates,
      hasModalButtonClass: !!row.querySelector(".button__modal"),
      firstTdChildren: tdChildInfo(firstTd),
      rowOuterHTML: row.outerHTML,
      dataRowClass: dRow ? dRow.className : null,
      dataRowText: dRow ? dRow.innerText.replace(/\\s+/g, " ").trim() : null,
      dataRowOuterHTML: dRow ? dRow.outerHTML : null,
    };
  });
}
"""


def _click_through_to_region_target(page) -> None:
    """scrape_bigfinance.py의 _click_through_to_target과 동일한 패턴이지만
    '품목 커스텀 설정' 대신 '품목 및 지역 커스텀 설정'을 클릭한다."""
    for label in ("Launch Data", "TRASS-BF 수출 데이터"):
        page.get_by_text(label, exact=True).first.click(timeout=10000)
        page.wait_for_timeout(500)

    sidebar = page.locator(".trass-trade__aside")
    sidebar.wait_for(timeout=10000)
    section = sidebar.locator(".menu__item", has_text="잠정 수출").first
    dropdown = section.locator(".dropdown").first
    class_attr = dropdown.get_attribute("class") or ""
    if "is--open" not in class_attr:
        section.locator(".menu__item__header").first.click(timeout=10000)
        page.wait_for_timeout(800)

    section.get_by_text(REGION_MENU_LABEL, exact=True).first.click(timeout=10000)
    page.wait_for_timeout(500)


def _region_page_ready(page) -> bool:
    try:
        page.wait_for_selector(f"text={REGION_PAGE_HEADING_TEXT}", timeout=10000)
        page.wait_for_selector(f"{GRID_SELECTOR} .label__columns table tbody tr", timeout=10000)
        return True
    except PWTimeoutError:
        return False


def ensure_region_data_ready(page) -> None:
    login_attempts = 0
    for attempt in range(1, MAX_NAV_ATTEMPTS + 1):
        page.goto(BASE_URL, wait_until="domcontentloaded")
        if _looks_like_login(page):
            login_attempts += 1
            if login_attempts >= MAX_LOGIN_ATTEMPTS:
                break
            print("\n로그인이 필요합니다. 열린 크롬 창에서 로그인해주세요.")
            input("로그인 완료 후 이 창(터미널)에서 Enter를 눌러주세요...")
            continue

        try:
            _click_through_to_region_target(page)
        except PWTimeoutError:
            pass

        if _region_page_ready(page):
            print("목표 페이지(잠정 수출 품목 지역별 리스트)를 찾았습니다.")
            return

        if _looks_like_login(page):
            login_attempts += 1
            if login_attempts >= MAX_LOGIN_ATTEMPTS:
                break
            print("\n로그인이 필요합니다. 열린 크롬 창에서 로그인해주세요.")
            input("로그인 완료 후 이 창(터미널)에서 Enter를 눌러주세요...")
            continue

        print(f"[정보] {attempt}번째 시도에서 목표 페이지를 찾지 못했습니다 (현재 URL: {page.url}). 재시도합니다.")
        if attempt >= MAX_NAV_ATTEMPTS:
            break
        page.wait_for_timeout(1500)

    _dump_debug(page, "region_page_not_ready")
    raise RuntimeError("품목 및 지역 커스텀 설정 페이지를 찾지 못했습니다. .auth/debug 폴더를 확인해주세요.")


def _extract_rows(page):
    return page.evaluate(
        ROWS_JS,
        {"label": f"{GRID_SELECTOR}__body .label__columns", "data": f"{GRID_SELECTOR}__body .data__columns"},
    )


def _print_context(rows, name_substring: str, label: str) -> None:
    print(f"\n[{label}] '{name_substring}' 포함 행 주변 문맥")
    matched = [i for i, r in enumerate(rows) if name_substring in (r["rowText"] or "")]
    if not matched:
        print("  -> 찾지 못했습니다.")
        return
    for i in matched:
        lo, hi = max(0, i - 1), min(len(rows), i + 4)
        for j in range(lo, hi):
            marker = ">>" if j == i else "  "
            r = rows[j]
            print(
                f"{marker} [{j}] rowText={r['rowText']!r} hasWholeBadge={r['hasWholeBadge']} "
                f"leadingNumber={r['leadingNumber']!r} hasModalButtonClass={r['hasModalButtonClass']}"
            )


def _try_expand_collapsed_item(page, body, name_substring: str) -> None:
    print(f"\n[펼침 테스트] '{name_substring}' 행을 펼쳐봅니다.")
    row = body.locator(".label__columns table tbody tr").filter(has_text=name_substring).first
    if row.count() == 0:
        print("  -> 해당 품목 행을 찾지 못했습니다.")
        return

    before_count = body.locator(".label__columns table tbody tr").count()
    row.scroll_into_view_if_needed()
    page.wait_for_timeout(200)

    clicked = False
    for desc, locator_fn in [
        ("행 내 마지막 엘리먼트 클릭", lambda: row.locator("*").last),
        ("행 자체 클릭", lambda: row),
    ]:
        try:
            locator_fn().click(timeout=2000, force=True)
            page.wait_for_timeout(600)
            after_count = body.locator(".label__columns table tbody tr").count()
            if after_count != before_count:
                print(f"  -> {desc}: 행 개수 {before_count} -> {after_count}로 변경됨. 펼침 성공으로 판단.")
                clicked = True
                break
            else:
                print(f"  -> {desc}: 클릭했지만 행 개수 변화 없음 ({before_count}).")
        except Exception as e:
            print(f"  -> {desc} 실패: {e}")
    if not clicked:
        print("  -> 펼침에 실패했거나, 펼쳐도 행 개수 변화가 없습니다 (이미 펼쳐진 상태였거나 다른 구조일 수 있음).")

    rows_after = _extract_rows(page)
    _print_context(rows_after, name_substring, "펼침 시도 후")


def _probe_company_modal(page, body, name_substring: str) -> None:
    print(f"\n[모달 테스트] '{name_substring}' 행에 차트/다운로드 모달이 있는지 확인합니다.")
    row = body.locator(".label__columns table tbody tr").filter(has_text=name_substring).first
    if row.count() == 0:
        print("  -> 해당 기업 행을 찾지 못했습니다.")
        return

    row.scroll_into_view_if_needed()
    row.hover()
    page.wait_for_timeout(300)
    has_modal_btn = row.locator(".button__modal").count() > 0
    print(f"  -> .button__modal 존재 여부: {has_modal_btn}")
    if not has_modal_btn:
        return

    try:
        row.locator(".button__modal").first.click(force=True, timeout=5000)
        page.wait_for_selector(".hs-codes-chart-modal", timeout=10000)
        page.wait_for_timeout(300)
        _dump_debug(page, "company_modal_probe")
        modal = page.locator(".hs-codes-chart-modal")
        has_all_range = modal.get_by_text("All", exact=True).count() > 0
        print(f"  -> 모달 열림. 'All' 범위 버튼 존재 여부: {has_all_range}")
        page.locator(".hs-codes-chart-modal .close-button").first.click(timeout=5000)
        page.wait_for_timeout(300)
    except Exception as e:
        print(f"  -> 기업 행 모달 열기/확인 중 오류: {e}")


def main() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            ensure_region_data_ready(page)

            body = page.locator(f"{GRID_SELECTOR}__body")

            rows = _extract_rows(page)
            print(f"[정보] 총 {len(rows)}개 행을 추출했습니다.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = DEBUG_DIR / f"region_rows_probe_{ts}.json"
            out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[정보] 전체 결과를 {out_path}에 저장했습니다.")

            _print_context(rows, "CCL", "펼쳐진 상태로 보였던 품목")
            _print_context(rows, "굴삭기", "펼쳐진 상태로 보였던 품목")
            _print_context(rows, COLLAPSED_TEST_ITEM, "접힌 상태로 보였던 품목 (펼치기 전)")

            _try_expand_collapsed_item(page, body, COLLAPSED_TEST_ITEM)
            _probe_company_modal(page, body, COMPANY_MODAL_TEST_NAME)

            input("\n확인했으면 이 창(터미널)에서 Enter를 눌러 종료...")
        finally:
            context.close()


if __name__ == "__main__":
    main()
