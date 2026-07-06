"""
수출입 데이터 — 투자 시그널 보드 (EPIC Finance 연동)

자산운용사 매니저가 품목을 하나씩 조회하지 않아도 "오늘 어떤 품목을 봐야 하는지"
첫 화면에서 바로 파악하도록 만든 대시보드. 데이터는 trade_history_long.csv 하나만
사실 소스로 쓰고, 파일에 없는 값(기업별 수출액, HS코드, 지역 기여도, 컨센서스 등)은
절대 임의로 만들어내지 않는다 — 매핑 정보가 준비되기 전까지는 빈 값/안내 문구로 남긴다.

실행: streamlit run app.py
"""

import subprocess
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils_data import (
    BASE_DIR,
    DataLoadError,
    build_pm_summary,
    compute_item_metrics,
    generate_comment,
    get_categories,
    get_data_status,
    get_hs_code,
    get_missing_items,
    get_related_companies,
    get_top_n,
    load_favorites,
    load_history,
    load_item_mapping,
    toggle_favorite,
)

st.set_page_config(page_title="수출입 데이터 — 투자 시그널 보드", layout="wide")

# ---------- 팔레트 (기존 eps_revision 대시보드와 동일) ----------
POSITIVE = "#00c87a"
NEGATIVE = "#ff4060"
WARNING_ORANGE = "#ffaa00"
SECONDARY = "#546080"
CARD_BG = "#1b2430"
PRICE_COLOR = "#39c0ff"
VOLUME_COLOR = "#b388ff"

SCRAPER_PATH = BASE_DIR / "scrape_bigfinance.py"
PAGE_SIZE_STEP = 12
MINI_CHART_MONTHS = 18

