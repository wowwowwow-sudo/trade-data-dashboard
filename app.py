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
    SIGNAL_SCORE_WEIGHTS,
    STRONG_YOY_PCT,
    TAG_DESCRIPTIONS,
    TAG_NEGATIVE_TURN,
    DataLoadError,
    build_pm_summary,
    build_related_company_table,
    classify_alert_reason,
    compute_company_metrics,
    compute_decade_item_metrics,
    compute_item_metrics,
    enrich_signal_board,
    generate_detail_commentary,
    get_categories,
    get_company_breakdown,
    get_company_history,
    get_data_status,
    get_hs_code,
    get_missing_items,
    get_related_companies,
    get_top_n,
    load_company_history,
    load_decade_history,
    load_favorites,
    load_history,
    load_item_mapping,
    search_company_data,
    search_related_company_items,
    toggle_favorite,
)

st.set_page_config(page_title="수출입 데이터 — 투자 시그널 보드", layout="wide")

# ---------- 팔레트 (디자인 토큰 - Phase 0에서 확정, 기관 리서치 톤) ----------
BG_MAIN = "#F8FAFC"
CARD_BG = "#FFFFFF"
CARD_BORDER = "#E5E7EB"
TEXT_MAIN = "#0F172A"
TEXT_SECONDARY = "#64748B"
ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
# 한국 시장 관례: 상승/긍정 = 빨강, 하락/부정 = 파랑 (미국식 초록/빨강과 반대)
POSITIVE = "#DC2626"
NEGATIVE = "#2563EB"
WARNING = "#F59E0B"
PRICE_COLOR = "#2563EB"
VOLUME_COLOR = "#7C3AED"
BADGE_BG = "#F3F4F6"
ACTIVE_BG = "#EFF6FF"
ACTIVE_TEXT = "#1D4ED8"
ACTIVE_BORDER = "#2563EB"
PLOTLY_TEMPLATE = "plotly_white"

SCRAPER_PATH = BASE_DIR / "scrape_bigfinance.py"
PAGE_SIZE_STEP = 12
TOP_CARD_COUNT = 8
CARD_COLS = 4

# ---------- 주식 리서치 대시보드 연계 (딥링크 발신) ----------
# .streamlit/secrets.toml에 RESEARCH_DASHBOARD_URL이 없으면 링크를 아예 보여주지 않는다 (에러 금지).
try:
    RESEARCH_DASHBOARD_URL = str(st.secrets.get("RESEARCH_DASHBOARD_URL", "")).strip().rstrip("/")
except Exception:
    RESEARCH_DASHBOARD_URL = ""

