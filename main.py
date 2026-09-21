# -*- coding: utf-8 -*-
"""
전국 시도별 실업률 지도 (Streamlit 앱)
=====================================
- 시도(17개)별 실업률을 5단계 색으로 칠한 단계구분도입니다.
- 내 컴퓨터에서 실행:  streamlit run main.py
- 스트림릿 클라우드 배포: main.py 와 requirements.txt 를 GitHub 저장소에 올린 뒤
  스트림릿 클라우드에서 그 저장소의 main.py 를 선택하면 됩니다.

데이터 흐름 (위에서 아래로 읽으면 됩니다)
  1) 시도 경계(GeoJSON)를 인터넷에서 불러온다.
  2) 아래 SIDO_DATA 표(시도별 실업률)를 시도 '코드'로 경계와 짝짓는다.
  3) 실업률을 5단계로 나눠 색을 칠하고, 지도와 순위 표를 보여 준다.

※ 실업률 숫자는 이 파일 안(SIDO_DATA)에 직접 적어 두었습니다.
   새 달 자료가 나오면 SIDO_DATA 의 숫자와 DATA_MONTH 만 고치면 됩니다.
"""

import json

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# ─────────────────────────────────────────────────────────────
# 1. 실업률 데이터 (숫자는 여기만 고치면 됩니다)
# ─────────────────────────────────────────────────────────────

DATA_MONTH = "2026년 8월"       # 자료 기준 달
NATIONAL_RATE = 2.0             # 같은 달 전국 실업률(%)
SOURCE = "국가데이터처 지방데이터청 '2026년 8월 고용동향' 보도자료(2026.9.9)"

# 시도별 실업률 표: (이름, 행안부 시도 코드, 실업률(%), 비고)
#  - 실업률을 아직 확인하지 못한 곳은 None 으로 두었어요.
#    None 인 곳은 지도에서 회색("자료 없음")으로 보이고, 숫자로 바꾸면 자동으로 색이 칠해집니다.
#  - 강원(42→51), 전북(45→52)은 행정구역 이름이 바뀌면서 코드가 달라져서 두 코드를 모두 적었어요.
#  - 광주·전남은 2026년 7월부터 '전남광주통합특별시'로 합쳐져 통계가 한 값으로 발표됐어요.
#    그래서 두 지역에 같은 값(1.9%)을 넣었습니다.
SIDO_DATA = [
    ("서울", ["11"], 2.7, ""),
    ("부산", ["26"], 1.3, ""),
    ("대구", ["27"], 2.1, ""),
    ("인천", ["28"], 2.4, ""),
    ("광주", ["29"], 1.9, "전남광주통합특별시 통합 집계"),
    ("대전", ["30"], None, ""),
    ("울산", ["31"], 2.6, ""),
    ("세종", ["36"], None, ""),
    ("경기", ["41"], None, ""),
    ("강원", ["42", "51"], None, ""),
    ("충북", ["43"], 1.0, ""),
    ("충남", ["44"], None, ""),
    ("전북", ["45", "52"], 1.7, ""),
    ("전남", ["46"], 1.9, "전남광주통합특별시 통합 집계"),
    ("경북", ["47"], None, ""),
    ("경남", ["48"], 1.8, ""),
    ("제주", ["50"], 0.8, ""),
]

# ─────────────────────────────────────────────────────────────
# 2. 지도 설정
# ─────────────────────────────────────────────────────────────

# 시도 경계 데이터 주소 (시도 17개)
GEO_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/boundaries/sido_kr.geojson"

# 5단계를 나누는 경계값(%). 1.0 미만 / 1.0~1.5 / 1.5~2.0 / 2.0~2.5 / 2.5 이상
BREAKS = [1.0, 1.5, 2.0, 2.5]

# 범례에 보일 글자 (위 경계값을 바꾸면 이 글자도 같이 바꿔 주세요)
LEVEL_LABELS = [
    "1.0% 미만",
    "1.0% 이상 ~ 1.5% 미만",
    "1.5% 이상 ~ 2.0% 미만",
    "2.0% 이상 ~ 2.5% 미만",
    "2.5% 이상",
]

