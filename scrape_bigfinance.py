"""
bigfinance.co.kr(EPIC Finance) "잠정 수출 품목 리스트" 페이지에서 품목별로
모달(차트)을 열어 "수출 금액"/"수출 단가"를 전체 기간(All) 엑셀로 다운로드하고,
그 히스토리를 trade_history_long.csv에 누적하는 스크립트.

목록 페이지의 요약 테이블(최신월/전년동월/전월 5개 컬럼)만 읽는 대신, 품목별
다운로드를 쓰는 이유: 다운로드 파일에는 2016년부터 월말 기준 전체 히스토리가
들어있어서, 한 번 실행으로 과거 데이터까지 백필할 수 있다 (.auth/debug의 실제
다운로드 파일로 열단위(10일/20일/월말) 구조 확인함, 2026-07-06).

로그인 방식:
  - Playwright 전용 크롬 프로필(.auth/chrome_profile)을 headless=False로 띄운다.
    이 폴더는 평소 쓰는 크롬의 User Data 폴더와 완전히 분리되어 있어
    평소 크롬이 켜져 있어도 충돌하지 않는다.
  - 최초 실행: 로그인 페이지가 뜨면 사용자가 직접 로그인(또는 크롬 자동완성 사용)
    -> Enter를 누르면 계속 진행. 로그인 상태는 프로필 폴더에 자동 저장된다.
  - 이후 실행: 프로필에 저장된 쿠키로 로그인 없이 바로 진행.
  - 세션 만료로 로그인 페이지가 다시 뜨면 동일하게 안내 후 재로그인 대기.

비밀번호는 이 스크립트가 절대 입력받거나 저장하지 않는다. 크롬이 자기 프로필
안에서 자동완성/비밀번호 저장을 처리할 뿐이다.

실행: python scrape_bigfinance.py
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from append_snapshot import append_company_snapshot, append_snapshot

BASE_DIR = Path(__file__).parent
PROFILE_DIR = BASE_DIR / ".auth" / "chrome_profile"
DEBUG_DIR = BASE_DIR / ".auth" / "debug"
DOWNLOAD_DIR = BASE_DIR / ".auth" / "downloads"

BASE_URL = "https://bigfinance.co.kr/"
# custom-product-group URL로 직접 goto하면 SPA가 딥링크를 제대로 처리하지 못하고
# 엉뚱한 화면(Market 기본 화면, 국가별 무역수지 화면 등)으로 보내는 경우가 있어서
# 실제 화면 메뉴를 Launch Data -> TRASS-BF 수출 데이터 -> 잠정 수출(펼치기) -> 품목 커스텀 설정
# 순서로 그대로 클릭해서 들어간다 (_click_through_to_target 참고).
PAGE_HEADING_TEXT = "잠정 수출 품목 리스트"  # "...지역별 리스트"(다른 메뉴)와 구분되는 이 페이지 고유 제목

# 이 페이지는 <table> 하나가 아니라 품목명 칼럼(.label__columns)과
# 수출금액/단가 칼럼(.data__columns)이 좌우로 분리된 커스텀 그리드(.group-column-sort-table)다.
# 이 그리드 클래스는 다른 메뉴(국가별 등)에서도 재사용되므로, PAGE_HEADING_TEXT로
# 페이지를 먼저 식별한 뒤에만 신뢰한다.
GRID_SELECTOR = ".group-column-sort-table"

YOY_TOLERANCE_PCT = 1.5  # 계산값과 다운로드 파일의 YoY 차이가 이 값(퍼센트포인트)을 넘으면 경고만 출력

EXPORT_METRIC_LABEL = "수출 금액"
PRICE_METRIC_LABEL = "수출 단가"

# "품목 및 지역 커스텀 설정" 화면 - 품목 중 일부만 하위에 기업(지역)을 펼쳐볼 수 있게
# 설정되어 있다 (probe_expand_ccl.py/probe_expand_full.py로 실제 DOM 확인, 2026-07-07).
# 상위 품목 행: class="tr-{N}", .expand-label 뱃지가 "전체".
# 하위 기업 행(펼쳤을 때만 나타남): class="tr-{N}-{M}", .expanded-label.sub-index에 순번,
# .group__item__name.sub-text에 기업명. 자체 .button__modal을 가지고 있어 품목 행과 동일한
# 방식(_open_item_modal/_download_series)으로 전체 히스토리 다운로드가 가능하다.
# 접힘/펼침 기본 상태는 하위 기업 존재 여부와 무관 - 반드시 펼쳐봐야 알 수 있다.
REGION_MENU_LABEL = "품목 및 지역 커스텀 설정"
REGION_PAGE_HEADING_TEXT = "잠정 수출 품목 지역별 리스트"
COLLAPSED_POLYLINE_POINTS = "6 9 12 15 18 9"  # 펼침 아이콘(svg polyline)의 "접힘" 상태 좌표

CHILD_ROW_WALK_JS = """
(rowEl) => {
  const children = [];
  let sib = rowEl.nextElementSibling;
  while (sib && /^tr-\\d+-\\d+$/.test(sib.className)) {
    const nameEl = sib.querySelector(".group__item__name.sub-text");
    children.push({ className: sib.className, companyName: nameEl ? nameEl.innerText.trim() : null });
    sib = sib.nextElementSibling;
  }
  return children;
}
"""


# ---------- 디버그 ----------
def _dump_debug(page, tag: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    png = DEBUG_DIR / f"{tag}_{ts}.png"
    html = DEBUG_DIR / f"{tag}_{ts}.html"
    try:
        page.screenshot(path=str(png), full_page=True)
    except Exception as e:
        print(f"[디버그] 스크린샷 저장 실패: {e}")
    try:
        html.write_text(page.content(), encoding="utf-8")
    except Exception as e:
        print(f"[디버그] HTML 저장 실패: {e}")
    print(f"[디버그] {DEBUG_DIR / (tag + '_' + ts)}.png / .html 에 현재 상태를 저장했습니다.")
    print("[디버그] 이 파일들을 확인 후 알려주시면 파서를 조정하겠습니다.")


# ---------- 로그인 & 이동 ----------
MAX_LOGIN_ATTEMPTS = 2
MAX_NAV_ATTEMPTS = 3  # 로그인 문제가 아닌데 엉뚱한 페이지로 튕기는 SPA 라우팅 타이밍 이슈 대응
DATA_READY_SELECTOR = f"{GRID_SELECTOR} .label__columns table tbody tr"


def _looks_like_login(page) -> bool:
    url_lower = page.url.lower()
    if "login" in url_lower or "signin" in url_lower:
        return True
    try:
        page.wait_for_selector("input[type='password']", timeout=2000)
        return True
    except PWTimeoutError:
        return False


def _click_through_to_target(page, target_label: str = "품목 커스텀 설정") -> None:
    """URL 직접 이동 대신, 실제 화면의 메뉴 텍스트를 순서대로 클릭해서 목표 페이지로 이동.

    사이드바 구조 (.auth/debug HTML로 확인):
      <li class="menu__item">
        <span class="menu__item__header"><span class="menu__item__header__title">잠정 수출</span>...</span>
        <ul class="dropdown is--open|is--close">
          <li class="dropdown__item"><span class="dropdown__item__title">품목 커스텀 설정</span></li>
          <li class="dropdown__item"><span class="dropdown__item__title">품목 및 지역 커스텀 설정</span></li>
          ...
        </ul>
      </li>
    '잠정 수출' 텍스트가 다른 곳(예: 데이터 타입 토글)에도 있을 수 있어 페이지 전역에서
    get_by_text로 찾지 않고, 사이드바(.trass-trade__aside) 안에서만 찾는다.

    target_label로 같은 '잠정 수출' 하위의 다른 메뉴(예: 기업/지역 breakdown이 있는
    '품목 및 지역 커스텀 설정')로도 이동할 수 있다.
    """
    for label in ("Launch Data", "TRASS-BF 수출 데이터"):
        page.get_by_text(label, exact=True).first.click(timeout=10000)
        page.wait_for_timeout(500)

    sidebar = page.locator(".trass-trade__aside")
    sidebar.wait_for(timeout=10000)
    section = sidebar.locator(".menu__item", has_text="잠정 수출").first
    dropdown = section.locator(".dropdown").first
    # 상단 "Launch Data" 드롭다운 패널이 화면에 남아 사이드바 헤더 클릭을 가로막는
    # 경우가 있어(2026-07-07 확인), 한 번만 클릭하고 넘어가지 않고 열림 상태가 될
    # 때까지 몇 차례 재시도한다.
    for _ in range(3):
        class_attr = dropdown.get_attribute("class") or ""
        if "is--open" in class_attr:
            break
        section.locator(".menu__item__header").first.click(timeout=10000)
        page.wait_for_timeout(800)

    section.get_by_text(target_label, exact=True).first.click(timeout=10000)
    page.wait_for_timeout(500)


def _page_ready(page) -> bool:
    try:
        page.wait_for_selector(f"text={PAGE_HEADING_TEXT}", timeout=10000)
        page.wait_for_selector(DATA_READY_SELECTOR, timeout=10000)
        return True
    except PWTimeoutError:
        return False


def _region_page_ready(page) -> bool:
    try:
        page.wait_for_selector(f"text={REGION_PAGE_HEADING_TEXT}", timeout=10000)
        page.wait_for_selector(f"{GRID_SELECTOR}__body .label__columns table tbody tr", timeout=10000)
        return True
    except PWTimeoutError:
        return False


def _ensure_ready(page, target_label: str, page_ready_fn, ready_desc: str) -> None:
    """ensure_data_ready/ensure_region_data_ready 공용 재시도/재로그인 로직.
    target_label만 다른 메뉴로 이동하는 두 화면(품목 커스텀 설정 / 품목 및 지역
    커스텀 설정)에 그대로 재사용한다."""
    login_attempts = 0
    for attempt in range(1, MAX_NAV_ATTEMPTS + 1):
        page.goto(BASE_URL, wait_until="domcontentloaded")
        if _looks_like_login(page):
            login_attempts += 1
            if login_attempts >= MAX_LOGIN_ATTEMPTS:
                break
            print("\n로그인이 필요합니다(최초 로그인 또는 세션 만료). 열린 크롬 창에서 로그인해주세요.")
            print("(자동완성/저장된 비밀번호가 있다면 채워질 수 있습니다. 없다면 직접 입력 후")
            print(" '비밀번호 저장'을 눌러두면 다음부터 자동완성이 동작합니다.)")
            input("로그인 완료 후 이 창(터미널)에서 Enter를 눌러주세요...")
            continue

        try:
            _click_through_to_target(page, target_label=target_label)
        except PWTimeoutError:
            pass  # 메뉴 클릭이 실패해도 아래에서 로그인/미도달 여부를 다시 판단

        if page_ready_fn(page):
            print(f"로그인 확인 완료. 목표 페이지({ready_desc})를 찾았습니다.")
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

    _dump_debug(page, "data_not_ready_exhausted")
    raise RuntimeError(
        "여러 번 재시도했지만 목표 페이지를 찾지 못했습니다. "
        ".auth/debug 폴더를 확인해주세요."
    )


def ensure_data_ready(page) -> None:
    """목표 페이지(잠정 수출 품목 리스트)가 뜰 때까지 기다린다.
    - 로그인 페이지로 튕기면(최초 로그인이든, 세션 만료로 뒤늦게 튕기는 경우든) 재로그인을 안내하고 재시도한다.
    - 로그인은 됐는데 엉뚱한 메뉴로 가 있으면(같은 그리드 컴포넌트를 쓰는 다른 메뉴 등),
      메뉴를 다시 클릭해서 재시도한다.
    """
    _ensure_ready(page, "품목 커스텀 설정", _page_ready, PAGE_HEADING_TEXT)


def ensure_region_data_ready(page) -> None:
    """목표 페이지(잠정 수출 품목 지역별 리스트 - 기업/지역 펼치기가 가능한 화면)가
    뜰 때까지 기다린다. ensure_data_ready와 동일한 재시도/재로그인 로직을 쓰되
    다른 메뉴로 이동한다."""
    _ensure_ready(page, REGION_MENU_LABEL, _region_page_ready, REGION_PAGE_HEADING_TEXT)


# ---------- 품목 모달: 열기/닫기/지표 전환/다운로드 ----------
def _open_item_modal(page, row) -> None:
    row.scroll_into_view_if_needed()
    row.hover()
    page.wait_for_timeout(200)
    row.locator(".button__modal").first.click(force=True)
    page.wait_for_selector(".hs-codes-chart-modal", timeout=10000)
    page.wait_for_timeout(300)


def _close_item_modal(page) -> None:
    page.locator(".hs-codes-chart-modal .close-button").first.click(timeout=5000)
    page.wait_for_timeout(300)


def _select_metric(page, modal, metric_label: str) -> None:
    current = modal.locator(".data-selector .input-list-selector__single-value").first.inner_text().strip()
    if current == metric_label:
        return
    modal.locator(".data-selector .input-list-selector__control").first.click()
    page.wait_for_timeout(300)
    modal.get_by_text(metric_label, exact=True).first.click()
    page.wait_for_timeout(300)


def _download_series(page, modal, metric_label: str) -> pd.DataFrame:
    _select_metric(page, modal, metric_label)
    with page.expect_download() as download_info:
        modal.locator(".chart-module__download-chart").click(timeout=10000)
    download = download_info.value
    # 품목명에 '/' 등이 섞여 있으면(예: "SiC/Si/Quartz") suggested_filename을 그대로
    # 경로로 쓰면 중첩 폴더로 오인되어 저장이 깨진다. 고정된 임시 파일명을 쓴다.
    tmp_path = DOWNLOAD_DIR / "_tmp_download.xlsx"
    download.save_as(str(tmp_path))
    df = pd.read_excel(tmp_path, header=0)
    df.columns = ["date", "value", "yoy"]
    df["date"] = pd.to_datetime(df["date"])
    tmp_path.unlink(missing_ok=True)
    return df


def _month_end_rows(df: pd.DataFrame) -> pd.DataFrame:
    """다운로드 데이터는 열흘 단위(10일/20일/월말)라 각 달의 마지막 행(월말 누계치)만 남긴다."""
    df = df.sort_values("date")
    period = df["date"].dt.to_period("M")
    return df.groupby(period, as_index=False).tail(1).reset_index(drop=True)


def _validate_latest_yoy(export_month: pd.DataFrame, item_name: str) -> None:
    latest = export_month.iloc[-1]
    if pd.isna(latest["yoy"]):
        return
    yoy_target = latest["date"] - pd.DateOffset(years=1)
    base_row = export_month[export_month["date"] == yoy_target]
    if base_row.empty or not base_row.iloc[0]["value"]:
        return
    base_value = base_row.iloc[0]["value"]
    computed = (latest["value"] - base_value) / base_value * 100
    diff = abs(computed - latest["yoy"])
    if diff > YOY_TOLERANCE_PCT:
        print(
            f"[경고] {item_name}: 계산된 YoY {computed:+.2f}% vs 다운로드 YoY "
            f"{latest['yoy']:+.2f}% (차이 {diff:.2f}%p) - 값을 확인해주세요. 저장은 계속 진행합니다."
        )


# ---------- 전체 품목 순회 ----------
def scrape_all_items(page) -> list[dict]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    body = page.locator(f"{GRID_SELECTOR}__body")
    item_rows = body.locator(".label__columns table tbody tr")
    n_rows = item_rows.count()
    if n_rows == 0:
        _dump_debug(page, "no_item_rows_found")
        raise RuntimeError("품목명 목록(.label__columns)을 찾지 못했습니다. .auth/debug 폴더를 확인해주세요.")
    print(f"[정보] {n_rows}개 품목을 순회하며 다운로드합니다 (품목당 수출금액+단가 2회 다운로드).")

    all_records: list[dict] = []
    n_done = 0
    for i in range(n_rows):
        row = item_rows.nth(i)
        name_span = row.locator(".group__item__name")
        item_name = name_span.first.inner_text().strip() if name_span.count() > 0 else ""
        if not item_name:
            continue

        print(f"[{i + 1}/{n_rows}] {item_name} 다운로드 중...")
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

        export_month = _month_end_rows(export_df)
        price_month = _month_end_rows(price_df)
        price_by_date = dict(zip(price_month["date"], price_month["value"]))

        for _, r in export_month.iterrows():
            all_records.append(
                {
                    "품목명": item_name,
                    "기준일": r["date"].strftime("%Y-%m-%d"),
                    "수출금액": r["value"],
                    "단가": price_by_date.get(r["date"]),
                }
            )

        _validate_latest_yoy(export_month, item_name)
        n_done += 1
        latest_month = export_month.iloc[-1]["date"].strftime("%Y-%m")
        print(f"  -> {len(export_month)}개월치 확보 (2016년~{latest_month})")

    print(f"[정보] {n_done}/{n_rows}개 품목, 총 {len(all_records)}개 레코드 추출.")
    if n_done == 0:
        raise RuntimeError("품목을 하나도 처리하지 못했습니다. 위 경고 메시지와 .auth/debug 폴더를 확인해주세요.")
    return all_records


# ---------- 품목 및 지역 커스텀 설정: 하위 기업(지역) 순회 ----------
def _ensure_row_expanded(page, row) -> None:
    """품목 행의 펼침 아이콘(svg polyline)이 '접힘' 좌표면 클릭해서 펼친다.
    이미 펼쳐져 있으면 아무것도 하지 않는다 (잘못 클릭하면 오히려 접혀버리므로)."""
    polyline = row.locator(".expand__group svg polyline").first
    if polyline.count() == 0:
        return
    points = (polyline.get_attribute("points") or "").strip()
    if points == COLLAPSED_POLYLINE_POINTS:
        row.locator(".expand__group svg").first.click(timeout=5000, force=True)
        page.wait_for_timeout(500)


def scrape_company_breakdowns(page) -> list[dict]:
    """"품목 및 지역 커스텀 설정" 화면에서, 하위에 기업(지역) 행이 실제로 설정된
    품목만 골라 기업별 수출금액/단가 전체 히스토리를 수집한다.

    한 품목에 하위 기업이 있는지는 펼치기 전에는 알 수 없다 (화면 기본 접힘/펼침
    상태와 무관 - probe_expand_full.py로 실제 확인함: IC칩처럼 접힌 채로 보이는
    품목도 펼치면 하위 기업이 나오는 경우가 있었다). 그래서 전체 품목을 순서대로
    펼쳐본 뒤, 펼친 직후 바로 다음에 tr-{부모}-{자식} 형태의 행이 있으면(=번호가
    매겨진 실제 기업 행) 그것만 채택하고, "+지역 추가" placeholder만 있으면
    건너뛴다. 특정 품목명을 하드코딩하지 않는다 - 품목 커스텀 설정은 계속
    추가될 수 있기 때문.
    """
    ensure_region_data_ready(page)
    body = page.locator(f"{GRID_SELECTOR}__body")

    # .group__item__name.main-text가 있는 행만 상위 품목 행이다 (하위 기업 행은
    # .group__item__name.sub-text, placeholder 행은 .group__item__name__option을 쓴다 -
    # probe_expand_ccl.py/probe_expand_full.py로 실제 DOM 확인함). 텍스트("전체" 접두어)
    # 매칭 대신 클래스로 구분해야 Playwright의 텍스트 정규화 이슈를 피할 수 있다
    # (2026-07-07: has_text 정규식 앵커가 실제로는 매칭되지 않는 문제를 겪음).
    top_rows = body.locator(".label__columns table tbody tr").filter(has=page.locator(".group__item__name.main-text"))
    n_top = top_rows.count()
    if n_top == 0:
        _dump_debug(page, "no_region_rows_found")
        raise RuntimeError("품목 및 지역 커스텀 설정 화면에서 품목 행을 찾지 못했습니다. .auth/debug 폴더를 확인해주세요.")

    item_names = []
    for i in range(n_top):
        name_el = top_rows.nth(i).locator(".group__item__name.main-text").first
        item_names.append(name_el.inner_text().strip())

    print(f"[정보] 품목 및 지역 커스텀 설정: {n_top}개 품목의 하위 기업 여부를 확인합니다.")

    all_records: list[dict] = []
    n_with_companies = 0
    for i, item_name in enumerate(item_names):
        # 이전 품목들을 펼치면서 새 행이 앞쪽에 삽입돼 인덱스가 계속 밀리므로,
        # 매번 품목명으로 다시 찾는다 (인덱스 고정 가정 금지).
        row = body.locator(".label__columns table tbody tr").filter(
            has=page.locator(".group__item__name.main-text", has_text=item_name)
        ).first
        if row.count() == 0:
            print(f"[경고] {item_name}: 행을 다시 찾지 못해 건너뜁니다.")
            continue

        row.scroll_into_view_if_needed()
        try:
            _ensure_row_expanded(page, row)
        except PWTimeoutError:
            print(f"[경고] {item_name}: 펼치기 실패, 건너뜁니다.")
            continue

        children = row.evaluate(CHILD_ROW_WALK_JS)
        if not children:
            continue

        n_with_companies += 1
        print(f"[{i + 1}/{n_top}] {item_name}: 하위 기업 {len(children)}개 발견")
        for child in children:
            company_name = (child.get("companyName") or "").strip()
            class_name = child.get("className")
            if not company_name or not class_name:
                continue

            company_row = body.locator(f".label__columns table tbody tr.{class_name}").first
            print(f"    - {company_name} 다운로드 중...")
            try:
                _open_item_modal(page, company_row)
                modal = page.locator(".hs-codes-chart-modal")
                modal.get_by_text("All", exact=True).first.click(timeout=5000)
                page.wait_for_timeout(400)

                export_df = _download_series(page, modal, EXPORT_METRIC_LABEL)
                price_df = _download_series(page, modal, PRICE_METRIC_LABEL)

                _close_item_modal(page)
            except PWTimeoutError as e:
                print(f"      [경고] {item_name}/{company_name}: 모달/다운로드 시간 초과({e.__class__.__name__}). 건너뜁니다.")
                _dump_debug(page, "company_download_timeout")
                try:
                    _close_item_modal(page)
                except PWTimeoutError:
                    pass
                continue

            export_month = _month_end_rows(export_df)
            price_month = _month_end_rows(price_df)
            price_by_date = dict(zip(price_month["date"], price_month["value"]))

            for _, r in export_month.iterrows():
                all_records.append(
                    {
                        "품목명": item_name,
                        "기업명": company_name,
                        "기준일": r["date"].strftime("%Y-%m-%d"),
                        "수출금액": r["value"],
                        "단가": price_by_date.get(r["date"]),
                    }
                )

    print(
        f"[정보] 하위 기업 보유 품목 {n_with_companies}/{n_top}개, "
        f"총 {len(all_records)}개 기업x월 레코드 추출."
    )
    return all_records


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
            records = scrape_all_items(page)

            company_records = scrape_company_breakdowns(page)
        finally:
            context.close()

    # (품목명, 기준일) 중복 제거 - 혹시 모를 중복 대비, 나중 값 우선
    seen = {}
    for r in records:
        seen[(r["품목명"], r["기준일"])] = r
    unique_records = list(seen.values())

    append_snapshot(unique_records)

    if company_records:
        seen_company = {}
        for r in company_records:
            seen_company[(r["품목명"], r["기업명"], r["기준일"])] = r
        unique_company_records = list(seen_company.values())
        append_company_snapshot(unique_company_records)
    else:
        print("[정보] 하위 기업이 설정된 품목이 없어 기업별 데이터 저장은 건너뜁니다.")


if __name__ == "__main__":
    main()