st.markdown(
    f"""
    <style>
    /* ===== 전역 톤 ===== */
    .stApp {{ background: {BG_MAIN}; }}

    /* ===== 카드 (보드/전체품목/품목상세/기업상세 공용) ===== */
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
        background: {ACTIVE_BG}; padding: 2px 8px; border-radius: 999px;
    }}
    .mapping-badge {{
        font-size: 10px; color: {TEXT_SECONDARY}; background: {BADGE_BG};
        padding: 1px 7px; border-radius: 999px; margin-left: 6px;
    }}
    .signal-card-title {{ font-weight: 700; font-size: 14.5px; color: {TEXT_MAIN}; margin-top: 6px; }}
    .signal-card-sector {{ font-size: 11.5px; color: {TEXT_SECONDARY}; margin-top: 2px; }}
    .signal-card-amount {{ font-size: 19px; font-weight: 700; color: {TEXT_MAIN}; margin-top: 8px; }}
    .signal-card-metrics {{ display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 8px; font-size: 11px; }}
    .signal-card-metrics .m-label {{ color: {TEXT_SECONDARY}; margin-right: 3px; }}
    .signal-card-meta {{ font-size: 10.5px; color: {TEXT_SECONDARY}; margin-top: 8px; }}

    /* ===== KPI 요약 카드 (투자 시그널 보드 최상단) ===== */
    .kpi-card {{
        background: {CARD_BG};
        border: 1px solid {CARD_BORDER};
        border-radius: 10px;
        padding: 14px 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }}
    .kpi-card-value {{ font-size: 24px; font-weight: 700; color: {TEXT_MAIN}; }}
    .kpi-card-label {{ font-size: 11.5px; color: {TEXT_SECONDARY}; margin-top: 4px; }}
    .kpi-card-value--sm {{ font-size: 17px; font-weight: 700; }}

    /* ===== 전체 품목 - 카테고리 필터 (작은 pill/chip) ===== */
    div[class*="st-key-category_filter_row"] .stButton button {{
        border-radius: 999px;
        padding: 2px 14px;
        font-size: 12px;
        min-height: 1.8em;
    }}

    /* ===== 카드 내부 버튼 (즐겨찾기/상세보기 링크형) ===== */
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

    /* ===== 사이드바 슬림화 + 네비게이션 (전체 품목의 카테고리 필터 버튼과
       겹치지 않도록 section[data-testid="stSidebar"] 범위로만 한정) ===== */
    section[data-testid="stSidebar"] {{
        min-width: 235px !important;
        max-width: 235px !important;
    }}
    section[data-testid="stSidebar"] .stButton button {{
        min-height: 2.1em;
        padding: 4px 12px;
        font-size: 13px;
        border-radius: 6px;
        text-align: left;
        justify-content: flex-start;
    }}
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {{
        gap: 0.35rem;
    }}
    section[data-testid="stSidebar"] button[kind="secondary"] {{
        background: transparent;
        border: 1px solid transparent;
        color: {TEXT_MAIN};
    }}
    section[data-testid="stSidebar"] button[kind="secondary"]:hover {{
        background: {BADGE_BG};
        color: {TEXT_MAIN};
        border-color: {BADGE_BG};
    }}
    section[data-testid="stSidebar"] button[kind="primary"] {{
        background: {ACTIVE_BG} !important;
        color: {ACTIVE_TEXT} !important;
        border: 1px solid {ACTIVE_BG} !important;
        border-left: 3px solid {ACTIVE_BORDER} !important;
        font-weight: 600;
        box-shadow: none !important;
    }}
    section[data-testid="stSidebar"] button[kind="primary"]:hover {{
        background: {ACTIVE_BG} !important;
        color: {ACTIVE_TEXT} !important;
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


@st.cache_data
def _load_company_with_metrics() -> pd.DataFrame:
    df = load_company_history()
    return compute_company_metrics(df)


@st.cache_data
def _load_decade() -> pd.DataFrame:
    return load_decade_history()


@st.cache_data
def _load_decade_with_metrics() -> pd.DataFrame:
    df = _load_decade()
    return compute_decade_item_metrics(df)


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
enriched_df = enrich_signal_board(latest_df)  # Signal Score/해석 태그 - 투자 시그널 보드와 전체 품목 화면이 공유
missing_items = get_missing_items(history_df, mapping_df)
data_status = get_data_status(history_df, missing_items)

# "품목 커스텀 설정"(10/20일 단위) - scrape_bigfinance.py를 아직 안 돌렸으면 빈 DataFrame이고,
# 이는 정상 상태이므로 앱을 막지 않는다(company 데이터와 동일한 방침).
decade_metrics_df = _load_decade_with_metrics()
decade_latest_df = (
    decade_metrics_df.sort_values("date").groupby("item_name", as_index=False).tail(1)
    if not decade_metrics_df.empty
    else decade_metrics_df
)

if "favorites" not in st.session_state:
    st.session_state.favorites = load_favorites()
if "view" not in st.session_state:
    st.session_state.view = "board"
if "selected_item" not in st.session_state:
    st.session_state.selected_item = None
if "selected_company" not in st.session_state:
    st.session_state.selected_company = None  # (item_name, company_name) 튜플 또는 None
if "selected_decade_item" not in st.session_state:
    st.session_state.selected_decade_item = None  # "품목 커스텀 설정"(10일 단위) 상세 화면용
if "page_size" not in st.session_state:
    st.session_state.page_size = PAGE_SIZE_STEP
if "selected_category" not in st.session_state:
    st.session_state.selected_category = "전체"
if "selected_kpi" not in st.session_state:
    st.session_state.selected_kpi = None
if "chart_period" not in st.session_state:
    st.session_state.chart_period = "5Y"
if "items_view_mode" not in st.session_state:
    st.session_state.items_view_mode = "테이블형"


# ---------- 딥링크 수신 (?hs=<HS코드>, ?category=<카테고리명>) ----------
# 세션당 한 번만 적용 - 그렇지 않으면 사용자가 딥링크로 들어온 뒤 다른 화면으로 이동해도
# URL의 쿼리 파라미터가 남아있는 한 매 rerun마다 다시 그 화면으로 되돌아가 버린다.
if "deep_link_applied" not in st.session_state:
    st.session_state.deep_link_applied = True
    qp_hs = st.query_params.get("hs")
    qp_category = st.query_params.get("category")

    if qp_hs:
        hs_query = str(qp_hs).strip()
        match = mapping_df[
            mapping_df["hs_code"].apply(lambda v: hs_query in [c.strip() for c in str(v or "").split(";") if c.strip()])
        ]
        if not match.empty:
            st.session_state.selected_item = match.iloc[0]["item_name"]
        # 존재하지 않는 hs코드는 조용히 무시 (에러 없음)

    if qp_category:
        category_query = str(qp_category).strip()
        if category_query in get_categories(mapping_df, history_df):
            st.session_state.view = "all"
            st.session_state.selected_category = category_query
        # 존재하지 않는 카테고리명도 조용히 무시


# ---------- 상태바 (모든 화면 상단 고정) ----------
def _status_item(text: str, color: str = TEXT_SECONDARY) -> str:
    return f'<span style="color:{color};">{text}</span>'


def render_status_bar() -> None:
    last_updated = (
        data_status["last_updated"].strftime("%Y-%m-%d %H:%M") if data_status["last_updated"] else "알 수 없음"
    )
    next_update = (
        data_status["next_update_estimate"].strftime("%Y-%m-%d") if data_status["next_update_estimate"] else "알 수 없음"
    )
    prelim_txt = "잠정치" if data_status["is_preliminary"] else "확정치"
    if data_status["missing_count"]:
        missing_txt = f"누락/수집실패 의심 {data_status['missing_count']}개 품목"
        missing_color = WARNING
    else:
        missing_txt = "누락 없음"
        missing_color = TEXT_SECONDARY

    items = [
        _status_item(f'데이터 기준: <b style="color:{TEXT_MAIN};">{data_status["latest_period"]}</b> {prelim_txt}'),
        _status_item(f"마지막 업데이트: {last_updated}"),
        _status_item(f"출처: {data_status['source_label']}"),
        _status_item(missing_txt, missing_color),
        _status_item(f"다음 업데이트 예정: {next_update}"),
    ]
    separator = f'<span style="color:{CARD_BORDER};">|</span>'
    st.markdown(
        f"""
        <div style="background:{CARD_BG};border:1px solid {CARD_BORDER};border-radius:6px;padding:6px 14px;
        margin-bottom:12px;font-size:12px;color:{TEXT_SECONDARY};display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;">
          {f" {separator} ".join(items)}
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


def _watchlist_table_display_df(rows_df: pd.DataFrame, tag_map: dict) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "관심 품목": rows_df["item_name"].values,
            "섹터": rows_df["category"].values,
            "관련 기업": [_related_companies_str(n) or "–" for n in rows_df["item_name"]],
            "최근월 수출액": [_fmt_amount_abbr(v) for v in rows_df["export_amount"]],
            "YoY": [_fmt_pct_text(v) for v in rows_df["yoy"]],
            "MoM": [_fmt_pct_text(v) for v in rows_df["mom"]],
            "3M YoY": [_fmt_pct_text(v) for v in rows_df["ma3_yoy"]],
            "단가 YoY": [_fmt_pct_text(v) for v in rows_df["price_yoy"]],
            "물량 YoY": [_fmt_pct_text(v) for v in rows_df["volume_yoy"]],
            "알림 사유": [classify_alert_reason(r) for _, r in rows_df.iterrows()],
            "해석 태그": [tag_map.get(n, "–") for n in rows_df["item_name"]],
        }
    )


