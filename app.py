"""
수출입 데이터 대시보드 - 품목+지역 커스텀 (EPIC Finance 연동)

데이터는 trade_history_long.csv 하나만 사실 소스로 쓴다. 파일에 없는 값
(단가, 기업별/지역별 수출액 등)은 절대 임의로 만들어내지 않고, 없으면
해당 UI를 생략하거나 "데이터 없음"으로 표시한다.

실행: streamlit run app.py
"""

import base64
import io
import subprocess
import sys

import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from utils_data import (
    BASE_DIR,
    DataLoadError,
    compute_item_metrics,
    get_categories,
    get_missing_items,
    get_related_companies,
    load_favorites,
    load_history,
    load_item_mapping,
    toggle_favorite,
)

st.set_page_config(page_title="수출입 데이터 — 품목+지역 커스텀", layout="wide")

# ---------- 팔레트 (기존 eps_revision 대시보드와 동일) ----------
POSITIVE = "#00c87a"
NEGATIVE = "#ff4060"
WARNING_ORANGE = "#ffaa00"
SECONDARY = "#546080"
CARD_BG = "#1b2430"

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


@st.cache_data(show_spinner=False)
def _make_mini_chart(dates: tuple, values: tuple) -> str:
    fig, ax = plt.subplots(figsize=(2.4, 0.9), dpi=130)
    n = len(values)
    colors = [SECONDARY] + [
        POSITIVE if values[i] >= values[i - 1] else NEGATIVE for i in range(1, n)
    ]
    edgecolors = ["none"] * (n - 1) + ["#ffffff"]
    linewidths = [0] * (n - 1) + [1.6]
    ax.bar(range(n), values, color=colors, width=0.65, edgecolor=edgecolors, linewidth=linewidths)
    ax.axis("off")
    fig.patch.set_alpha(0)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", transparent=True, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _fmt_pct(v) -> tuple[str, str]:
    if pd.isna(v):
        return "N/A", SECONDARY
    color = POSITIVE if v >= 0 else NEGATIVE
    return f"{v:+.2f}%", color


def _fmt_amount(v) -> str:
    if pd.isna(v):
        return "N/A"
    return f"${v:,.0f}"


# ---------- 데이터 로드 & 에러 처리 ----------
try:
    history_df, has_decade = _load()
    metrics_df = _load_with_metrics()
except DataLoadError as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

mapping_df = load_item_mapping(history_df)
latest_df = metrics_df.sort_values("date").groupby("item_name", as_index=False).tail(1)
latest_date = history_df["date"].max().strftime("%Y-%m-%d")
missing_items = get_missing_items(history_df, mapping_df)

if "favorites" not in st.session_state:
    st.session_state.favorites = load_favorites()
if "selected_category" not in st.session_state:
    st.session_state.selected_category = "전체"
if "selected_item" not in st.session_state:
    st.session_state.selected_item = None
if "page_size" not in st.session_state:
    st.session_state.page_size = PAGE_SIZE_STEP


# ---------- 헤더 ----------
header_left, header_right = st.columns([5, 1])
with header_left:
    st.title("수출입 데이터 — 품목+지역 커스텀")
    st.caption(f"epicfinance 연동 | 월간 잠정치 | 최신: {latest_date} | {history_df['item_name'].nunique()}개 품목")
with header_right:
    st.write("")
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

# ---------- 요약 카드 ----------
n_up = (latest_df["yoy"] > 0).sum()
n_down = (latest_df["yoy"] < 0).sum()
s1, s2, s3, s4 = st.columns(4)
s1.metric("추적 품목", f"{history_df['item_name'].nunique()}개")
s2.metric("최신 기준일", latest_date)
s3.metric("YoY 증가 품목", f"{n_up}개")
s4.metric("YoY 감소 품목", f"{n_down}개")

if missing_items:
    names = ", ".join(f"{i['item_name']}({i['reason']})" for i in missing_items)
    st.markdown(
        f"""<div style="background:#2a2013;border:1px solid {WARNING_ORANGE};border-radius:8px;
        padding:10px 16px;margin:8px 0;color:{WARNING_ORANGE};font-size:13px;">
        ⚠ 수집 실패/누락 의심 품목 {len(missing_items)}개: {names}
        </div>""",
        unsafe_allow_html=True,
    )


# ---------- 검색/필터/정렬 ----------
search = st.text_input("품목명/기업명 검색...", label_visibility="collapsed", placeholder="품목명 또는 기업명 검색...")

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


def _related_companies_str(item_name: str) -> str:
    companies = get_related_companies(mapping_df, item_name)
    return " ".join(companies)


# ---------- 필터링 ----------
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