st.markdown(
    f"""
    <style>
    div[data-testid="stButton"] button {{
        background-color: {CARD_BG};
        color: #fff;
        border: 1px solid #2a3646;
    }}
    div[data-testid="stButton"] button:hover {{
        border-color: {SECONDARY};
        color: #fff;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------- 데이터 로딩 (캐시) ----------
@st.cache_data
def _load() -> tuple[pd.DataFrame, bool]:
    return load_history()


@st.cache_data
def _load_with_metrics() -> pd.DataFrame:
    df, _ = _load()
    return compute_item_metrics(df)


# ---------- 포맷 헬퍼 ----------
def _fmt_amount(v) -> str:
    if pd.isna(v):
        return "N/A"
    return f"${v:,.0f}"


def _fmt_pct_text(v) -> str:
    return f"{v:+.1f}%" if pd.notna(v) else "N/A"


def _fmt_pct_color(v) -> tuple[str, str]:
    if pd.isna(v):
        return "N/A", SECONDARY
    return f"{v:+.2f}%", (POSITIVE if v >= 0 else NEGATIVE)


# ---------- 데이터 로드 & 에러 처리 ----------
try:
    history_df, has_decade = _load()
    metrics_df = _load_with_metrics()
except DataLoadError as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

mapping_df = load_item_mapping(history_df)
latest_df = metrics_df.sort_values("date").groupby("item_name", as_index=False).tail(1)
missing_items = get_missing_items(history_df, mapping_df)
data_status = get_data_status(history_df, missing_items)

if "favorites" not in st.session_state:
    st.session_state.favorites = load_favorites()
if "view" not in st.session_state:
    st.session_state.view = "board"
if "selected_item" not in st.session_state:
    st.session_state.selected_item = None
if "page_size" not in st.session_state:
    st.session_state.page_size = PAGE_SIZE_STEP
if "selected_category" not in st.session_state:
    st.session_state.selected_category = "전체"


# ---------- 상태바 (모든 화면 상단 고정) ----------
def render_status_bar() -> None:
    last_updated = (
        data_status["last_updated"].strftime("%Y-%m-%d %H:%M") if data_status["last_updated"] else "알 수 없음"
    )
    next_update = (
        data_status["next_update_estimate"].strftime("%Y-%m-%d") if data_status["next_update_estimate"] else "알 수 없음"
    )
    prelim_txt = "잠정치" if data_status["is_preliminary"] else "확정치"
    if data_status["missing_count"]:
        missing_txt = f"⚠ 누락/수집실패 의심 {data_status['missing_count']}개 품목"
        missing_color = WARNING_ORANGE
    else:
        missing_txt = "누락 없음"
        missing_color = SECONDARY
    st.markdown(
        f"""
        <div style="background:{CARD_BG};border-radius:8px;padding:10px 18px;margin-bottom:14px;
        font-size:12.5px;color:{SECONDARY};display:flex;flex-wrap:wrap;gap:8px 22px;align-items:center;">
          <span style="color:#fff;">데이터 기준: <b>{data_status['latest_period']}</b> {prelim_txt}</span>
          <span>마지막 업데이트: {last_updated}</span>
          <span>출처: {data_status['source_label']}</span>
          <span style="color:{missing_color};">{missing_txt}</span>
          <span>다음 업데이트 예정(스케줄 기준): {next_update}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if data_status["is_preliminary"]:
        st.caption("주의: 당월 잠정치는 향후 확정치 발표 시 수정될 수 있습니다.")


# ---------- Top10 / Watchlist 테이블 공용 ----------
def _table_display_df(rows_df: pd.DataFrame, with_comment: bool = False) -> pd.DataFrame:
    data = {
        "품목명": rows_df["item_name"].values,
        "HS코드": [get_hs_code(mapping_df, n) or "미매핑" for n in rows_df["item_name"]],
        "최근월 수출금액": [_fmt_amount(v) for v in rows_df["export_amount"]],
        "MoM": [_fmt_pct_text(v) for v in rows_df["mom"]],
        "YoY": [_fmt_pct_text(v) for v in rows_df["yoy"]],
        "3개월평균 YoY": [_fmt_pct_text(v) for v in rows_df["ma3_yoy"]],
        "단가 YoY": [_fmt_pct_text(v) for v in rows_df["price_yoy"]],
        "물량 YoY": [_fmt_pct_text(v) for v in rows_df["volume_yoy"]],
        "기준월": [str(p) for p in rows_df["period"]],
        "잠정/확정": ["잠정치"] * len(rows_df),
    }
    if with_comment:
        data["자동 코멘트"] = [generate_comment(r) for _, r in rows_df.iterrows()]
    return pd.DataFrame(data)


def _render_selectable_table(rows_df: pd.DataFrame, key: str, with_comment: bool = False, height: int | None = None) -> None:
    display = _table_display_df(rows_df, with_comment=with_comment)
    event = st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        height=height,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    selection = getattr(event, "selection", None) or (event.get("selection") if isinstance(event, dict) else None)
    rows = (selection or {}).get("rows") if selection else None
    if rows:
        st.session_state.selected_item = rows_df.iloc[rows[0]]["item_name"]
        st.rerun()


# ---------- 1순위: 투자 시그널 보드 ----------
BOARD_METRICS = [
    ("yoy", "🚀 수출금액 YoY 급증 Top 10"),
    ("ma3_yoy", "📈 3개월 이동평균 YoY 개선 Top 10"),
    ("price_yoy", "💰 수출단가 YoY 상승 Top 10"),
    ("volume_yoy", "📦 수출물량 YoY 증가 Top 10"),
]


def render_board() -> None:
    st.subheader("투자 시그널 보드")
    st.caption("품목을 하나씩 눌러보지 않아도 유의미한 변화가 있는 품목을 바로 확인할 수 있습니다. 행을 클릭하면 상세 페이지로 이동합니다.")

    cols = st.columns(2)
    for i, (col_key, title) in enumerate(BOARD_METRICS):
        with cols[i % 2]:
            st.markdown(f"**{title}**")
            top = get_top_n(latest_df, col_key, n=10)
            if top.empty:
                st.caption("계산 가능한 데이터가 없습니다.")
                continue
            _render_selectable_table(top, key=f"board_{col_key}", height=250)


# ---------- 6순위: Watchlist ----------
def render_watchlist() -> None:
    st.subheader("⭐ Watchlist")
    fav_view = latest_df[latest_df["item_name"].isin(st.session_state.favorites)]
    if fav_view.empty:
        st.info("Watchlist가 비어 있습니다. '전체 품목' 탭에서 ☆ 버튼을 눌러 추가해주세요.")
        return
    _render_selectable_table(fav_view, key="watchlist_table", with_comment=True)
    st.caption("추후 기업별 매핑이 정리되면 관련 기업, 컨센서스 대비 괴리, 데이터 신뢰도 컬럼을 추가할 예정입니다.")


# ---------- 전체 품목 (검색/필터/카드) ----------
def _related_companies_str(item_name: str) -> str:
    return " ".join(get_related_companies(mapping_df, item_name))


def render_card(row: pd.Series) -> None:
    item_name = row["item_name"]
    companies = get_related_companies(mapping_df, item_name)
    company_txt = ", ".join(companies) if companies else "매핑된 기업 없음 (정리 예정)"

    yoy_txt, yoy_color = _fmt_pct_color(row["yoy"])
    mom_txt, mom_color = _fmt_pct_color(row["mom"])
    is_fav = item_name in st.session_state.favorites
    star = "★" if is_fav else "☆"

    with st.container():
        st.markdown(
            f"""
            <div style="background:{CARD_BG};border-radius:12px 12px 0 0;padding:16px 20px 10px 20px;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <div>
                  <div style="color:#fff;font-size:15px;font-weight:600;">{item_name}</div>
                  <div style="color:{SECONDARY};font-size:12px;">{row['category']} · {company_txt}</div>
                </div>
                <div style="text-align:right;">
                  <div style="color:#fff;font-size:18px;font-weight:700;">{_fmt_amount(row['export_amount'])}</div>
                  <div style="color:{yoy_color};font-size:12px;">YoY {yoy_txt}</div>
                  <div style="color:{mom_color};font-size:12px;">MoM {mom_txt}</div>
                </div>
              </div>
              <div style="color:{SECONDARY};font-size:11px;margin-top:6px;display:flex;gap:10px;flex-wrap:wrap;">
                <span>3개월평균YoY {_fmt_pct_text(row['ma3_yoy'])}</span>
                <span>단가YoY {_fmt_pct_text(row['price_yoy'])}</span>
                <span>물량YoY {_fmt_pct_text(row['volume_yoy'])}</span>
              </div>
              <div style="color:{SECONDARY};font-size:11px;margin-top:2px;">기준월 {row['period']} · 잠정치</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        bcol1, bcol2 = st.columns([1, 3])
        with bcol1:
            if st.button(star, key=f"fav_{item_name}", help="Watchlist 토글"):
                st.session_state.favorites = toggle_favorite(item_name)
                st.rerun()
        with bcol2:
            if st.button("상세보기", key=f"detail_{item_name}", width="stretch"):
                st.session_state.selected_item = item_name
                st.rerun()


def render_all_items() -> None:
    st.subheader("전체 품목")
    search = st.text_input(
        "품목명/기업명 검색...", label_visibility="collapsed", placeholder="품목명 또는 기업명 검색..."
    )

    categories = get_categories(mapping_df, history_df)
    category_options = ["전체", "즐겨찾기"] + categories
    cols_per_row = 6
    cat_rows = [category_options[i : i + cols_per_row] for i in range(0, len(category_options), cols_per_row)]
    for row in cat_rows:
        cols = st.columns(len(row))
        for col, cat in zip(cols, row):
            is_selected = st.session_state.selected_category == cat
            if col.button(cat, key=f"cat_{cat}", type="primary" if is_selected else "secondary", width="stretch"):
                st.session_state.selected_category = cat
                st.session_state.page_size = PAGE_SIZE_STEP
                st.rerun()

    sort_key = st.selectbox("정렬", ["수출액순", "YoY순", "MoM순", "이름순"], label_visibility="collapsed")

    view = latest_df.copy()
    if st.session_state.selected_category == "즐겨찾기":
        view = view[view["item_name"].isin(st.session_state.favorites)]
    elif st.session_state.selected_category != "전체":
        view = view[view["category"] == st.session_state.selected_category]

    if search:
        company_match = view["item_name"].apply(lambda n: search.lower() in _related_companies_str(n).lower())
        name_match = view["item_name"].str.contains(search, case=False, na=False)
        view = view[name_match | company_match]

    sort_map = {
        "수출액순": ("export_amount", False),
        "YoY순": ("yoy", False),
        "MoM순": ("mom", False),
        "이름순": ("item_name", True),
    }
    sort_col, sort_asc = sort_map[sort_key]
    view = view.sort_values(sort_col, ascending=sort_asc, na_position="last")

    st.caption(f"{len(view)}개 품목")
    if view.empty:
        st.info("조건에 맞는 품목이 없습니다.")
        return

    page_view = view.head(st.session_state.page_size)
    cols = st.columns(3)
    for i, (_, row) in enumerate(page_view.iterrows()):
        with cols[i % 3]:
            render_card(row)

    if st.session_state.page_size < len(view):
        if st.button(f"더 보기 ({st.session_state.page_size}/{len(view)})", width="stretch"):
            st.session_state.page_size += PAGE_SIZE_STEP
            st.rerun()


# ---------- 2~4순위: 품목 상세 (요약/차트/단가·물량 분해/자동코멘트/원자료) ----------
def render_detail(item_name: str) -> None:
    if st.button("← 목록으로"):
        st.session_state.selected_item = None
        st.rerun()

    item_hist = metrics_df[metrics_df["item_name"] == item_name].sort_values("date")
    if item_hist.empty:
        st.warning("이 품목의 데이터가 없습니다.")
        return
    latest = item_hist.iloc[-1]

    hs = get_hs_code(mapping_df, item_name)
    companies = get_related_companies(mapping_df, item_name)
    st.subheader(item_name)
    meta = [f"HS코드: {hs or '미매핑 (정리 예정)'}"]
    meta.append("관련 기업: " + (", ".join(companies) if companies else "매핑 데이터 정리 후 추가 예정"))
    st.caption(" | ".join(meta))

    # 1. 핵심 요약 박스
    st.markdown("##### 1. 핵심 요약")
    c1, c2, c3 = st.columns(3)
    c1.metric("최근월 수출금액", _fmt_amount(latest["export_amount"]))
    c1.metric("MoM", _fmt_pct_text(latest["mom"]))
    c2.metric("YoY", _fmt_pct_text(latest["yoy"]))
    c2.metric("3개월 이동평균 YoY", _fmt_pct_text(latest["ma3_yoy"]))
    c3.metric("수출단가 YoY", _fmt_pct_text(latest["price_yoy"]))
    c3.metric("수출물량 YoY", _fmt_pct_text(latest["volume_yoy"]))
    st.caption(f"기준월: {latest['period']} · 잠정/확정: 잠정치")

    st.markdown(
        f"""<div style="background:#132a24;border:1px solid {POSITIVE};border-radius:8px;
        padding:12px 16px;margin:10px 0;color:#d8fff0;font-size:13.5px;line-height:1.5;">
        💬 {generate_comment(latest)}
        </div>""",
        unsafe_allow_html=True,
    )

    has_price = item_hist["unit_price"].notna().any() if "unit_price" in item_hist.columns else False

    # 2. 월별 수출금액 차트
    st.markdown("##### 2. 월별 수출금액")
    fig = go.Figure(go.Bar(x=item_hist["date"], y=item_hist["export_amount"], marker_color=SECONDARY, name="수출금액"))
    fig.update_layout(template="plotly_dark", height=300, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    # 3. 3개월 이동평균 차트
    st.markdown("##### 3. 3개월 이동평균 (수출금액)")
    fig_ma = go.Figure()
    fig_ma.add_trace(go.Bar(x=item_hist["date"], y=item_hist["export_amount"], marker_color="#2a3646", name="월별 수출금액"))
    fig_ma.add_trace(
        go.Scatter(x=item_hist["date"], y=item_hist["ma3_amount"], mode="lines", line=dict(color=WARNING_ORANGE, width=2), name="3개월 이동평균")
    )
    fig_ma.update_layout(template="plotly_dark", height=300, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12))
    st.plotly_chart(fig_ma, width="stretch")

    # 4. YoY/MoM
    st.markdown("##### 4. 수출금액 YoY / MoM")
    yc, mc = st.columns(2)
    with yc:
        yoy_fig = go.Figure(go.Scatter(x=item_hist["date"], y=item_hist["yoy"], mode="lines+markers", line=dict(color=POSITIVE), name="YoY"))
        yoy_fig.update_layout(template="plotly_dark", height=270, title="YoY(%)", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(yoy_fig, width="stretch")
    with mc:
        mom_fig = go.Figure(go.Scatter(x=item_hist["date"], y=item_hist["mom"], mode="lines+markers", line=dict(color=WARNING_ORANGE), name="MoM"))
        mom_fig.update_layout(template="plotly_dark", height=270, title="MoM(%)", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(mom_fig, width="stretch")

    # 5. 수출단가 YoY
    st.markdown("##### 5. 수출단가 YoY")
    if has_price and item_hist["price_yoy"].notna().any():
        pfig = go.Figure(go.Scatter(x=item_hist["date"], y=item_hist["price_yoy"], mode="lines+markers", line=dict(color=PRICE_COLOR), name="단가 YoY"))
        pfig.update_layout(template="plotly_dark", height=270, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(pfig, width="stretch")
    else:
        st.caption("단가 데이터가 없어 생략합니다.")

    # 6. 수출물량 YoY
    st.markdown("##### 6. 수출물량 YoY (추정치: 수출금액 ÷ 단가)")
    if has_price and item_hist["volume_yoy"].notna().any():
        vfig = go.Figure(go.Scatter(x=item_hist["date"], y=item_hist["volume_yoy"], mode="lines+markers", line=dict(color=VOLUME_COLOR), name="물량 YoY"))
        vfig.update_layout(template="plotly_dark", height=270, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(vfig, width="stretch")
    else:
        st.caption("단가 데이터가 없어 물량을 역산할 수 없습니다.")

    # 7. 수출금액 증가 요인 분해
    st.markdown("##### 7. 수출금액 증가 요인 분해 (단가 vs 물량)")
    recent = item_hist.tail(12)
    if has_price and (recent["price_yoy"].notna().any() or recent["volume_yoy"].notna().any()):
        decomp_fig = go.Figure()
        decomp_fig.add_trace(go.Bar(x=recent["date"], y=recent["price_yoy"], name="단가 YoY", marker_color=PRICE_COLOR))
        decomp_fig.add_trace(go.Bar(x=recent["date"], y=recent["volume_yoy"], name="물량 YoY", marker_color=VOLUME_COLOR))
        decomp_fig.update_layout(
            barmode="group", template="plotly_dark", height=320, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12)
        )
        st.plotly_chart(decomp_fig, width="stretch")
        st.caption(
            "단가 YoY와 물량 YoY를 나란히 비교합니다. 단가 막대가 더 크면 ASP/믹스 개선, 물량 막대가 더 크면 "
            "물량 중심 성장(마진 확인 필요)으로 해석할 수 있습니다."
        )
    else:
        st.caption("단가 데이터가 없어 요인 분해를 생략합니다.")

    # 8. 원자료 테이블
    st.markdown("##### 8. 원자료 테이블")
    raw_cols = ["date", "export_amount", "unit_price", "export_volume", "mom", "yoy", "price_yoy", "volume_yoy", "ma3_yoy"]
    raw_cols = [c for c in raw_cols if c in item_hist.columns]
    raw_display = item_hist[raw_cols].sort_values("date", ascending=False).copy()
    raw_display.insert(0, "잠정/확정", "잠정치")
    raw_display.rename(
        columns={
            "date": "기준일", "export_amount": "수출금액", "unit_price": "단가", "export_volume": "물량(추정)",
            "mom": "MoM(%)", "yoy": "YoY(%)", "price_yoy": "단가YoY(%)", "volume_yoy": "물량YoY(%)", "ma3_yoy": "3개월평균YoY(%)",
        },
        inplace=True,
    )
    st.dataframe(raw_display, hide_index=True, width="stretch")


# ---------- 사이드바: 네비게이션 / 갱신 / 다운로드 ----------
with st.sidebar:
    st.title("수출입 데이터")
    st.caption("투자 시그널 보드")

    if SCRAPER_PATH.exists():
        if st.button("🔄 데이터 갱신", width="stretch"):
            # 로컬 전용 기능: scrape_bigfinance.py는 headless=False로 실제 크롬 창을 띄우고
            # 세션이 만료되어 있으면 터미널에서 로그인 대기(input())를 한다. subprocess.run은
            # `streamlit run app.py`를 실행한 바로 그 터미널의 stdin/stdout을 그대로 물려받으므로
            # 그 창에서 로그인하면 된다. Streamlit Cloud 같은 서버 환경에는 크롬/터미널이 없어
            # 이 버튼이 동작하지 않는다 - 로컬 실행 전용.
            with st.spinner("scrape_bigfinance.py 실행 중... 크롬 창이 뜨면 필요 시 로그인해주세요 (터미널 확인)"):
                result = subprocess.run([sys.executable, str(SCRAPER_PATH)], cwd=str(BASE_DIR))
            if result.returncode == 0:
                st.cache_data.clear()
                st.success("갱신 완료.")
                st.rerun()
            else:
                st.error(f"갱신 스크립트가 오류로 종료됐습니다 (종료 코드 {result.returncode}). 터미널 로그를 확인해주세요.")
    else:
        st.caption("scrape_bigfinance.py 없음 - 수동으로 스크립트를 실행해주세요.")

    st.divider()
    nav_options = [("board", "📊 투자 시그널"), ("watchlist", "⭐ Watchlist"), ("all", "📋 전체 품목")]
    for key, label in nav_options:
        is_active = st.session_state.view == key and st.session_state.selected_item is None
        if st.button(label, key=f"nav_{key}", width="stretch", type="primary" if is_active else "secondary"):
            st.session_state.view = key
            st.session_state.selected_item = None
            st.rerun()

    st.divider()
    st.caption("다운로드")
    raw_csv = history_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("Raw Data 다운로드", raw_csv, file_name="trade_history_raw.csv", mime="text/csv", width="stretch")

    pm_df = build_pm_summary(latest_df, mapping_df)
    pm_csv = pm_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("PM Summary 다운로드", pm_csv, file_name="pm_summary.csv", mime="text/csv", width="stretch")
    st.caption("PM Summary에는 섹터/관련기업/지역/컨센서스 비교 등은 매핑 데이터 정리 후 추가될 예정입니다.")


# ---------- 메인 ----------
render_status_bar()

if st.session_state.selected_item:
    render_detail(st.session_state.selected_item)
elif st.session_state.view == "watchlist":
    render_watchlist()
elif st.session_state.view == "all":
    render_all_items()
else:
    render_board()
