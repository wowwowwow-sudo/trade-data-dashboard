"""
"품목 및 지역 커스텀 설정" 화면에서 실제 펼침(chevron) 아이콘을 정확히 클릭해
"전기전자_CCL"을 펼치고, 그 결과 "두산"이 DOM 어디에(새 <tr>인지, 같은 셀 안에
추가된 내용인지, 완전히 다른 위치인지) 나타나는지 확인하는 1회성 탐색 스크립트.

이전 probe_region_settings.py 결과로 확인한 것:
- 각 품목 행의 구조는 <td><div class="expand__group"><span class="expand-label">전체</span>
  <span class="group__item__name main-text">품목명</span><span><svg>...점 세 개 폴리라인...</svg></span>
  </div><div class="modal__block button__modal__inline">...차트 아이콘...</div></td>
- 이전 시도에서 "행의 마지막 엘리먼트"를 클릭한 건 실제로 차트 아이콘(.button__modal)이었어서
  펼쳐지지 않았다. 이번엔 .expand__group 안의 svg를 정확히 클릭한다.

실행: python probe_expand_ccl.py
"""

from playwright.sync_api import sync_playwright

from probe_region_settings import ensure_region_data_ready
from scrape_bigfinance import DEBUG_DIR, GRID_SELECTOR, PROFILE_DIR, _dump_debug

FIND_LEAF_JS = """
(targetText) => {
  const all = document.querySelectorAll('*');
  let el = null;
  for (const cand of all) {
    if (cand.children.length === 0 && cand.innerText && cand.innerText.trim() === targetText) {
      el = cand;
      break;
    }
  }
  if (!el) return { found: false };
  const ancestors = [];
  let cur = el;
  for (let i = 0; i < 8 && cur; i++) {
    ancestors.push({ tag: cur.tagName, className: cur.className || null, id: cur.id || null });
    cur = cur.parentElement;
  }
  const trAncestor = el.closest('tr');
  const containerAncestor = el.closest('.group-column-sort-table, .label__columns, .data__columns');
  return {
    found: true,
    leafOuterHTML: el.outerHTML,
    ancestors,
    nearestTrOuterHTML: trAncestor ? trAncestor.outerHTML : null,
    nearestTrClassName: trAncestor ? trAncestor.className : null,
    withinKnownContainer: containerAncestor ? containerAncestor.className : null,
  };
}
"""

ROW_COUNT_JS = """
(sel) => ({
  labelRows: document.querySelectorAll(sel.label + " table tbody tr").length,
  dataRows: document.querySelectorAll(sel.data + " table tbody tr").length,
})
"""


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

            sel = {"label": f"{GRID_SELECTOR}__body .label__columns", "data": f"{GRID_SELECTOR}__body .data__columns"}
            before_counts = page.evaluate(ROW_COUNT_JS, sel)
            print(f"[정보] 클릭 전 행 개수: {before_counts}")

            before_check = page.evaluate(FIND_LEAF_JS, "두산")
            print(f"[정보] 클릭 전 '두산' 텍스트 발견 여부: {before_check.get('found')}")

            body = page.locator(f"{GRID_SELECTOR}__body")
            ccl_row = body.locator(".label__columns table tbody tr").filter(has_text="전기전자_CCL").first
            if ccl_row.count() == 0:
                print("[오류] '전기전자_CCL' 행을 찾지 못했습니다.")
                _dump_debug(page, "ccl_row_not_found")
                return

            ccl_row.scroll_into_view_if_needed()
            chevron = ccl_row.locator(".expand__group svg").first
            if chevron.count() == 0:
                print("[오류] CCL 행 안에서 svg(펼침 아이콘)를 찾지 못했습니다.")
                _dump_debug(page, "ccl_chevron_not_found")
                return

            chevron.click(timeout=5000, force=True)
            page.wait_for_timeout(800)

            after_counts = page.evaluate(ROW_COUNT_JS, sel)
            print(f"[정보] 클릭 후 행 개수: {after_counts}")

            after_check = page.evaluate(FIND_LEAF_JS, "두산")
            print(f"[정보] 클릭 후 '두산' 텍스트 발견 여부: {after_check.get('found')}")
            if after_check.get("found"):
                print(f"  - leaf outerHTML: {after_check.get('leafOuterHTML')}")
                print(f"  - 가장 가까운 <tr> className: {after_check.get('nearestTrClassName')}")
                print(f"  - 가장 가까운 <tr> outerHTML: {after_check.get('nearestTrOuterHTML')}")
                print(f"  - 알려진 컨테이너(className): {after_check.get('withinKnownContainer')}")
                print("  - 조상 체인 (leaf -> 상위):")
                for a in after_check.get("ancestors", []):
                    print(f"      {a}")

            # CCL 행 자체의 클릭 후 상태도 확인 (같은 td 안에 내용이 추가됐을 수도 있으므로)
            ccl_row_html_after = ccl_row.evaluate("el => el.outerHTML")
            print(f"\n[정보] 클릭 후 CCL 행 자체의 outerHTML:\n{ccl_row_html_after}")

            _dump_debug(page, "ccl_expanded")

            input("\n확인했으면 이 창(터미널)에서 Enter를 눌러 종료...")
        finally:
            context.close()


if __name__ == "__main__":
    main()