# 낮은 단계는 옅게, 높은 단계는 진하게 (5단계 푸른색 계열)
LEVEL_COLORS = ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"]

# 값이 없는 지역은 회색으로 칠합니다.
NO_DATA = "자료 없음"
COLOR_MAP = dict(zip(LEVEL_LABELS, LEVEL_COLORS))
COLOR_MAP[NO_DATA] = "#d9d9d9"

TOP_N = 5  # 순위 표에 보여 줄 개수 (시도가 17개뿐이라 5개씩 보여 줍니다)

ONE_DAY = 60 * 60 * 24  # 캐시(임시 저장) 유지 시간: 하루


# ─────────────────────────────────────────────────────────────
# 3. 지도 경계 불러오기
#    @st.cache_data 를 붙이면 한 번 불러온 결과를 기억해 두어서
#    화면을 다시 그릴 때마다 인터넷에서 새로 받지 않아도 됩니다.
# ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=ONE_DAY, show_spinner="지도 경계 데이터를 불러오는 중이에요...")
def load_boundaries():
    """GeoJSON 경계와, 지역 목록(코드·시도) 표를 돌려줍니다."""
    response = requests.get(GEO_URL, timeout=60)
    response.raise_for_status()  # 주소가 틀렸거나 서버 오류면 여기서 멈춥니다.
    geojson = json.loads(response.content.decode("utf-8-sig"))

    rows = []
    for feature in geojson["features"]:
        props = feature["properties"]
        # 코드를 2자리 글자로 통일해 둡니다. (숫자가 아니라 이름표니까 글자로!)
        props["코드"] = str(props["코드"]).strip().zfill(2)[:2]
        # 시도 이름 속성이 없어도 앱이 멈추지 않도록 여러 후보를 순서대로 찾아봅니다.
        name = props.get("시도") or props.get("시도명") or props.get("name") or props["코드"]
        rows.append({"코드": props["코드"], "시도": str(name)})

    return geojson, pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# 4. 지도용 표 만들기
# ─────────────────────────────────────────────────────────────

def short_name(full_name):
    """'서울특별시' → '서울', '충청북도' → '충북' 처럼 짧은 이름으로 바꿉니다."""
    special = {"충청북": "충북", "충청남": "충남", "전라북": "전북",
               "전라남": "전남", "경상북": "경북", "경상남": "경남"}
    for long_start, short in special.items():
        if full_name.startswith(long_start):
            return short
    return full_name[:2]


def build_map_data(regions):
    """지역 목록에 실업률을 붙이고, 5단계 구간을 계산합니다."""

    # 코드로 찾는 사전(기본)과 이름으로 찾는 사전(코드가 안 맞을 때만 쓰는 보조 수단)
    by_code, by_name = {}, {}
    for name, codes, rate, note in SIDO_DATA:
        for code in codes:
            by_code[code] = (name, rate, note)
        by_name[name] = (name, rate, note)

    rows = []
    for _, region in regions.iterrows():
        info = by_code.get(region["코드"]) or by_name.get(short_name(region["시도"]))
        if info is None:  # 어디에도 짝이 없으면 '자료 없음'으로 둡니다.
            info = (region["시도"], None, "")
        name, rate, note = info
        rows.append({"코드": region["코드"], "시도": name, "실업률": rate, "비고": note})

    data = pd.DataFrame(rows)
    data["실업률"] = pd.to_numeric(data["실업률"], errors="coerce")  # None → 빈값(NaN)

    # 5단계로 나누기: right=False 는 '1.0 이상 1.5 미만'처럼 왼쪽 값을 포함한다는 뜻
    edges = [-np.inf] + BREAKS + [np.inf]
    data["단계"] = pd.cut(data["실업률"], bins=edges, labels=LEVEL_LABELS, right=False)
    data["단계"] = data["단계"].astype(object).fillna(NO_DATA)

    # 마우스를 올렸을 때 보일 글자
    def hover_text(row):
        if pd.isna(row["실업률"]):
            return NO_DATA
        text = f"{row['실업률']:.1f}%"
        return f"{text} ({row['비고']})" if row["비고"] else text

    data["실업률_표시"] = data.apply(hover_text, axis=1)
    return data


