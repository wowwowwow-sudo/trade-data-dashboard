"""
품목 리스트에서 "하위 행(기업/지역 breakdown)"이 실제로 어떤 DOM 구조로 나타나는지
확인하기 위한 1회성 탐색 스크립트 (probe_modal.py와 같은 패턴 - 구조를 추측해서
먼저 구현하지 않기 위한 중간 단계).

.label__columns 쪽 모든 <tr>에 대해 클래스명/들여쓰기(padding-left 등)/첫 번째 td의
자식 엘리먼트 구조/텍스트를 JS로 추출하고, 같은 인덱스의 .data__columns 쪽 값도 같이
저장한다. 기업 하위 행이 있는 품목(예: 화면에 보이는 "전기전자_CCL" 아래 "두산" 등)과
없는 품목("전체" 행만 있는 품목)을 실제 데이터로 비교해 구분 로직을 설계하기 위함.

실행: python probe_item_rows.py
(scrape_bigfinance.py와 동일하게 크롬 창이 뜨고, 필요시 로그인을 기다린다.)

결과: .auth/debug/item_rows_probe_<타임스탬프>.json 에 저장됨.
"""

import json
from datetime import datetime

from playwright.sync_api import sync_playwright

from scrape_bigfinance import BASE_DIR, DEBUG_DIR, GRID_SELECTOR, PROFILE_DIR, ensure_data_ready

# 사용자가 화면에서 실제로 기업 하위 행을 봤다고 언급한 품목/기업명 힌트.
# 하드코딩된 처리 로직에 쓰이는 게 아니라, 콘솔에서 해당 행을 빨리 찾기 위한 검색어일 뿐.
HINT_KEYWORDS = ["두산", "굴삭기", "HD건설기계", "두산밥캣", "CCL"]

ROWS_JS = """
(sel) => {
  function shortHTML(el, max) {
    if (!el) return null;
    const html = el.outerHTML || "";
    return html.length > max ? html.slice(0, max) + "..." : html;
  }
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
    const nameEl = row.querySelector(".group__item__name");
    const nameCs = nameEl ? getComputedStyle(nameEl) : null;
    const firstTd = row.querySelector("td");
    const tdCs = firstTd ? getComputedStyle(firstTd) : null;
    const dRow = dataRows[i] || null;
    return {
      idx: i,
      rowClass: row.className,
      nameSpanClass: nameEl ? nameEl.className : null,
      nameText: nameEl ? nameEl.innerText.trim() : null,
      rowText: row.innerText.replace(/\\s+/g, " ").trim(),
      tdCount: row.children.length,
      hasModalButton: !!row.querySelector(".button__modal"),
      nameComputedPaddingLeft: nameCs ? nameCs.paddingLeft : null,
      nameComputedMarginLeft: nameCs ? nameCs.marginLeft : null,
      nameComputedTextIndent: nameCs ? nameCs.textIndent : null,
      tdComputedPaddingLeft: tdCs ? tdCs.paddingLeft : null,
      firstTdChildren: tdChildInfo(firstTd),
      rowOuterHTML: shortHTML(row, 400),
      dataRowClass: dRow ? dRow.className : null,
      dataRowText: dRow ? dRow.innerText.replace(/\\s+/g, " ").trim().slice(0, 200) : null,
      dataRowOuterHTML: shortHTML(dRow, 400),
    };
  });
}
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
            ensure_data_ready(page)

            rows = page.evaluate(
                ROWS_JS,
                {"label": f"{GRID_SELECTOR}__body .label__columns", "data": f"{GRID_SELECTOR}__body .data__columns"},
            )
        finally:
            context.close()

    print(f"[정보] 총 {len(rows)}개 행을 추출했습니다.")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = DEBUG_DIR / f"item_rows_probe_{ts}.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[정보] 전체 결과를 {out_path}에 저장했습니다.")

    print("\n[힌트 키워드 매칭 행] (앞뒤 문맥 포함)")
    matched_any = False
    for i, r in enumerate(rows):
        if any(k.lower() in (r["rowText"] or "").lower() for k in HINT_KEYWORDS):
            matched_any = True
            lo = max(0, i - 2)
            hi = min(len(rows), i + 3)
            print(f"--- idx {i} 주변 (idx {lo}~{hi - 1}) ---")
            for j in range(lo, hi):
                marker = ">>" if j == i else "  "
                print(f"{marker} [{j}] rowClass={rows[j]['rowClass']!r} nameText={rows[j]['nameText']!r}")
    if not matched_any:
        print("힌트 키워드와 일치하는 행을 찾지 못했습니다. (해당 품목이 현재 커스텀 설정에 없을 수 있습니다)")
        print("이 경우 out_path의 JSON 전체를 보고 상위/하위 행 패턴을 확인해야 합니다.")


if __name__ == "__main__":
    main()
