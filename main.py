
# -*- coding: utf-8 -*-
"""
전국 시군구 고령화 지도 (Streamlit 앱)
=====================================
- 시군구별 65세 이상 인구 비율(고령화율)을 5단계 색으로 칠한 단계구분도입니다.
- 내 컴퓨터에서 실행:  streamlit run main.py
- 스트림릿 클라우드 배포: main.py 와 requirements.txt 를 GitHub 저장소에 올린 뒤
  스트림릿 클라우드에서 그 저장소의 main.py 를 선택하면 됩니다.

데이터 흐름 (위에서 아래로 읽으면 됩니다)
  1) 인구 CSV(읍·면·동 단위)를 불러와 가장 최신 연도만 남긴다.
  2) '코드' 앞 5자리를 잘라 시군구 단위로 인구를 합친다.
  3) 지도 경계(GeoJSON)의 시군구 '코드'와 맞춰 고령화율을 계산한다.
  4) 고령화율을 5단계로 나눠 색을 칠하고, 지도와 표를 보여 준다.
"""

import io    # 내려받은 파일 내용을 '파일처럼' 읽기 위한 도구
import json  # GeoJSON 글자를 파이썬 딕셔너리로 바꾸는 도구
import re    # 열 이름에서 나이 숫자를 뽑아내는 도구(정규식)

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# ─────────────────────────────────────────────────────────────
# 1. 설정값 (바꾸고 싶을 때는 여기만 고치면 됩니다)
# ─────────────────────────────────────────────────────────────

# 인구 데이터와 지도 경계 데이터의 주소
POP_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/population_yearly.csv.gz"
GEO_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/boundaries/sigungu_kr.geojson"

# 5단계를 나누는 경계값(%). 19 미만 / 19~23 / 23~28 / 28~38 / 38 이상
BREAKS = [19, 23, 28, 38]

# 범례에 보일 글자 (위 경계값을 바꾸면 이 글자도 같이 바꿔 주세요)
LEVEL_LABELS = [
    "19% 미만",
    "19% 이상 ~ 23% 미만",
    "23% 이상 ~ 28% 미만",
    "28% 이상 ~ 38% 미만",
    "38% 이상",
]

# 낮은 단계는 옅게, 높은 단계는 진하게 (5단계 붉은색 계열)
LEVEL_COLORS = ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"]

# 인구 데이터와 짝이 맞지 않는 지역은 회색으로 칠합니다.
NO_DATA = "자료 없음"
COLOR_MAP = dict(zip(LEVEL_LABELS, LEVEL_COLORS))
COLOR_MAP[NO_DATA] = "#d9d9d9"

# '계_0세', '계_65세', '계_100세 이상' 같은 열 이름을 찾는 규칙
# ('계_' 는 남녀를 합친 값입니다. '남_', '여_' 열은 쓰지 않아요.)
AGE_PATTERN = re.compile(r"^계_(\d+)세(\s*이상)?$")

ONE_DAY = 60 * 60 * 24  # 캐시(임시 저장) 유지 시간: 하루