# ---------- 상세 화면 ----------
def render_detail(item_name: str) -> None:
    if st.button("← 목록으로"):
        st.session_state.selected_item = None
        st.rerun()

    item_hist = metrics_df[metrics_df["item_name"] == item_name].sort_values("date")
    if item_hist.empty:
        st.warning("이 품목의 데이터가 없습니다.")
        return
    latest = item_hist.iloc[-1]

    st.subheader(item_name)
    companies = get_related_companies(mapping_df, item_name)
    if companies:
        st.caption("관련 기업: " + ", ".join(companies))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("최신 수출액", _fmt_amount(latest["export_amount"]))
    yoy_txt, _ = _fmt_pct(latest["yoy"])
    c2.metric("YoY", yoy_txt)
    mom_txt, _ = _fmt_pct(latest["mom"])
    c3.metric("MoM", mom_txt)
    c4.metric("전월 수출액", _fmt_amount(latest["prev_month_amount"]))

    has_price = "unit_price" in item_hist.columns and item_hist["unit_price"].notna().any()

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(x=item_hist["date"], y=item_hist["export_amount"], name="수출금액", marker_color=SECONDARY),
        secondary_y=False,
    )
    if has_price:
        fig.add_trace(
            go.Scatter(
                x=item_hist["date"], y=item_hist["unit_price"], name="단가", mode="lines",
                line=dict(color=WARNING_ORANGE, width=2),
            ),
            secondary_y=True,
        )
    fig.update_layout(
        template="plotly_dark", height=420, margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", y=1.05),
    )
    fig.update_yaxes(title_text="수출금액 (USD)", secondary_y=False)
    if has_price:
        fig.update_yaxes(title_text="단가", secondary_y=True)
    st.plotly_chart(fig, width="stretch")

    yc, mc = st.columns(2)
    with yc:
        yoy_fig = go.Figure(
            go.Scatter(
                x=item_hist["date"], y=item_hist["yoy"], mode="lines+markers", name="YoY(%)",
                line=dict(color=POSITIVE),
            )
        )
        yoy_fig.update_layout(template="plotly_dark", height=300, title="YoY 변화율(%)", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(yoy_fig, width="stretch")
    with mc:
        mom_fig = go.Figure(
            go.Scatter(
                x=item_hist["date"], y=item_hist["mom"], mode="lines+markers", name="MoM(%)",
                line=dict(color=WARNING_ORANGE),
            )
        )
        mom_fig.update_layout(template="plotly_dark", height=300, title="MoM 변화율(%)", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(mom_fig, width="stretch")


# ---------- 카드 렌더링 ----------
def render_card(row: pd.Series) -> None:
    item_name = row["item_name"]
    companies = get_related_companies(mapping_df, item_name)
    company_txt = ", ".join(companies) if companies else "매핑된 기업 없음"

    item_hist = metrics_df[metrics_df["item_name"] == item_name].sort_values("date").tail(MINI_CHART_MONTHS)
    if item_hist.empty:
        return
    img_b64 = _make_mini_chart(tuple(item_hist["date"]), tuple(item_hist["export_amount"]))

    yoy_txt, yoy_color = _fmt_pct(row["yoy"])
    mom_txt, mom_color = _fmt_pct(row["mom"])
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
              <div style="color:{SECONDARY};font-size:11px;margin-top:4px;">전월 수출액 {_fmt_amount(row['prev_month_amount'])}</div>
              <img src="data:image/png;base64,{img_b64}" style="width:100%;margin-top:6px;" />
            </div>
            """,
            unsafe_allow_html=True,
        )
        bcol1, bcol2 = st.columns([1, 3])
        with bcol1:
            if st.button(star, key=f"fav_{item_name}", help="즐겨찾기 토글"):
                st.session_state.favorites = toggle_favorite(item_name)
                st.rerun()
        with bcol2:
            if st.button("상세보기", key=f"detail_{item_name}", width="stretch"):
                st.session_state.selected_item = item_name
                st.rerun()


# ---------- 메인 렌더링 ----------
if st.session_state.selected_item:
    render_detail(st.session_state.selected_item)
else:
    st.caption(f"{len(view)}개 품목")
    if view.empty:
        st.info("조건에 맞는 품목이 없습니다.")
    else:
        page_view = view.head(st.session_state.page_size)
        cols = st.columns(3)
        for i, (_, row) in enumerate(page_view.iterrows()):
            with cols[i % 3]:
                render_card(row)

        if st.session_state.page_size < len(view):
            if st.button(f"더 보기 ({st.session_state.page_size}/{len(view)})", width="stretch"):
                st.session_state.page_size += PAGE_SIZE_STEP
                st.rerun()
