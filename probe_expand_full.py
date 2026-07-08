"""
"품목 및 지역 커스텀 설정" 화면 - 하위 행(기업) 감지 로직 설계를 위한 마지막 확인.

지금까지 확인한 것 (probe_expand_ccl.py 결과):
- 상위 품목 행: class="tr-{N}", .group__item__name.main-text, .expand-label("전체")
- 하위 기업 행(펼쳤을 때 새로 생김): class="tr-{N}-{M}", .group__item__name.sub-text,
  .expanded-label.sub-index(순번), 자체 .button__modal 보유

아직 확인 안 된 것:
1. CCL을 펼쳤을 때 라벨 행이 68->70개로 "두산" 외에 1개 더 늘었는데, 그게 "+지역 추가"
   안내 행인지 다른 회사 행인지
2. 하위 기업이 아예 없는 품목(반도체_IC칩, 웨이퍼)을 펼치면 어떤 모습인지
   (숫자 매겨진 회사 행 없이 "+지역 추가"만 나오는지, 그 경우의 class 패턴)
3. "두산" 행에 실제로 모달을 열어서 품목 모달과 동일하게 전체기간(All) 다운로드가
   되는지

이 스크립트는 CCL과 반도체_IC칩을 각각 펼쳐서 그 결과를 JSON+콘솔로 출력하고,
두산 행의 모달을 실제로 열어본다.

실행: python probe_expand_full.py
"""

import json
from datetime import datetime

from playwright.sync_api import sync_playwright

from probe_region_settings import ROWS_JS, _print_context, ensure_region_data_ready
from scrape_bigfinance import DEBUG_DIR, GRID_SELECTOR, PROFILE_DIR, _close_item_modal, _dump_debug


def _extract_rows(page):
    return page.evaluate(
        ROWS_JS,
        {"label": f"{GRID_SELECTOR}__body .label__columns", "data": f"{GRID_SELECTOR}__body .data__columns"},
    )


def _expand_by_text(page, body, name_substring: str) -> bool:
    row = body.locator(".label__columns table tbody tr").filter(has_text=name_substring).first
    if row.count() == 0:
        print(f"[오류] '{name_substring}' 행을 찾지 못했습니다.")
        return False
    row.scroll_into_view_if_needed()
    chevron = row.locator(".expand__group svg").first
    if chevron.count() == 0:
        print(f"[오류] '{name_substring}' 행에서 펼침 아이콘을 찾지 못했습니다.")
        return False
    chevron.click(timeout=5000, force=True)
    page.wait_for_timeout(700)
    return True


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

            print("=== 1. 전기전자_CCL 펼치기 (하위 기업 있는 케이스) ===")
            if _expand_by_text(page, body, "전기전자_CCL"):
                rows = _extract_rows(page)
                _print_context(rows, "CCL", "CCL 펼친 후")
                # CCL(tr-64) 바로 다음, 다음 품목 행(hasWholeBadge=True)이 나오기 전까지 전부 출력
                ccl_idx = next(i for i, r in enumerate(rows) if r["rowText"] == "전체 전기전자_CCL")
                j = ccl_idx + 1
                print("\n[CCL 하위 전체 행 상세]")
                while j < len(rows) and not rows[j]["hasWholeBadge"]:
                    r = rows[j]
                    print(f"  [{j}] rowClass={r['rowClass']!r} rowText={r['rowText']!r}")
                    print(f"       rowOuterHTML={r['rowOuterHTML']}")
                    j += 1

            print("\n=== 2. 반도체_IC칩, 웨이퍼 펼치기 (하위 기업 없는 케이스) ===")
            if _expand_by_text(page, body, "반도체_IC칩"):
                rows = _extract_rows(page)
                ic_idx = next(i for i, r in enumerate(rows) if "반도체_IC칩" in r["rowText"] and r["hasWholeBadge"])
                j = ic_idx + 1
                print("\n[IC칩 하위 전체 행 상세]")
                found_any = False
                while j < len(rows) and not rows[j]["hasWholeBadge"]:
                    found_any = True
                    r = rows[j]
                    print(f"  [{j}] rowClass={r['rowClass']!r} rowText={r['rowText']!r}")
                    print(f"       rowOuterHTML={r['rowOuterHTML']}")
                    j += 1
                if not found_any:
                    print("  (하위 행이 전혀 없습니다 - 펼쳐도 아무것도 안 나타남)")

            print("\n=== 3. '두산' 행 모달 테스트 ===")
            doosan_row = body.locator(".label__columns table tbody tr").filter(has_text="두산").first
            if doosan_row.count() > 0:
                doosan_row.scroll_into_view_if_needed()
                doosan_row.hover()
                page.wait_for_timeout(300)
                has_modal = doosan_row.locator(".button__modal").count() > 0
                print(f"'두산' 행 .button__modal 존재: {has_modal}")
                if has_modal:
                    doosan_row.locator(".button__modal").first.click(force=True, timeout=5000)
                    page.wait_for_selector(".hs-codes-chart-modal", timeout=10000)
                    page.wait_for_timeout(300)
                    modal = page.locator(".hs-codes-chart-modal")
                    has_all = modal.get_by_text("All", exact=True).count() > 0
                    print(f"모달 내 'All' 버튼 존재: {has_all}")
                    try:
                        current_metric = modal.locator(
                            ".data-selector .input-list-selector__single-value"
                        ).first.inner_text().strip()
                        print(f"현재 선택된 지표: {current_metric!r}")
                    except Exception as e:
                        print(f"지표 선택자 확인 실패: {e}")
                    _dump_debug(page, "doosan_modal_probe")
                    _close_item_modal(page)
            else:
                print("'두산' 행을 찾지 못했습니다.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            rows_final = _extract_rows(page)
            out_path = DEBUG_DIR / f"expand_full_probe_{ts}.json"
            out_path.write_text(json.dumps(rows_final, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n[정보] 최종 전체 행 상태를 {out_path}에 저장했습니다.")

            input("\n확인했으면 이 창(터미널)에서 Enter를 눌러 종료...")
        finally:
            context.close()


if __name__ == "__main__":
    main()