# ─────────────────────────────────────────────────────────────
# 2. 데이터 불러오기
#    @st.cache_data 를 붙이면 한 번 불러온 결과를 기억해 두어서
#    화면을 다시 그릴 때마다 인터넷에서 새로 받지 않아도 됩니다.
# ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=ONE_DAY, show_spinner="인구 데이터를 불러오는 중이에요...")
def load_population_summary():
    """최신 연도의 시군구별 (전체 인구, 65세 이상 인구)를 계산해서 돌려줍니다."""

    # (1) 파일 내려받기
    response = requests.get(POP_URL, timeout=60)
    response.raise_for_status()  # 주소가 틀렸거나 서버 오류면 여기서 멈춥니다.
    raw = response.content

    # gzip 파일은 맨 앞 두 바이트가 정해져 있어요. 압축 파일인지 확인합니다.
    compression = "gzip" if raw[:2] == b"\x1f\x8b" else None

    # (2) CSV 읽기
    #   - dtype={"코드": str} : '코드'를 숫자가 아니라 글자로 읽습니다. (중요!)
    #   - usecols : 필요한 열(연도, 코드, '계_'로 시작하는 열)만 읽어서 메모리를 아낍니다.
    df = None
    for encoding in ("utf-8-sig", "cp949"):  # 글자 인코딩이 다를 수 있어 두 가지를 시도
        try:
            df = pd.read_csv(
                io.BytesIO(raw),
                compression=compression,
                encoding=encoding,
                dtype={"코드": str},
                usecols=lambda col: col in ("연도", "코드") or col.startswith("계_"),
            )
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        raise ValueError("CSV 파일의 글자 인코딩을 읽지 못했어요.")

    if "연도" not in df.columns or "코드" not in df.columns:
        raise ValueError("CSV에 '연도' 또는 '코드' 열이 없어요.")

    # (3) 나이별 열 찾기: {열 이름: 나이} 형태로 모읍니다.
    age_of = {}
    for col in df.columns:
        match = AGE_PATTERN.match(col)
        if match:
            age_of[col] = int(match.group(1))  # 예: '계_65세' → 65
    if not age_of:
        raise ValueError("'계_0세' 같은 나이별 열을 찾지 못했어요.")

    all_cols = list(age_of)                                  # 0세 ~ 100세 이상 전부
    old_cols = [col for col, age in age_of.items() if age >= 65]  # 65세 이상만

    # (4) 가장 최신 연도만 남기기
    df["연도"] = pd.to_numeric(df["연도"], errors="coerce")
    latest_year = int(df["연도"].max())
    df = df[df["연도"] == latest_year].copy()

    # (5) 코드가 비어 있는 줄은 버립니다.
    df = df.dropna(subset=["코드"])

    # (6) 인구 열을 숫자로 바꾸기 (혹시 '1,234' 처럼 쉼표가 있어도 처리)
    def to_number(column):
        if not pd.api.types.is_numeric_dtype(column):  # 글자로 읽힌 열만 쉼표를 없앱니다.
            column = column.astype(str).str.replace(",", "", regex=False)
        return pd.to_numeric(column, errors="coerce")

    numbers = df[all_cols].apply(to_number).fillna(0)  # 빈칸은 0명으로 처리

    # (7) 읍·면·동별 인구를 '시군구 코드(= 코드 앞 5자리)' 단위로 합치기
    slim = pd.DataFrame(
        {
            "시군구코드": df["코드"].str.strip().str[:5],
            "전체": numbers.sum(axis=1),             # 0세 ~ 100세 이상 모두 더함
            "65세이상": numbers[old_cols].sum(axis=1),  # 65세 이상만 더함
        }
    )
    summary = slim.groupby("시군구코드", as_index=False)[["전체", "65세이상"]].sum()

    return summary, latest_year


@st.cache_data(ttl=ONE_DAY, show_spinner="지도 경계 데이터를 불러오는 중이에요...")
def load_boundaries():
    """GeoJSON 경계와, 지역 목록(코드·시군구·시도) 표를 돌려줍니다."""
    response = requests.get(GEO_URL, timeout=60)
    response.raise_for_status()
    geojson = json.loads(response.content.decode("utf-8-sig"))

    rows = []
    for feature in geojson["features"]:
        props = feature["properties"]
        # 코드를 5자리 글자로 통일해 둡니다. (인구 데이터의 코드와 같은 모양으로 맞추기 위해)
        props["코드"] = str(props["코드"]).strip().zfill(5)
        rows.append({"코드": props["코드"], "시군구": props["시군구"], "시도": props["시도"]})

    return geojson, pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# 3. 지도용 표 만들기
# ─────────────────────────────────────────────────────────────

def build_map_data(regions, summary):
    """지역 목록에 인구를 붙이고, 고령화율과 5단계 구간을 계산합니다."""

    # 이름이 아니라 '코드'로 붙입니다. ('남구'처럼 이름이 같은 지역이 있어도 안전!)
    data = regions.merge(summary, how="left", left_on="코드", right_on="시군구코드")

    # 고령화율(%) = 65세 이상 인구 ÷ 전체 인구 × 100
    data["고령화율"] = (data["65세이상"] / data["전체"] * 100).where(data["전체"] > 0)

    # 5단계로 나누기: right=False 는 '19 이상 23 미만'처럼 왼쪽 값을 포함한다는 뜻
    edges = [-np.inf] + BREAKS + [np.inf]
    data["단계"] = pd.cut(data["고령화율"], bins=edges, labels=LEVEL_LABELS, right=False)
    data["단계"] = data["단계"].astype(object).fillna(NO_DATA)

    # 마우스를 올렸을 때 보일 글자
    data["고령화율_표시"] = data["고령화율"].map(
        lambda value: NO_DATA if pd.isna(value) else f"{value:.1f}%"
    )
    return data