def render_watchlist_table(rows_df: pd.DataFrame, key: str) -> None:
    tag_map = dict(zip(enriched_df["item_name"], enriched_df["tag"]))
    display = _watchlist_table_display_df(rows_df, tag_map)
    pct_cols = ["YoY", "MoM", "3M YoY", "단가 YoY", "물량 YoY"]
    styler = display.style.map(_pct_text_color, subset=pct_cols)
    event = st.dataframe(
        styler, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=key
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


# ---------- 카드 (품목 상세 화면 - 기업별 수출) ----------
SPARKLINE_MONTHS = 12


def render_company_card(row: pd.Series, item_name: str, company_metrics_df: pd.DataFrame) -> None:
    company_name = row["company_name"]
    yoy_txt, yoy_color = _fmt_pct_color(row["yoy"])
    mom_txt, mom_color = _fmt_pct_color(row["mom"])

    uid = f"company_{item_name}_{company_name}"
    with st.container(key=f"company_card_wrap_{uid}"):
        st.markdown(
            f"""
            <div class="signal-card">
              <div class="signal-card-title">{company_name}</div>
              <div class="signal-card-amount">{_fmt_amount_abbr(row['export_amount'])}</div>
              <div class="signal-card-metrics">
                <span><span class="m-label">YoY</span><span style="color:{yoy_color};font-weight:600;">{yoy_txt}</span></span>
                <span><span class="m-label">MoM</span><span style="color:{mom_color};font-weight:600;">{mom_txt}</span></span>
              </div>
              <div class="signal-card-meta">{row['period']} · 잠정치</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        hist = get_company_history(company_metrics_df, item_name, company_name).tail(SPARKLINE_MONTHS)
        if len(hist) >= 2:
            bar_colors = [WARNING if i == len(hist) - 1 else ACCENT for i in range(len(hist))]
            spark = go.Figure(go.Bar(x=hist["date"], y=hist["export_amount"], marker_color=bar_colors))
            spark.update_layout(
                height=70,
                margin=dict(l=0, r=0, t=2, b=0),
                template=PLOTLY_TEMPLATE,
                xaxis_visible=False,
                yaxis_visible=False,
                showlegend=False,
            )
            st.plotly_chart(spark, width="stretch", key=f"spark_{uid}", config={"displayModeBar": False})

        if st.button("상세보기 →", key=f"company_link_{uid}", width="stretch"):
            st.session_state.selected_company = (item_name, company_name)
            st.rerun()


# ---------- 1순위: 투자 시그널 보드 ----------
SIGNAL_TABS = [
    ("yoy", "YoY 급증"),
    ("mom", "MoM 급증"),
    ("ma3_yoy", "3개월 추세개선"),
    ("price_yoy", "단가 상승"),
    ("volume_yoy", "물량 증가"),
]

TOP10_COLS = 5  # KPI 카드 개수와 맞춤


# ---------- KPI 요약 (오늘의 요약) ----------
def _kpi_definitions(enriched_df: pd.DataFrame) -> list[dict]:
    """KPI 카드 5개의 정의. 카운트 계산과 클릭 시 드릴다운 목록이 항상 일치하도록
    같은 mask를 두 군데서 재사용한다."""
    favorites = st.session_state.favorites
    return [
        {
            "id": "surge",
            "label": "급증 품목 수",
            "mask": enriched_df["yoy"] >= STRONG_YOY_PCT,
            "sort_col": "yoy",
            "ascending": False,
        },
        {
            "id": "watchlist",
            "label": "Watchlist 알림 수",
            "mask": enriched_df["item_name"].isin(favorites),
            "sort_col": "yoy",
            "ascending": False,
        },
        {
            "id": "price_up",
            "label": "단가 상승 품목 수",
            "mask": enriched_df["price_yoy"] > 0,
            "sort_col": "price_yoy",
            "ascending": False,
        },
        {
            "id": "volume_up",
            "label": "물량 증가 품목 수",
            "mask": enriched_df["volume_yoy"] > 0,
            "sort_col": "volume_yoy",
            "ascending": False,
        },
        {
            "id": "negative_turn",
            "label": "마이너스 전환 품목 수",
            "mask": enriched_df["tag"] == TAG_NEGATIVE_TURN,
            "sort_col": "yoy",
            "ascending": True,
        },
    ]


def _render_kpi_drilldown(kpi: dict, enriched_df: pd.DataFrame) -> None:
    subset = enriched_df[kpi["mask"]].sort_values(kpi["sort_col"], ascending=kpi["ascending"])
    st.markdown(f"###### {kpi['label']} 목록 ({len(subset)}개)")
    if subset.empty:
        st.caption("해당하는 품목이 없습니다.")
        return
    display = pd.DataFrame(
        {
            "품목": subset["item_name"].values,
            "섹터": subset["category"].values,
            "최근월 수출액": [_fmt_amount_abbr(v) for v in subset["export_amount"]],
            "YoY": [_fmt_pct_text(v) for v in subset["yoy"]],
            "MoM": [_fmt_pct_text(v) for v in subset["mom"]],
            "단가 YoY": [_fmt_pct_text(v) for v in subset["price_yoy"]],
            "물량 YoY": [_fmt_pct_text(v) for v in subset["volume_yoy"]],
            "해석 태그": subset["tag"].values,
        }
    )
    pct_cols = ["YoY", "MoM", "단가 YoY", "물량 YoY"]
    styler = display.style.map(_pct_text_color, subset=pct_cols)
    event = st.dataframe(
        styler, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=f"kpi_list_{kpi['id']}"
    )
    _handle_selection(event, subset)


def render_kpi_summary(enriched_df: pd.DataFrame) -> None:
    kpis = _kpi_definitions(enriched_df)
    cols = st.columns(TOP10_COLS)
    for col, kpi in zip(cols, kpis):
        with col:
            count = int(kpi["mask"].sum())
            st.markdown(
                f"""
                <div class="kpi-card">
                  <div class="kpi-card-value">{count}</div>
                  <div class="kpi-card-label">{kpi['label']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            is_open = st.session_state.selected_kpi == kpi["id"]
            if st.button("숨기기 ▴" if is_open else "목록 보기 ▾", key=f"kpi_toggle_{kpi['id']}", width="stretch"):
                st.session_state.selected_kpi = None if is_open else kpi["id"]
                st.rerun()

    active_kpi = next((k for k in kpis if k["id"] == st.session_state.selected_kpi), None)
    if active_kpi:
        _render_kpi_drilldown(active_kpi, enriched_df)


# ---------- 오늘의 투자 시그널 Top 10 테이블 ----------
def _signal_top10_display_df(top10: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "순위": range(1, len(top10) + 1),
            "Signal Score": top10["signal_score"].values,
            "해석 태그": top10["tag"].values,
            "품목": top10["item_name"].values,
            "섹터": top10["category"].values,
            "관련 기업": [_related_companies_str(n) or "–" for n in top10["item_name"]],
            "최근월 수출액": [_fmt_amount_abbr(v) for v in top10["export_amount"]],
            "YoY": [_fmt_pct_text(v) for v in top10["yoy"]],
            "MoM": [_fmt_pct_text(v) for v in top10["mom"]],
            "3M YoY": [_fmt_pct_text(v) for v in top10["ma3_yoy"]],
            "단가 YoY": [_fmt_pct_text(v) for v in top10["price_yoy"]],
            "물량 YoY": [_fmt_pct_text(v) for v in top10["volume_yoy"]],
        }
    )


def render_signal_score_legend() -> None:
    """Signal Score 계산식/해석 태그 의미를 실제 코드의 가중치·설명과 동일하게 보여준다
    (하드코딩된 별도 문구가 아니라 utils_data.py의 SIGNAL_SCORE_WEIGHTS/TAG_DESCRIPTIONS를
    그대로 사용 - 로직이 바뀌면 이 설명도 자동으로 같이 바뀐다)."""
    with st.expander("Signal Score / 해석 태그 기준 보기"):
        weight_label = {"yoy": "YoY", "mom": "MoM", "ma3_yoy": "3M YoY", "price_yoy": "단가 YoY"}
        formula = " + ".join(f"{w:.0%}×{weight_label[k]} 순위" for k, w in SIGNAL_SCORE_WEIGHTS.items())
        st.markdown(
            f"**Signal Score** = {formula} (각 지표를 전체 품목 중 순위로 환산해 0~100점, 결측은 중립값 50으로 대체)"
        )
        st.markdown("**해석 태그** (아래 순서대로 먼저 맞는 조건 하나만 표시)")
        for tag, desc in TAG_DESCRIPTIONS.items():
            st.markdown(f"- **{tag}**: {desc}")


def _pct_text_color(val) -> str:
    """+/- 접두사로만 판단해서 색을 입힌다 (과하지 않게 - 텍스트 색만, 배경 없음)."""
    if isinstance(val, str) and val.startswith("+"):
        return f"color: {POSITIVE}"
    if isinstance(val, str) and val.startswith("-"):
        return f"color: {NEGATIVE}"
    return ""


def render_signal_top10_table(enriched_df: pd.DataFrame) -> None:
    st.markdown("###### 오늘의 투자 시그널 Top 10 (Signal Score 기준)")
    render_signal_score_legend()
    top10 = enriched_df.sort_values("signal_score", ascending=False).head(10).reset_index(drop=True)
    if top10.empty:
        st.caption("계산 가능한 데이터가 없습니다.")
        return

    display = _signal_top10_display_df(top10)
    pct_cols = ["YoY", "MoM", "3M YoY", "단가 YoY", "물량 YoY"]
    styler = display.style.map(_pct_text_color, subset=pct_cols).format({"Signal Score": "{:.1f}"})
    event = st.dataframe(
        styler, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="signal_top10_table"
    )
    _handle_selection(event, top10)


def render_board() -> None:
    st.subheader("투자 시그널 보드")

    render_kpi_summary(enriched_df)
    render_signal_top10_table(enriched_df)
    st.divider()

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


# ---------- 기업 검색 (실제 수출 데이터 + 관련 종목 참고 텍스트) ----------
def render_company_search() -> None:
    st.subheader("🏢 기업 검색")
    query = st.text_input(
        "기업명 검색...", label_visibility="collapsed", placeholder="기업명을 입력하세요 (예: 삼성전기)"
    )
    if not query:
        st.caption(
            "기업명을 입력하면 실제 수출 데이터(품목 및 지역 커스텀 설정에 등록된 기업)와, "
            "관련 종목으로만 참고 표시된 품목을 함께 보여줍니다."
        )
        return

    company_metrics_df = _load_company_with_metrics()
    data_matches = search_company_data(company_metrics_df, query)
    related_matches = search_related_company_items(mapping_df, query)

    if not data_matches.empty:
        st.markdown(f"##### 실제 수출 데이터 있음 ({len(data_matches)}건)")
        data_matches = data_matches.sort_values("export_amount", ascending=False).reset_index(drop=True)
        cols = st.columns(CARD_COLS)
        for i, (_, row) in enumerate(data_matches.iterrows()):
            with cols[i % CARD_COLS]:
                st.caption(row["item_name"])
                render_company_card(row, row["item_name"], company_metrics_df)
    else:
        st.caption("실제 수출 데이터(기업별 커스텀 설정)는 없습니다.")

    if related_matches:
        st.markdown(f"##### 관련 종목으로 언급된 품목 ({len(related_matches)}건 · 참고용, 실제 수출 데이터 아님)")
        for r in related_matches:
            matched_str = ", ".join(r["matched_companies"])
            if st.button(f"{r['item_name']} — {matched_str}", key=f"related_item_{r['item_name']}"):
                st.session_state.selected_item = r["item_name"]
                st.rerun()
    elif data_matches.empty:
        st.info("검색 결과가 없습니다.")


# ---------- 전체 품목 (검색/필터/카드+테이블) ----------
def _related_companies_str(item_name: str) -> str:
    return " ".join(get_related_companies(mapping_df, item_name))


def _all_items_table_display_df(view: pd.DataFrame, tag_map: dict) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "품목": view["item_name"].values,
            "섹터": view["category"].values,
            "최근월 수출액": [_fmt_amount_abbr(v) for v in view["export_amount"]],
            "YoY": [_fmt_pct_text(v) for v in view["yoy"]],
            "MoM": [_fmt_pct_text(v) for v in view["mom"]],
            "3M YoY": [_fmt_pct_text(v) for v in view["ma3_yoy"]],
            "단가 YoY": [_fmt_pct_text(v) for v in view["price_yoy"]],
            "물량 YoY": [_fmt_pct_text(v) for v in view["volume_yoy"]],
            "관련 기업": [_related_companies_str(n) or "–" for n in view["item_name"]],
            "해석 태그": [tag_map.get(n, "–") for n in view["item_name"]],
        }
    )


