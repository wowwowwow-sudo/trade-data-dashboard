"""
수출입 데이터 — 투자 시그널 보드 (EPIC Finance 연동)

자산운용사 매니저가 품목을 하나씩 조회하지 않아도 "오늘 어떤 품목을 봐야 하는지"
10초 안에 파악하도록 만든 대시보드. 데이터는 trade_history_long.csv 하나만
사실 소스로 쓰고, 파일에 없는 값(기업별 수출액, HS코드, 지역 기여도, 컨센서스 등)은
절대 임의로 만들어내지 않는다 — 매핑 정보가 준비되기 전까지는 빈 값/작은 배지로만 남긴다.

화면 카드/테이블의 금액은 축약 표시($29.0B 등)하고, 원자료 테이블·다운로드 파일은
원래 숫자를 그대로 유지한다. 전역 라이트 테마는 .streamlit/config.toml에서 관리한다.

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

# ---------- 팔레트 (라이트 테마) ----------
CARD_BG = "#FFFFFF"
CARD_BORDER = "#E5E7EB"
TEXT_MAIN = "#111827"
TEXT_SECONDARY = "#6B7280"
POSITIVE = "#10B981"
NEGATIVE = "#EF4444"
ACCENT = "#2563EB"
WARNING = "#F59E0B"
PRICE_COLOR = "#2563EB"
VOLUME_COLOR = "#7C3AED"
BADGE_BG = "#F3F4F6"
PLOTLY_TEMPLATE = "plotly_white"

SCRAPER_PATH = BASE_DIR / "scrape_bigfinance.py"
PAGE_SIZE_STEP = 12
TOP_CARD_COUNT = 8
CARD_COLS = 4

st.markdown(
    f"""
    <style>
    .signal-card {{
        background: {CARD_BG};
        border: 1px solid {CARD_BORDER};
        border-radius: 10px;
        padding: 12px 16px 10px 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        margin-bottom: 2px;
    }}
    .rank-badge {{
        font-weight: 700; color: {ACCENT}; font-size: 11.5px;
        background: #EFF4FF; padding: 2px 8px; border-radius: 999px;
    }}
    .mapping-badge {{
        font-size: 10px; color: #9CA3AF; background: {BADGE_BG};
        padding: 1px 7px; border-radius: 999px; margin-left: 6px;
    }}
    .signal-card-title {{ font-weight: 700; font-size: 14.5px; color: {TEXT_MAIN}; margin-top: 6px; }}
    .signal-card-sector {{ font-size: 11.5px; color: {TEXT_SECONDARY}; margin-top: 2px; }}
    .signal-card-amount {{ font-size: 19px; font-weight: 700; color: {TEXT_MAIN}; margin-top: 8px; }}
    .signal-card-metrics {{ display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 8px; font-size: 11px; }}
    .signal-card-metrics .m-label {{ color: {TEXT_SECONDARY}; margin-right: 3px; }}
    .signal-card-meta {{ font-size: 10.5px; color: #9CA3AF; margin-top: 8px; }}

    div[class*="st-key-fav_"] button, div[class*="st-key-link_"] button {{
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 2px !important;
        min-height: 1.6em !important;
        color: {TEXT_SECONDARY} !important;
    }}
    div[class*="st-key-link_"] button {{
        color: {ACCENT} !important;
        font-size: 12px !important;
        float: right;
    }}
    div[class*="st-key-fav_"] button {{
        font-size: 15px !important;
        float: right;
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
    """원자료/상세페이지용 - 원래 숫자 그대로."""
    if pd.isna(v):
        return "N/A"
    return f"${v:,.0f}"


def _fmt_amount_abbr(v) -> str:
    """카드/전체 품목 테이블용 - 축약 표시 ($29.0B / $151.1M)."""
    if pd.isna(v):
        return "N/A"
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e9:
        return f"{sign}${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{sign}${v / 1e3:.1f}K"
    return f"{sign}${v:.0f}"


def _fmt_pct_text(v) -> str:
    return f"{v:+.1f}%" if pd.notna(v) else "N/A"


def _fmt_pct_color(v) -> tuple[str, str]:
    if pd.isna(v):
        return "N/A", TEXT_SECONDARY
    return f"{v:+.1f}%", (POSITIVE if v >= 0 else NEGATIVE)


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
        missing_color = WARNING
    else:
        missing_txt = "누락 없음"
        missing_color = TEXT_SECONDARY
    st.markdown(
        f"""
        <div style="background:{CARD_BG};border:1px solid {CARD_BORDER};border-radius:8px;padding:9px 18px;
        margin-bottom:14px;font-size:12.5px;color:{TEXT_SECONDARY};display:flex;flex-wrap:wrap;gap:6px 22px;align-items:center;">
          <span style="color:{TEXT_MAIN};">데이터 기준: <b>{data_status['latest_period']}</b> {prelim_txt}</span>
          <span>마지막 업데이트: {last_updated}</span>
          <span>출처: {data_status['source_label']}</span>
          <span style="color:{missing_color};">{missing_txt}</span>
          <span>다음 업데이트 예정: {next_update}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------- 공용: 선택 가능한 테이블 (Top10/Watchlist/전체 테이블 공용) ----------
def _handle_selection(event, source_df: pd.DataFrame) -> None:
    selection = getattr(event, "selection", None) or (event.get("selection") if isinstance(event, dict) else None)
    rows = (selection or {}).get("rows") if selection else None
    if rows:
        st.session_state.selected_item = source_df.iloc[rows[0]]["item_name"]
        st.rerun()


def _full_table_display_df(sorted_df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Rank": range(1, len(sorted_df) + 1),
            "품목명": sorted_df["item_name"].values,
            "섹터": sorted_df["category"].values,
            "최근월 수출액": [_fmt_amount_abbr(v) for v in sorted_df["export_amount"]],
            "YoY": [_fmt_pct_text(v) for v in sorted_df["yoy"]],
            "MoM": [_fmt_pct_text(v) for v in sorted_df["mom"]],
            "3M YoY": [_fmt_pct_text(v) for v in sorted_df["ma3_yoy"]],
            "단가 YoY": [_fmt_pct_text(v) for v in sorted_df["price_yoy"]],
            "물량 YoY": [_fmt_pct_text(v) for v in sorted_df["volume_yoy"]],
            "기준월": [str(p) for p in sorted_df["period"]],
            "잠정/확정": ["잠정치"] * len(sorted_df),
        }
    )


def render_full_table(sorted_df: pd.DataFrame, key: str) -> None:
    display = _full_table_display_df(sorted_df)
    event = st.dataframe(
        display, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=key
    )
    _handle_selection(event, sorted_df)


def _watchlist_table_display_df(rows_df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "품목명": rows_df["item_name"].values,
            "HS코드": [get_hs_code(mapping_df, n) or "미매핑" for n in rows_df["item_name"]],
            "최근월 수출액": [_fmt_amount_abbr(v) for v in rows_df["export_amount"]],
            "YoY": [_fmt_pct_text(v) for v in rows_df["yoy"]],
            "MoM": [_fmt_pct_text(v) for v in rows_df["mom"]],
            "3M YoY": [_fmt_pct_text(v) for v in rows_df["ma3_yoy"]],
            "단가 YoY": [_fmt_pct_text(v) for v in rows_df["price_yoy"]],
            "물량 YoY": [_fmt_pct_text(v) for v in rows_df["volume_yoy"]],
            "기준월": [str(p) for p in rows_df["period"]],
            "잠정/확정": ["잠정치"] * len(rows_df),
            "자동 코멘트": [generate_comment(r) for _, r in rows_df.iterrows()],
        }
    )


def render_watchlist_table(rows_df: pd.DataFrame, key: str) -> None:
    display = _watchlist_table_display_df(rows_df)
    event = st.dataframe(
        display, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=key
    )
    _handle_selection(event, rows_df)


# ---------- 카드 (보드/전체 품목 공용) ----------
def render_card(row: pd.Series, rank: int | None = None, key_prefix: str = "all") -> None:
    item_name = row["item_name"]
    companies = get_related_companies(mapping_df, item_name)
    badge_html = "" if companies else '<span class="mapping-badge">기업 매핑 예정</span>'
    rank_html = f'<span class="rank-badge">#{rank}</span>' if rank else ""

    yoy_txt, yoy_color = _fmt_pct_color(row["yoy"])
    mom_txt, mom_color = _fmt_pct_color(row["mom"])
    ma3_txt, ma3_color = _fmt_pct_color(row["ma3_yoy"])
    price_txt, price_color = _fmt_pct_color(row["price_yoy"])
    vol_txt, vol_color = _fmt_pct_color(row["volume_yoy"])
    is_fav = item_name in st.session_state.favorites
    star = "★" if is_fav else "☆"

    uid = f"{key_prefix}_{item_name}_{rank}"
    with st.container(key=f"card_wrap_{uid}"):
        st.markdown(
            f"""
            <div class="signal-card">
              {rank_html}
              <div class="signal-card-title">{item_name}</div>
              <div class="signal-card-sector">{row['category']}{badge_html}</div>
              <div class="signal-card-amount">{_fmt_amount_abbr(row['export_amount'])}</div>
              <div class="signal-card-metrics">
                <span><span class="m-label">YoY</span><span style="color:{yoy_color};font-weight:600;">{yoy_txt}</span></span>
                <span><span class="m-label">MoM</span><span style="color:{mom_color};font-weight:600;">{mom_txt}</span></span>
                <span><span class="m-label">3M YoY</span><span style="color:{ma3_color};font-weight:600;">{ma3_txt}</span></span>
                <span><span class="m-label">단가YoY</span><span style="color:{price_color};font-weight:600;">{price_txt}</span></span>
                <span><span class="m-label">물량YoY</span><span style="color:{vol_color};font-weight:600;">{vol_txt}</span></span>
              </div>
              <div class="signal-card-meta">{row['period']} · 잠정치</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        b1, b2 = st.columns([1, 1])
        with b1:
            if st.button(star, key=f"fav_{uid}", help="Watchlist 토글"):
                st.session_state.favorites = toggle_favorite(item_name)
                st.rerun()
        with b2:
            if st.button("상세보기 →", key=f"link_{uid}"):
                st.session_state.selected_item = item_name
                st.rerun()


# ---------- 1순위: 투자 시그널 보드 ----------
SIGNAL_TABS = [
    ("yoy", "YoY 급증"),
    ("mom", "MoM 급증"),
    ("ma3_yoy", "3개월 추세개선"),
    ("price_yoy", "단가 상승"),
    ("volume_yoy", "물량 증가"),
]


def render_board() -> None:
    st.subheader("투자 시그널 보드")
    tabs = st.tabs([label for _, label in SIGNAL_TABS])
    for tab, (col_key, label) in zip(tabs, SIGNAL_TABS):
        with tab:
            ranked = get_top_n(latest_df, col_key, n=len(latest_df))
            if ranked.empty:
                st.caption("계산 가능한 데이터가 없습니다.")
                continue

            top_cards = ranked.head(TOP_CARD_COUNT)
            cols = st.columns(CARD_COLS)
            for i, (_, row) in enumerate(top_cards.iterrows()):
                with cols[i % CARD_COLS]:
                    render_card(row, rank=i + 1, key_prefix=col_key)

            st.markdown(f"###### 전체 품목 ({label} 기준 정렬, 컬럼 클릭 시 재정렬 가능)")
            render_full_table(ranked, key=f"board_table_{col_key}")


# ---------- 6순위: Watchlist ----------
def render_watchlist() -> None:
    st.subheader("⭐ Watchlist")
    fav_view = latest_df[latest_df["item_name"].isin(st.session_state.favorites)]
    if fav_view.empty:
        st.info("Watchlist가 비어 있습니다. '전체 품목' 탭에서 ☆ 버튼을 눌러 추가해주세요.")
        return
    render_watchlist_table(fav_view, key="watchlist_table")
    st.caption("추후 기업별 매핑이 정리되면 관련 기업, 컨센서스 대비 괴리, 데이터 신뢰도 컬럼을 추가할 예정입니다.")


# ---------- 전체 품목 (검색/필터/카드+테이블) ----------
def _related_companies_str(item_name: str) -> str:
    return " ".join(get_related_companies(mapping_df, item_name))


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
    meta_lines = [f'<div><span style="font-weight:600;">HS코드:</span> {hs or "미매핑"}</div>']
    if companies:
        meta_lines.append(
            f'<div><span style="font-weight:600;">관련 기업:</span> {", ".join(companies)}</div>'
        )
    st.markdown(
        f'<div style="color:{TEXT_MAIN};font-size:13.5px;line-height:1.7;margin:4px 0 10px;">'
        + "".join(meta_lines) + '</div>',
        unsafe_allow_html=True,
    )

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
        f"""<div style="background:#ECFDF5;border:1px solid {POSITIVE};border-radius:8px;
        padding:12px 16px;margin:10px 0;color:#065F46;font-size:13.5px;line-height:1.5;">
        💬 {generate_comment(latest)}
        </div>""",
        unsafe_allow_html=True,
    )

    has_price = item_hist["unit_price"].notna().any() if "unit_price" in item_hist.columns else False

    # 2. 월별 수출금액 차트
    st.markdown("##### 2. 월별 수출금액")
    fig = go.Figure(go.Bar(x=item_hist["date"], y=item_hist["export_amount"], marker_color=ACCENT, name="수출금액"))
    fig.update_layout(template=PLOTLY_TEMPLATE, height=300, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    # 3. 단가 추이
    st.markdown("##### 3. 단가 추이")
    if has_price and item_hist["unit_price"].notna().any():
        price_fig = go.Figure(
            go.Scatter(x=item_hist["date"], y=item_hist["unit_price"], mode="lines+markers", line=dict(color=PRICE_COLOR, width=2), name="단가")
        )
        price_fig.update_layout(template=PLOTLY_TEMPLATE, height=300, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(price_fig, width="stretch")
    else:
        st.caption("단가 데이터가 없어 생략합니다.")

    # 4. 3개월 이동평균 차트
    st.markdown("##### 4. 3개월 이동평균 (수출금액)")
    fig_ma = go.Figure()
    fig_ma.add_trace(go.Bar(x=item_hist["date"], y=item_hist["export_amount"], marker_color="#DBEAFE", name="월별 수출금액"))
    fig_ma.add_trace(
        go.Scatter(x=item_hist["date"], y=item_hist["ma3_amount"], mode="lines", line=dict(color=WARNING, width=2), name="3개월 이동평균")
    )
    fig_ma.update_layout(template=PLOTLY_TEMPLATE, height=300, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12))
    st.plotly_chart(fig_ma, width="stretch")

    # 5. YoY/MoM
    st.markdown("##### 5. 수출금액 YoY / MoM")
    yc, mc = st.columns(2)
    with yc:
        yoy_fig = go.Figure(go.Scatter(x=item_hist["date"], y=item_hist["yoy"], mode="lines+markers", line=dict(color=POSITIVE), name="YoY"))
        yoy_fig.update_layout(template=PLOTLY_TEMPLATE, height=270, title="YoY(%)", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(yoy_fig, width="stretch")
    with mc:
        mom_fig = go.Figure(go.Scatter(x=item_hist["date"], y=item_hist["mom"], mode="lines+markers", line=dict(color=WARNING), name="MoM"))
        mom_fig.update_layout(template=PLOTLY_TEMPLATE, height=270, title="MoM(%)", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(mom_fig, width="stretch")

    # 6. 수출단가 YoY
    st.markdown("##### 6. 수출단가 YoY")
    if has_price and item_hist["price_yoy"].notna().any():
        pfig = go.Figure(go.Scatter(x=item_hist["date"], y=item_hist["price_yoy"], mode="lines+markers", line=dict(color=PRICE_COLOR), name="단가 YoY"))
        pfig.update_layout(template=PLOTLY_TEMPLATE, height=270, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(pfig, width="stretch")
    else:
        st.caption("단가 데이터가 없어 생략합니다.")

    # 7. 수출물량 YoY
    st.markdown("##### 7. 수출물량 YoY (추정치: 수출금액 ÷ 단가)")
    if has_price and item_hist["volume_yoy"].notna().any():
        vfig = go.Figure(go.Scatter(x=item_hist["date"], y=item_hist["volume_yoy"], mode="lines+markers", line=dict(color=VOLUME_COLOR), name="물량 YoY"))
        vfig.update_layout(template=PLOTLY_TEMPLATE, height=270, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(vfig, width="stretch")
    else:
        st.caption("단가 데이터가 없어 물량을 역산할 수 없습니다.")

    # 8. 수출금액 증가 요인 분해
    st.markdown("##### 8. 수출금액 증가 요인 분해 (단가 vs 물량)")
    recent = item_hist.tail(12)
    if has_price and (recent["price_yoy"].notna().any() or recent["volume_yoy"].notna().any()):
        decomp_fig = go.Figure()
        decomp_fig.add_trace(go.Bar(x=recent["date"], y=recent["price_yoy"], name="단가 YoY", marker_color=PRICE_COLOR))
        decomp_fig.add_trace(go.Bar(x=recent["date"], y=recent["volume_yoy"], name="물량 YoY", marker_color=VOLUME_COLOR))
        decomp_fig.update_layout(
            barmode="group", template=PLOTLY_TEMPLATE, height=320, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12)
        )
        st.plotly_chart(decomp_fig, width="stretch")
        st.caption(
            "단가 YoY와 물량 YoY를 나란히 비교합니다. 단가 막대가 더 크면 ASP/믹스 개선, 물량 막대가 더 크면 "
            "물량 중심 성장(마진 확인 필요)으로 해석할 수 있습니다."
        )
    else:
        st.caption("단가 데이터가 없어 요인 분해를 생략합니다.")

    # 9. 원자료 테이블
    st.markdown("##### 9. 원자료 테이블")
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


# ---------- 사이드바: 간결한 네비게이션 / 다운로드 ----------
with st.sidebar:
    st.markdown(f"##### 수출입 데이터")

    nav_options = [("board", "📊 투자 시그널"), ("watchlist", "⭐ Watchlist"), ("all", "📋 전체 품목")]
    for key, label in nav_options:
        is_active = st.session_state.view == key and st.session_state.selected_item is None
        if st.button(label, key=f"nav_{key}", width="stretch", type="primary" if is_active else "secondary"):
            st.session_state.view = key
            st.session_state.selected_item = None
            st.rerun()

    st.divider()

    with st.expander("⬇ 다운로드"):
        raw_csv = history_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Raw Data", raw_csv, file_name="trade_history_raw.csv", mime="text/csv", width="stretch")

        pm_df = build_pm_summary(latest_df, mapping_df)
        pm_csv = pm_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("PM Summary", pm_csv, file_name="pm_summary.csv", mime="text/csv", width="stretch")
        st.caption("PM Summary: 섹터/관련기업/컨센서스 비교는 매핑 정리 후 추가 예정")

    with st.expander("⚙ 설정"):
        if SCRAPER_PATH.exists():
            if st.button("🔄 데이터 갱신", width="stretch"):
                # 로컬 전용: scrape_bigfinance.py는 headless=False로 크롬 창을 띄우고 세션 만료 시
                # 터미널에서 로그인 대기(input())한다. subprocess.run은 `streamlit run app.py`를
                # 실행한 그 터미널의 stdin을 그대로 물려받으므로 그 창에서 로그인하면 된다.
                # Streamlit Cloud 등 서버 환경에는 크롬/터미널이 없어 동작하지 않는다.
                with st.spinner("scrape_bigfinance.py 실행 중... 크롬 창이 뜨면 필요 시 로그인해주세요 (터미널 확인)"):
                    result = subprocess.run([sys.executable, str(SCRAPER_PATH)], cwd=str(BASE_DIR))
                if result.returncode == 0:
                    st.cache_data.clear()
                    st.success("갱신 완료.")
                    st.rerun()
                else:
                    st.error(f"갱신 스크립트가 오류로 종료됐습니다 (종료 코드 {result.returncode}).")
        else:
            st.caption("scrape_bigfinance.py 없음")


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