def make_rank_table(rows):
    """순위 표에 보여 줄 열만 골라 이름을 예쁘게 바꿉니다."""
    table = rows[["시도", "실업률", "비고"]].copy()
    table["실업률"] = table["실업률"].round(1)
    table.columns = ["시도", "실업률(%)", "비고"]
    table.insert(0, "순위", range(1, len(table) + 1))
    return table


# ─────────────────────────────────────────────────────────────
# 5. 화면 그리기
# ─────────────────────────────────────────────────────────────

# 페이지 설정은 스트림릿 명령 중 가장 먼저 와야 합니다.
st.set_page_config(page_title="전국 실업률 지도", page_icon="🗺️", layout="wide")
st.title("🗺️ 전국 시도별 실업률 지도")

# 지도 경계 불러오기 (실패하면 안내 문구를 보여 주고 멈춥니다)
try:
    geojson, regions = load_boundaries()
except (requests.RequestException, ValueError, KeyError) as error:
    st.error(f"지도 데이터를 불러오지 못했어요. 잠시 뒤에 다시 시도해 주세요. (원인: {error})")
    st.stop()

data = build_map_data(regions)

st.caption(
    f"{DATA_MONTH} 기준 · 실업률(%) = 실업자 ÷ 경제활동인구 × 100 · "
    f"같은 달 전국 실업률 {NATIONAL_RATE:.1f}%"
)

# ── 5-1. 단계구분도 ──────────────────────────────────────────
fig = px.choropleth(
    data,
    geojson=geojson,                   # 지역 경계
    locations="코드",                  # 표에서 지역을 가리키는 열
    featureidkey="properties.코드",    # GeoJSON 안에서 같은 값을 가진 속성 → 코드로 짝을 맞춥니다.
    color="단계",                      # 단계(글자)별로 색을 나눕니다.
    color_discrete_map=COLOR_MAP,      # 단계별 색
    category_orders={"단계": LEVEL_LABELS + [NO_DATA]},  # 범례 순서: 낮은 단계 → 높은 단계
    custom_data=["시도", "실업률_표시"],  # 마우스 올릴 때 쓸 값
)

fig.update_traces(
    # 마우스를 올리면: 시도 이름 / 실업률
    hovertemplate=(
        "<b>%{customdata[0]}</b><br>"
        "실업률: %{customdata[1]}"
        "<extra></extra>"
    ),
    marker_line_color="#777777",  # 경계선 색
    marker_line_width=0.6,        # 경계선 두께
)

# visible=False → 바다·땅·배경 지도를 모두 숨기고 경계선만 보여 줍니다.
# fitbounds="locations" → 우리나라 경계에 딱 맞게 확대합니다.
fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator")
fig.update_layout(
    legend_title_text="실업률",
    margin=dict(l=0, r=0, t=0, b=0),
    height=720,
)
st.plotly_chart(fig)

# ── 5-2. 데이터 안내 (값이 비어 있는 지역이 있을 때만 보입니다) ──
missing = data[data["실업률"].isna()]
if not missing.empty:
    names = ", ".join(missing["시도"])
    st.warning(
        f"실업률 값이 없는 시도가 {len(missing)}곳 있어 회색으로 표시했어요: {names}. "
        "main.py 위쪽 SIDO_DATA 에서 None 을 숫자로 바꾸면 색이 칠해집니다."
    )

# ── 5-3. 실업률 높은 곳 / 낮은 곳 ────────────────────────────
valid = data.dropna(subset=["실업률"])  # 실업률이 있는 시도만

left, right = st.columns(2)  # 화면을 좌우 두 칸으로 나눕니다.

with left:
    st.subheader(f"실업률 높은 곳 {TOP_N}곳")
    st.dataframe(make_rank_table(valid.nlargest(TOP_N, "실업률")), hide_index=True)

with right:
    st.subheader(f"실업률 낮은 곳 {TOP_N}곳")
    st.dataframe(make_rank_table(valid.nsmallest(TOP_N, "실업률")), hide_index=True)

st.caption(
    f"출처: {SOURCE}. 경제활동인구조사는 표본조사라 시도별 수치에는 오차가 있어, "
    "작은 차이는 의미가 크지 않을 수 있어요."
)