def render_all_items_table(view: pd.DataFrame) -> None:
    tag_map = dict(zip(enriched_df["item_name"], enriched_df["tag"]))
    display = _all_items_table_display_df(view, tag_map)
    pct_cols = ["YoY", "MoM", "3M YoY", "단가 YoY", "물량 YoY"]
    styler = display.style.map(_pct_text_color, subset=pct_cols)
    event = st.dataframe(
        styler, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="all_items_table"
    )
    _handle_selection(event, view)


def render_all_items() -> None:
    st.subheader("전체 품목")
    search = st.text_input(
        "품목명/기업명 검색...", label_visibility="collapsed", placeholder="품목명 또는 기업명 검색..."
    )

    categories = get_categories(mapping_df, history_df)
    category_options = ["전체", "즐겨찾기"] + categories
    cols_per_row = 6
    cat_rows = [category_options[i : i + cols_per_row] for i in range(0, len(category_options), cols_per_row)]
    with st.container(key="category_filter_row"):
        for row in cat_rows:
            cols = st.columns(len(row))
            for col, cat in zip(cols, row):
                is_selected = st.session_state.selected_category == cat
                if col.button(cat, key=f"cat_{cat}", type="primary" if is_selected else "secondary", width="stretch"):
                    st.session_state.selected_category = cat
                    st.session_state.page_size = PAGE_SIZE_STEP
                    st.rerun()

    sc1, sc2 = st.columns([3, 1])
    with sc1:
        sort_key = st.selectbox("정렬", ["수출액순", "YoY순", "MoM순", "이름순"], label_visibility="collapsed")
    with sc2:
        st.segmented_control("보기", ["테이블형", "카드형"], key="items_view_mode", label_visibility="collapsed")

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

    if st.session_state.items_view_mode == "테이블형":
        render_all_items_table(view)
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