def make_rank_table(rows):
    """순위 표에 보여 줄 열만 골라 이름을 예쁘게 바꿉니다."""
    table = rows[["시도", "시군구", "65세이상", "전체", "고령화율"]].copy()
    table["65세이상"] = table["65세이상"].round().astype(int)
    table["전체"] = table["전체"].round().astype(int)
    table["고령화율"] = table["고령화율"].round(1)
    table.columns = ["시도", "시군구", "65세 이상 인구(명)", "전체 인구(명)", "고령화율(%)"]
    table.insert(0, "순위", range(1, len(table) + 1))
    return table


# ─────────────────────────────────────────────────────────────
# 4. 화면 그리기
# ─────────────────────────────────────────────────────────────

# 페이지 설정은 스트림릿 명령 중 가장 먼저 와야 합니다.
st.set_page_config(page_title="전국 고령화 지도", page_icon="🗺️", layout="wide")
st.title("🗺️ 전국 시군구 고령화 지도")

# 데이터 불러오기 (실패하면 안내 문구를 보여 주고 멈춥니다)
try:
    summary, year = load_population_summary()
    geojson, regions = load_boundaries()
except (requests.RequestException, ValueError, KeyError) as error:
    st.error(f"데이터를 불러오지 못했어요. 잠시 뒤에 다시 시도해 주세요. (원인: {error})")
    st.stop()

data = build_map_data(regions, summary)

st.caption(f"{year}년 기준 · 고령화율 = 65세 이상 인구 ÷ 전체 인구 × 100 (시군구 단위)")

# ── 4-1. 단계구분도 ──────────────────────────────────────────
fig = px.choropleth(
    data,
    geojson=geojson,                   # 지역 경계
    locations="코드",                  # 표에서 지역을 가리키는 열
    featureidkey="properties.코드",    # GeoJSON 안에서 같은 값을 가진 속성 → 코드로 짝을 맞춥니다.
    color="단계",                      # 단계(글자)별로 색을 나눕니다.
    color_discrete_map=COLOR_MAP,      # 단계별 색
    category_orders={"단계": LEVEL_LABELS + [NO_DATA]},  # 범례 순서: 낮은 단계 → 높은 단계
    custom_data=["시군구", "시도", "고령화율_표시"],       # 마우스 올릴 때 쓸 값
)

fig.update_traces(
    # 마우스를 올리면: 시군구 이름 / 시도 / 고령화율
    hovertemplate=(
        "<b>%{customdata[0]}</b><br>"
        "시도: %{customdata[1]}<br>"
        "고령화율: %{customdata[2]}"
        "<extra></extra>"
    ),
    marker_line_color="#777777",  # 경계선 색
    marker_line_width=0.4,        # 경계선 두께
)

# visible=False → 바다·땅·배경 지도를 모두 숨기고 경계선만 보여 줍니다.
# fitbounds="locations" → 우리나라 경계에 딱 맞게 확대합니다.
fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator")
fig.update_layout(
    legend_title_text="65세 이상 인구 비율",
    margin=dict(l=0, r=0, t=0, b=0),
    height=720,
)
st.plotly_chart(fig)

# ── 4-2. 데이터 점검 안내 (짝이 안 맞는 지역이 있을 때만 보입니다) ──
missing = data[data["고령화율"].isna()]
if not missing.empty:
    names = ", ".join((missing["시도"] + " " + missing["시군구"]).head(20))
    st.warning(
        f"인구 데이터와 짝이 맞지 않는 지역이 {len(missing)}곳 있어 회색으로 표시했어요: {names}"
    )
unused_codes = sorted(set(summary["시군구코드"]) - set(regions["코드"]))
if unused_codes:
    st.caption(f"지도에 대응하는 경계가 없어 쓰이지 않은 인구 데이터 코드: {', '.join(unused_codes[:20])}")

# ── 4-3. 고령화율 높은 곳 / 낮은 곳 10개 ────────────────────
valid = data.dropna(subset=["고령화율"])  # 고령화율을 계산할 수 있는 지역만

left, right = st.columns(2)  # 화면을 좌우 두 칸으로 나눕니다.

with left:
    st.subheader("고령화율 높은 곳 10곳")
    st.dataframe(make_rank_table(valid.nlargest(10, "고령화율")), hide_index=True)

with right:
    st.subheader("고령화율 낮은 곳 10곳")
    st.dataframe(make_rank_table(valid.nsmallest(10, "고령화율")), hide_index=True)
