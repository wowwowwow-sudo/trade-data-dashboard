"""
수출입 데이터 대시보드 - 누적 히스토리 버전
데이터 소스: bigfinance.co.kr (EPIC Finance) > Launch Data > TRASS-BF 수출 데이터
trade_history_long.csv (long-format, 품목명·기준일별 누적)를 읽어 카드로 렌더링.

새 데이터가 나올 때마다 append_snapshot.py로 누적하면
스파크라인이 자동으로 길어짐 (지금은 품목당 최대 3개 시점).

실행: streamlit run trade_dashboard_live.py
"""

import base64
import io
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="수출입 데이터 대시보드", layout="wide")

HISTORY_PATH = Path(__file__).parent / "trade_history_long.csv"


# ---------- 1. 히스토리 로드 ----------
@st.cache_data
def load_history() -> pd.DataFrame:
    if not HISTORY_PATH.exists():
        st.error(f"{HISTORY_PATH.name} 파일을 찾을 수 없습니다.")
        st.stop()
    df = pd.read_csv(HISTORY_PATH, parse_dates=["기준일"])
    return df.sort_values(["품목명", "기준일"])


# ---------- 2. 품목별 최신 지표 계산 (YoY/MoM은 존재하는 시점만큼만 계산) ----------
def compute_latest_metrics(item_df: pd.DataFrame) -> dict:
    item_df = item_df.sort_values("기준일")
    latest = item_df.iloc[-1]
    result = {
        "품목명": latest["품목명"],
        "대분류": latest["대분류"],
        "기준일": latest["기준일"],
        "수출금액": latest["수출금액"],
        "단가": latest["단가"],
        "yoy": None,
        "mom": None,
        "series": item_df["수출금액"].tolist(),
        "series_dates": item_df["기준일"].dt.strftime("%Y-%m").tolist(),
    }
    # MoM: 최신 시점 바로 이전 "행"이 아니라, 실제로 1~2개월 전인 시점일 때만 계산
    # (YoY 베이스 시점이 유일한 이전 기록일 경우 MoM으로 잘못 계산되는 것을 방지)
    if len(item_df) >= 2:
        prev_row = item_df.iloc[-2]
        gap_days = (latest["기준일"] - prev_row["기준일"]).days
        if 20 <= gap_days <= 45 and prev_row["수출금액"]:
            result["mom"] = (latest["수출금액"] - prev_row["수출금액"]) / prev_row["수출금액"] * 100
    # YoY: 같은 월/일, 1년 전 시점이 있으면 계산
    yoy_target = latest["기준일"] - pd.DateOffset(years=1)
    yoy_row = item_df[item_df["기준일"] == yoy_target]
    if not yoy_row.empty:
        base = yoy_row.iloc[0]["수출금액"]
        if base:
            result["yoy"] = (latest["수출금액"] - base) / base * 100
    return result


# ---------- 3. 스파크라인 (있는 만큼만, 점 1개면 단일 막대) ----------
@st.cache_data(show_spinner=False)
def make_sparkline(series: tuple[float, ...]) -> str:
    fig, ax = plt.subplots(figsize=(2.2, 0.8), dpi=130)
    colors = ["#6b7280"] + [
        "#2ecc71" if series[i] >= series[i - 1] else "#e74c3c"
        for i in range(1, len(series))
    ]
    ax.bar(range(len(series)), series, color=colors, width=0.6)
    ax.axis("off")
    fig.patch.set_alpha(0)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", transparent=True, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


# ---------- 4. 카드 렌더링 ----------
def render_card(m: dict):
    yoy_txt = f"{m['yoy']:+.2f}%" if m["yoy"] is not None else "N/A (1년치 데이터 필요)"
    mom_txt = f"{m['mom']:+.2f}%" if m["mom"] is not None else "N/A"
    yoy_color = "#8a93a3" if m["yoy"] is None else ("#2ecc71" if m["yoy"] >= 0 else "#e74c3c")
    mom_color = "#8a93a3" if m["mom"] is None else ("#3498db" if m["mom"] >= 0 else "#e74c3c")
    img_b64 = make_sparkline(tuple(m["series"]))
    n_points = len(m["series"])
    st.markdown(
        f"""
        <div style="background:#1b2430;border-radius:12px;padding:16px 20px;margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <div style="color:#fff;font-size:15px;font-weight:600;">{m['품목명']}</div>
              <div style="color:#8a93a3;font-size:12px;">{m['대분류']} · 단가 {m['단가']:,.2f} 달러/kg · 시점 {n_points}개</div>
            </div>
            <div style="text-align:right;">
              <div style="color:#fff;font-size:19px;font-weight:700;">${m['수출금액']:,.0f}</div>
              <div style="color:{yoy_color};font-size:12px;">YoY {yoy_txt}</div>
              <div style="color:{mom_color};font-size:12px;">MoM {mom_txt}</div>
            </div>
          </div>
          <img src="data:image/png;base64,{img_b64}" style="width:100%;margin-top:8px;" />
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------- 5. 필터 / 정렬 UI ----------
history = load_history()
items = [compute_latest_metrics(g) for _, g in history.groupby("품목명")]

st.title("수출입 데이터 대시보드")
latest_date = history["기준일"].max().strftime("%Y-%m-%d")
st.caption(f"epicfinance 연동 (아크임팩트자산운용 커스텀 워치리스트) | 최신 기준일: {latest_date} | {len(items)}개 품목")

categories = ["전체"] + sorted({m["대분류"] for m in items})

c1, c2, c3 = st.columns(3)
c1.metric("추적 품목", f"{len(items)}개")
yoy_known = [m for m in items if m["yoy"] is not None]
c2.metric("YoY 계산 가능", f"{len(yoy_known)}개")
c3.metric("YoY 평균", f"{(sum(m['yoy'] for m in yoy_known) / len(yoy_known)):+.1f}%" if yoy_known else "N/A")

search = st.text_input("품목명 검색...", label_visibility="collapsed")
tab = st.radio("대분류", categories, horizontal=True, label_visibility="collapsed")
sort_key = st.selectbox("정렬", ["수출액순", "YoY순", "MoM순", "이름순"], label_visibility="collapsed")

view = items
if tab != "전체":
    view = [m for m in view if m["대분류"] == tab]
if search:
    view = [m for m in view if search in m["품목명"]]

sort_fns = {
    "수출액순": lambda m: -m["수출금액"],
    "YoY순": lambda m: -(m["yoy"] if m["yoy"] is not None else -999),
    "MoM순": lambda m: -(m["mom"] if m["mom"] is not None else -999),
    "이름순": lambda m: m["품목명"],
}
view = sorted(view, key=sort_fns[sort_key])

cols = st.columns(3)
for i, m in enumerate(view):
    with cols[i % 3]:
        render_card(m)