# ---------- 품목 상세 - 기간 선택 (모든 차트 공통 적용) ----------
CHART_PERIOD_OPTIONS = ["1Y", "3Y", "5Y", "전체"]
CHART_PERIOD_YEARS = {"1Y": 1, "3Y": 3, "5Y": 5, "전체": None}


def _filter_by_period(hist: pd.DataFrame, period_label: str) -> pd.DataFrame:
    years = CHART_PERIOD_YEARS.get(period_label)
    if not years or hist.empty:
        return hist
    cutoff = hist["date"].max() - pd.DateOffset(years=years)
    return hist[hist["date"] >= cutoff]


# ---------- 품목 상세 - KPI 카드 5개 ----------
def render_detail_kpi_cards(latest: pd.Series) -> None:
    yoy_txt, yoy_color = _fmt_pct_color(latest["yoy"])
    mom_txt, mom_color = _fmt_pct_color(latest["mom"])
    ma3_txt, ma3_color = _fmt_pct_color(latest["ma3_yoy"])
    price_txt, price_color = _fmt_pct_color(latest.get("price_yoy"))
    vol_txt, vol_color = _fmt_pct_color(latest.get("volume_yoy"))

    cards = [
        ("최근월 수출금액", f'<span style="color:{TEXT_MAIN};">{_fmt_amount(latest["export_amount"])}</span>'),
        ("YoY", f'<span style="color:{yoy_color};">{yoy_txt}</span>'),
        ("MoM", f'<span style="color:{mom_color};">{mom_txt}</span>'),
        ("3개월 이동평균 YoY", f'<span style="color:{ma3_color};">{ma3_txt}</span>'),
        (
            "단가·물량",
            f'<span style="color:{price_color};">단가 {price_txt}</span> · <span style="color:{vol_color};">물량 {vol_txt}</span>',
        ),
    ]
    cols = st.columns(len(cards))
    for col, (label, value_html) in zip(cols, cards):
        with col:
            st.markdown(
                f"""
                <div class="kpi-card">
                  <div class="kpi-card-value kpi-card-value--sm">{value_html}</div>
                  <div class="kpi-card-label">{label}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


# ---------- 품목 상세 - 관련 기업 테이블 ----------
def render_related_company_table(item_name: str, item_export_amount: float, company_metrics_df: pd.DataFrame) -> None:
    table = build_related_company_table(item_name, mapping_df, company_metrics_df, item_export_amount)
    if table.empty:
        return

    st.markdown("###### 관련 기업")
    display = pd.DataFrame(
        {
            "기업명": table["company_name"].values,
            "관련 품목": table["related_items"].values,
            "최근월 수출액": [_fmt_amount_abbr(v) for v in table["export_amount"]],
            "YoY": [_fmt_pct_text(v) for v in table["yoy"]],
            "MoM": [_fmt_pct_text(v) for v in table["mom"]],
            "해석": table["note"].values,
        }
    )

    column_config = None
    if RESEARCH_DASHBOARD_URL:
        # 종목코드가 확인된 기업만 링크가 채워진다 (미확인 기업은 빈 값 - 링크 없음).
        display["리서치 대시보드"] = [
            f"{RESEARCH_DASHBOARD_URL}?stock={code}" if code else None for code in table["stock_code"]
        ]
        column_config = {
            "리서치 대시보드": st.column_config.LinkColumn("리서치 대시보드", display_text="종목 상세 →")
        }

    styler = display.style.map(_pct_text_color, subset=["YoY", "MoM"])
    event = st.dataframe(
        styler,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key=f"related_company_table_{item_name}",
        column_config=column_config,
    )
    selection = getattr(event, "selection", None) or (event.get("selection") if isinstance(event, dict) else None)
    rows = (selection or {}).get("rows") if selection else None
    if rows:
        company_row = table.iloc[rows[0]]
        if pd.notna(company_row["export_amount"]):
            st.session_state.selected_company = (item_name, company_row["company_name"])
            st.rerun()
        else:
            st.caption(f"'{company_row['company_name']}'은(는) 참고용 매핑만 있어 실측 상세 데이터가 없습니다.")


# ---------- 2~4순위: 품목 상세 (정보 → KPI → 투자 해석 → 관련 기업 → 차트 → 원자료) ----------
def render_detail(item_name: str) -> None:
    if st.button("← 목록으로"):
        st.session_state.selected_item = None
        st.rerun()

    item_hist = metrics_df[metrics_df["item_name"] == item_name].sort_values("date")
    if item_hist.empty:
        st.warning("이 품목의 데이터가 없습니다.")
        return
    latest = item_hist.iloc[-1]

    # 1. 상단 정보
    hs = get_hs_code(mapping_df, item_name)
    companies = get_related_companies(mapping_df, item_name)
    st.subheader(item_name)
    meta_lines = [f'<div><span style="font-weight:600;">HS코드:</span> {hs or "미매핑"}</div>']
    if companies:
        meta_lines.append(f'<div><span style="font-weight:600;">관련 기업:</span> {", ".join(companies)}</div>')
    meta_lines.append(f'<div><span style="font-weight:600;">기준월:</span> {latest["period"]} · 잠정치</div>')
    st.markdown(
        f'<div style="color:{TEXT_MAIN};font-size:13.5px;line-height:1.7;margin:4px 0 10px;">'
        + "".join(meta_lines) + '</div>',
        unsafe_allow_html=True,
    )

    # 2. KPI 카드 5개
    render_detail_kpi_cards(latest)

    # 3. 투자 해석 박스
    st.markdown(
        f"""<div style="background:{ACTIVE_BG};border:1px solid {ACCENT};border-radius:8px;
        padding:12px 16px;margin:10px 0;color:{ACCENT_DARK};font-size:13.5px;line-height:1.5;">
        💬 {generate_detail_commentary(latest, companies)}
        </div>""",
        unsafe_allow_html=True,
    )

    # 4. 관련 기업 테이블 (참고용 매핑 전체 + 실측 데이터 있는 기업은 클릭해서 상세로)
    company_metrics_df = _load_company_with_metrics()
    render_related_company_table(item_name, latest["export_amount"], company_metrics_df)

    has_price = item_hist["unit_price"].notna().any() if "unit_price" in item_hist.columns else False

    # 5. 탭: 차트 분석 / 기업별 / 원자료
    tab_charts, tab_company, tab_raw = st.tabs(["차트 분석", "기업별", "원자료"])

    with tab_charts:
        st.segmented_control("기간", CHART_PERIOD_OPTIONS, key="chart_period")
        hist = _filter_by_period(item_hist, st.session_state.chart_period)

        # 핵심 차트 3개 - 1열 전체 폭
        st.markdown("###### 월별 수출금액")
        fig = go.Figure(go.Bar(x=hist["date"], y=hist["export_amount"], marker_color=ACCENT, name="수출금액"))
        fig.update_layout(template=PLOTLY_TEMPLATE, height=340, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")

        st.markdown("###### 단가 추이")
        if has_price and hist["unit_price"].notna().any():
            price_fig = go.Figure(
                go.Scatter(
                    x=hist["date"], y=hist["unit_price"], mode="lines+markers", line=dict(color=PRICE_COLOR, width=2), name="단가"
                )
            )
            price_fig.update_layout(template=PLOTLY_TEMPLATE, height=340, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(price_fig, width="stretch")
        else:
            st.caption("단가 데이터가 없어 생략합니다.")

        st.markdown("###### 3개월 이동평균")
        fig_ma = go.Figure()
        fig_ma.add_trace(go.Bar(x=hist["date"], y=hist["export_amount"], marker_color="#DBEAFE", name="월별 수출금액"))
        fig_ma.add_trace(
            go.Scatter(x=hist["date"], y=hist["ma3_amount"], mode="lines", line=dict(color=WARNING, width=2), name="3개월 이동평균")
        )
        fig_ma.update_layout(
            template=PLOTLY_TEMPLATE, height=340, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12)
        )
        st.plotly_chart(fig_ma, width="stretch")

        # 보조 차트 - 하단 2열 그리드 (좁으면 자동 1열)
        st.markdown("###### 보조 지표")
        yc, mc = st.columns(2)
        with yc:
            yoy_fig = go.Figure(go.Scatter(x=hist["date"], y=hist["yoy"], mode="lines+markers", line=dict(color=POSITIVE), name="YoY"))
            yoy_fig.update_layout(template=PLOTLY_TEMPLATE, height=270, title="수출금액 YoY(%)", margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(yoy_fig, width="stretch")
        with mc:
            mom_fig = go.Figure(go.Scatter(x=hist["date"], y=hist["mom"], mode="lines+markers", line=dict(color=WARNING), name="MoM"))
            mom_fig.update_layout(template=PLOTLY_TEMPLATE, height=270, title="수출금액 MoM(%)", margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(mom_fig, width="stretch")

        pc, vc = st.columns(2)
        with pc:
            if has_price and hist["price_yoy"].notna().any():
                pfig = go.Figure(
                    go.Scatter(x=hist["date"], y=hist["price_yoy"], mode="lines+markers", line=dict(color=PRICE_COLOR), name="단가 YoY")
                )
                pfig.update_layout(template=PLOTLY_TEMPLATE, height=270, title="수출단가 YoY(%)", margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(pfig, width="stretch")
            else:
                st.caption("단가 데이터가 없어 생략합니다.")
        with vc:
            if has_price and hist["volume_yoy"].notna().any():
                vfig = go.Figure(
                    go.Scatter(x=hist["date"], y=hist["volume_yoy"], mode="lines+markers", line=dict(color=VOLUME_COLOR), name="물량 YoY")
                )
                vfig.update_layout(
                    template=PLOTLY_TEMPLATE, height=270, title="수출물량 YoY(%, 추정치)", margin=dict(l=10, r=10, t=40, b=10)
                )
                st.plotly_chart(vfig, width="stretch")
            else:
                st.caption("단가 데이터가 없어 물량을 역산할 수 없습니다.")

        recent = hist.tail(12)
        if has_price and (recent["price_yoy"].notna().any() or recent["volume_yoy"].notna().any()):
            st.markdown("###### 수출금액 증가 요인 분해 (단가 vs 물량, 최근 12개월)")
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

    with tab_company:
        # EPIC Finance "품목 및 지역 커스텀 설정"에서 하위 기업 행이 실제로 설정된
        # 품목만 존재 (137개 품목 중 일부뿐) - 없으면 안내만 표시.
        company_view = get_company_breakdown(company_metrics_df, item_name)
        if company_view.empty:
            st.caption("이 품목은 EPIC Finance에 하위 기업(지역) 커스텀 설정이 없어 기업별 데이터가 없습니다.")
        else:
            company_view = company_view.sort_values("export_amount", ascending=False).reset_index(drop=True)
            cols = st.columns(CARD_COLS)
            for i, (_, crow) in enumerate(company_view.iterrows()):
                with cols[i % CARD_COLS]:
                    render_company_card(crow, item_name, company_metrics_df)

    with tab_raw:
        with st.expander("원자료 테이블 보기"):
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


# ---------- 기업 상세 (품목 상세와 유사한 레이아웃, 4단계 드릴다운) ----------
def render_company_detail(item_name: str, company_name: str) -> None:
    if st.button("← 품목 상세로"):
        st.session_state.selected_company = None
        st.rerun()

    company_metrics_df = _load_company_with_metrics()
    hist = get_company_history(company_metrics_df, item_name, company_name).sort_values("date")
    if hist.empty:
        st.warning("이 기업의 데이터가 없습니다.")
        return
    latest = hist.iloc[-1]

    st.subheader(f"{company_name}")
    st.markdown(
        f'<div style="color:{TEXT_MAIN};font-size:13.5px;line-height:1.7;margin:4px 0 10px;">'
        f'<div><span style="font-weight:600;">품목:</span> {item_name}</div></div>',
        unsafe_allow_html=True,
    )

    # 1. 핵심 요약
    st.markdown("##### 1. 핵심 요약")
    c1, c2, c3 = st.columns(3)
    c1.metric("최근월 수출금액", _fmt_amount(latest["export_amount"]))
    c1.metric("MoM", _fmt_pct_text(latest["mom"]))
    c2.metric("YoY", _fmt_pct_text(latest["yoy"]))
    c2.metric("3개월 이동평균 YoY", _fmt_pct_text(latest["ma3_yoy"]))
    c3.metric("수출단가 YoY", _fmt_pct_text(latest["price_yoy"]))
    c3.metric("수출물량 YoY", _fmt_pct_text(latest["volume_yoy"]))
    st.caption(f"기준월: {latest['period']} · 잠정/확정: 잠정치")

    has_price = hist["unit_price"].notna().any() if "unit_price" in hist.columns else False

    # 2. 월별 수출금액 차트
    st.markdown("##### 2. 월별 수출금액")
    fig = go.Figure(go.Bar(x=hist["date"], y=hist["export_amount"], marker_color=ACCENT, name="수출금액"))
    fig.update_layout(template=PLOTLY_TEMPLATE, height=300, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    # 3. 단가 추이
    st.markdown("##### 3. 단가 추이")
    if has_price and hist["unit_price"].notna().any():
        price_fig = go.Figure(
            go.Scatter(x=hist["date"], y=hist["unit_price"], mode="lines+markers", line=dict(color=PRICE_COLOR, width=2), name="단가")
        )
        price_fig.update_layout(template=PLOTLY_TEMPLATE, height=300, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(price_fig, width="stretch")
    else:
        st.caption("단가 데이터가 없어 생략합니다.")

    # 4. 수출금액 YoY / MoM
    st.markdown("##### 4. 수출금액 YoY / MoM")
    yc, mc = st.columns(2)
    with yc:
        yoy_fig = go.Figure(go.Scatter(x=hist["date"], y=hist["yoy"], mode="lines+markers", line=dict(color=POSITIVE), name="YoY"))
        yoy_fig.update_layout(template=PLOTLY_TEMPLATE, height=270, title="YoY(%)", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(yoy_fig, width="stretch")
    with mc:
        mom_fig = go.Figure(go.Scatter(x=hist["date"], y=hist["mom"], mode="lines+markers", line=dict(color=WARNING), name="MoM"))
        mom_fig.update_layout(template=PLOTLY_TEMPLATE, height=270, title="MoM(%)", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(mom_fig, width="stretch")

    # 5. 원자료 테이블
    st.markdown("##### 5. 원자료 테이블")
    raw_cols = ["date", "export_amount", "unit_price", "export_volume", "mom", "yoy", "price_yoy", "volume_yoy", "ma3_yoy"]
    raw_cols = [c for c in raw_cols if c in hist.columns]
    raw_display = hist[raw_cols].sort_values("date", ascending=False).copy()
    raw_display.insert(0, "잠정/확정", "잠정치")
    raw_display.rename(
        columns={
            "date": "기준일", "export_amount": "수출금액", "unit_price": "단가", "export_volume": "물량(추정)",
            "mom": "MoM(%)", "yoy": "YoY(%)", "price_yoy": "단가YoY(%)", "volume_yoy": "물량YoY(%)", "ma3_yoy": "3개월평균YoY(%)",
        },
        inplace=True,
    )
    st.dataframe(raw_display, hide_index=True, width="stretch")


# ---------- "품목 커스텀 설정" (10일 단위 - 상순/중순/하순) ----------
def _decade_table_display_df(view: pd.DataFrame) -> pd.DataFrame:
    has_price = "unit_price" in view.columns
    return pd.DataFrame(
        {
            "품목": view["item_name"].values,
            "섹터": view["category"].values,
            "기준일": view["date"].dt.strftime("%Y-%m-%d").values,
            "최신 수출액": [_fmt_amount_abbr(v) for v in view["export_amount"]],
            "YoY": [_fmt_pct_text(v) for v in view["yoy"]],
            "갱신 대비": [_fmt_pct_text(v) for v in view["prev_change_pct"]],
            "단가": [_fmt_amount(v) for v in view["unit_price"]] if has_price else ["–"] * len(view),
            "단가 YoY": [_fmt_pct_text(v) for v in view["price_yoy"]],
            "관련 기업": [_related_companies_str(n) or "–" for n in view["item_name"]],
        }
    )


def render_items_decade_table(view: pd.DataFrame) -> None:
    display = _decade_table_display_df(view)
    pct_cols = ["YoY", "갱신 대비", "단가 YoY"]
    styler = display.style.map(_pct_text_color, subset=pct_cols)
    event = st.dataframe(
        styler, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="items_decade_table"
    )
    selection = getattr(event, "selection", None) or (event.get("selection") if isinstance(event, dict) else None)
    rows = (selection or {}).get("rows") if selection else None
    if rows:
        st.session_state.selected_decade_item = view.iloc[rows[0]]["item_name"]
        st.rerun()


def render_items_decade() -> None:
    st.subheader("품목 커스텀 설정 (10일 단위)")
    st.caption("EPIC Finance \"품목 커스텀 설정\" 화면 기준 - 상순/중순/하순 갱신마다 반영됩니다.")

    if decade_latest_df.empty:
        st.info(
            "아직 수집된 데이터가 없습니다. 왼쪽 사이드바 \"설정\"에서 "
            "\"데이터 갱신\"을 먼저 실행해주세요."
        )
        return

    search = st.text_input(
        "품목명/기업명 검색...", label_visibility="collapsed", placeholder="품목명 또는 기업명 검색...", key="decade_search"
    )

    categories = get_categories(mapping_df, decade_latest_df)
    category_options = ["전체"] + categories
    cols_per_row = 6
    cat_rows = [category_options[i : i + cols_per_row] for i in range(0, len(category_options), cols_per_row)]
    with st.container(key="decade_category_filter_row"):
        for row in cat_rows:
            cols = st.columns(len(row))
            for col, cat in zip(cols, row):
                is_selected = st.session_state.get("decade_selected_category", "전체") == cat
                if col.button(cat, key=f"decade_cat_{cat}", type="primary" if is_selected else "secondary", width="stretch"):
                    st.session_state.decade_selected_category = cat
                    st.rerun()

    sort_key = st.selectbox("정렬", ["수출액순", "YoY순", "갱신 대비순", "이름순"], label_visibility="collapsed", key="decade_sort")

    view = decade_latest_df.copy()
    selected_category = st.session_state.get("decade_selected_category", "전체")
    if selected_category != "전체":
        view = view[view["category"] == selected_category]

    if search:
        company_match = view["item_name"].apply(lambda n: search.lower() in _related_companies_str(n).lower())
        name_match = view["item_name"].str.contains(search, case=False, na=False)
        view = view[name_match | company_match]

    sort_map = {
        "수출액순": ("export_amount", False),
        "YoY순": ("yoy", False),
        "갱신 대비순": ("prev_change_pct", False),
        "이름순": ("item_name", True),
    }
    sort_col, sort_asc = sort_map[sort_key]
    view = view.sort_values(sort_col, ascending=sort_asc, na_position="last")

    st.caption(f"{len(view)}개 품목 · 최신 기준일 {view['date'].max().strftime('%Y-%m-%d')}")
    if view.empty:
        st.info("조건에 맞는 품목이 없습니다.")
        return

    render_items_decade_table(view)


def render_decade_detail(item_name: str) -> None:
    if st.button("← 목록으로", key="decade_back"):
        st.session_state.selected_decade_item = None
        st.rerun()

    hist = decade_metrics_df[decade_metrics_df["item_name"] == item_name].sort_values("date")
    if hist.empty:
        st.warning("이 품목의 데이터가 없습니다.")
        return
    latest = hist.iloc[-1]

    companies = get_related_companies(mapping_df, item_name)
    st.subheader(item_name)
    meta_lines = [f'<div><span style="font-weight:600;">기준일:</span> {latest["date"].strftime("%Y-%m-%d")} · 잠정치</div>']
    if companies:
        meta_lines.append(f'<div><span style="font-weight:600;">관련 기업:</span> {", ".join(companies)}</div>')
    st.markdown(
        f'<div style="color:{TEXT_MAIN};font-size:13.5px;line-height:1.7;margin:4px 0 10px;">'
        + "".join(meta_lines) + '</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("최신 수출금액", _fmt_amount(latest["export_amount"]))
    c2.metric("YoY (전년 동순)", _fmt_pct_text(latest["yoy"]))
    c3.metric("갱신 대비", _fmt_pct_text(latest["prev_change_pct"]))

    has_price = hist["unit_price"].notna().any() if "unit_price" in hist.columns else False

    st.markdown("###### 수출금액 추이 (10일/20일/월말)")
    fig = go.Figure(go.Bar(x=hist["date"], y=hist["export_amount"], marker_color=ACCENT, name="수출금액"))
    fig.update_layout(template=PLOTLY_TEMPLATE, height=340, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    if has_price:
        st.markdown("###### 단가 추이")
        price_fig = go.Figure(
            go.Scatter(x=hist["date"], y=hist["unit_price"], mode="lines+markers", line=dict(color=PRICE_COLOR, width=2), name="단가")
        )
        price_fig.update_layout(template=PLOTLY_TEMPLATE, height=300, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(price_fig, width="stretch")

    with st.expander("원자료 테이블 보기"):
        raw_cols = ["date", "export_amount", "unit_price", "prev_change_pct", "yoy", "price_yoy"]
        raw_cols = [c for c in raw_cols if c in hist.columns]
        raw_display = hist[raw_cols].sort_values("date", ascending=False).copy()
        raw_display.rename(
            columns={
                "date": "기준일", "export_amount": "수출금액", "unit_price": "단가",
                "prev_change_pct": "갱신 대비(%)", "yoy": "YoY(%)", "price_yoy": "단가YoY(%)",
            },
            inplace=True,
        )
        st.dataframe(raw_display, hide_index=True, width="stretch")


# ---------- 사이드바: 간결한 네비게이션 / 다운로드 ----------
with st.sidebar:
    st.markdown(f"##### 수출입 데이터")

    nav_options = [
        ("board", "투자 시그널"),
        ("watchlist", "Watchlist"),
        ("all", "전체 품목"),
        ("items_decade", "품목 커스텀 설정(10/20일)"),
        ("company_search", "기업 검색"),
    ]
    for key, label in nav_options:
        is_active = (
            st.session_state.view == key
            and st.session_state.selected_item is None
            and st.session_state.selected_company is None
            and st.session_state.selected_decade_item is None
        )
        if st.button(label, key=f"nav_{key}", width="stretch", type="primary" if is_active else "secondary"):
            st.session_state.view = key
            st.session_state.selected_item = None
            st.session_state.selected_company = None
            st.session_state.selected_decade_item = None
            st.rerun()

    st.divider()

    with st.expander("다운로드"):
        raw_csv = history_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Raw Data", raw_csv, file_name="trade_history_raw.csv", mime="text/csv", width="stretch")

        pm_df = build_pm_summary(latest_df, mapping_df)
        pm_csv = pm_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("PM Summary", pm_csv, file_name="pm_summary.csv", mime="text/csv", width="stretch")
        st.caption("PM Summary: 섹터/관련기업/컨센서스 비교는 매핑 정리 후 추가 예정")

    with st.expander("설정"):
        if SCRAPER_PATH.exists():
            if st.button("데이터 갱신", width="stretch"):
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
        st.caption("\"데이터 갱신\" 한 번으로 월말 데이터와 품목 커스텀 설정(10/20일) 데이터를 함께 갱신합니다.")


# ---------- 메인 ----------
render_status_bar()

if st.session_state.selected_company:
    render_company_detail(*st.session_state.selected_company)
elif st.session_state.selected_item:
    render_detail(st.session_state.selected_item)
elif st.session_state.selected_decade_item:
    render_decade_detail(st.session_state.selected_decade_item)
elif st.session_state.view == "watchlist":
    render_watchlist()
elif st.session_state.view == "all":
    render_all_items()
elif st.session_state.view == "items_decade":
    render_items_decade()
elif st.session_state.view == "company_search":
    render_company_search()
else:
    render_board()
