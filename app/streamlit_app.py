import streamlit as st
import pandas as pd
import geopandas as gpd
import shapely.wkt
import folium
from streamlit_folium import st_folium
from pathlib import Path
import plotly.express as px
from datetime import date

st.set_page_config(layout="wide")

BASE_DIR = Path(__file__).resolve().parent.parent
GEO_PATH = BASE_DIR / "gold" / "flood_risk_geo"
BRONZE_PATH = BASE_DIR / "bronze" / "imerg"


def get_latest_dataset_timestamp():

    files = sorted(BRONZE_PATH.rglob("*.parquet"))

    if not files:
        return None

    files = files[-3:]

    latest_ts = None

    for f in files:

        df = pd.read_parquet(f, columns=["timestamp_utc", "rainfall_mm"])

        if (df["rainfall_mm"] > 0).any():

            ts = pd.to_datetime(df["timestamp_utc"]).max()

            if latest_ts is None or ts > latest_ts:
                latest_ts = ts

    return latest_ts


latest_dataset_timestamp = get_latest_dataset_timestamp()

st.title("Denpasar Grid-Based Flood Risk Modeling System")

selected_date = st.date_input(
    "Select Date",
    value=date(2026, 2, 23)
)

year = selected_date.strftime("%Y")
month = selected_date.strftime("%m")
day = selected_date.strftime("%d")

path = GEO_PATH / "hourly" / f"year={year}" / f"month={month}" / f"day={day}.parquet"

if not path.exists():
    st.warning("No data available.")
    st.stop()

df = pd.read_parquet(path)

df["geometry"] = df["geometry_wkt"].apply(shapely.wkt.loads)
gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")

timestamps = sorted(gdf["timestamp_utc"].unique())
selected_time = st.select_slider("Select Time", options=timestamps)

gdf = gdf[gdf["timestamp_utc"] == selected_time]
gdf["timestamp_utc"] = gdf["timestamp_utc"].astype(str)

avg_risk = round(gdf["risk_score"].mean(), 1)
max_risk = round(gdf["risk_score"].max(), 1)

avg_rain_1h = round(gdf["rainfall_1h"].mean(), 2)
avg_rain_6h = round(gdf["rainfall_6h_sum"].mean(), 2)
avg_rain_24h = round(gdf["rainfall_24h_sum"].mean(), 2)
avg_elevation = round(gdf["mean_elevation"].mean(), 2)
avg_lowland = round(gdf["percent_lowland"].mean(), 2)
avg_urban = round(gdf["urban_ratio"].mean(), 2)

k1, k2 = st.columns(2)

k1.markdown(
f"""
<div style="text-align:center">
<div style="font-size:18px;color:#aaaaaa">Average Risk</div>
<div style="font-size:48px;font-weight:700;color:#ff6666">{avg_risk}</div>
</div>
""",
unsafe_allow_html=True
)

k2.markdown(
f"""
<div style="text-align:center">
<div style="font-size:18px;color:#aaaaaa">Max Risk</div>
<div style="font-size:48px;font-weight:700;color:#ff3333">{max_risk}</div>
</div>
""",
unsafe_allow_html=True
)

st.markdown("### Flood Risk Map")

map_col, info_col = st.columns([4,2])

with map_col:

    m = folium.Map(location=[-8.67,115.21], zoom_start=13, tiles=None)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri Satellite",
        overlay=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}.png",
        attr="CartoDB",
        overlay=True
    ).add_to(m)

    def style_function(feature):

        score = feature["properties"]["risk_score"]

        if score < 25:
            color = "#ffcccc"
            opacity = 0.25
        elif score < 50:
            color = "#ff9999"
            opacity = 0.35
        elif score < 75:
            color = "#ff4d4d"
            opacity = 0.5
        else:
            color = "#cc0000"
            opacity = 0.65

        return {
            "fillColor": color,
            "color": "#660000",
            "weight": 0.25,
            "fillOpacity": opacity
        }

    folium.GeoJson(
        gdf,
        style_function=style_function,
        tooltip=folium.GeoJsonTooltip(
            fields=[
                "grid_id",
                "risk_score",
                "rainfall_24h_sum",
                "rainfall_6h_sum",
                "rainfall_1h",
                "mean_elevation",
                "urban_ratio"
            ],
            aliases=[
                "Grid ID:",
                "Risk Score:",
                "Rain 24h (mm):",
                "Rain 6h (mm):",
                "Rain 1h (mm):",
                "Elevation (m):",
                "Urban Ratio:"
            ],
            localize=True
        )
    ).add_to(m)

    st_folium(m, width=1200, height=700)

with info_col:

    st.markdown(
f"""
<div style="font-size:13px; line-height:1.35">

<div style="font-size:19px;font-weight:700;margin-bottom:6px;">How to Read This Map</div>

Rainfall observations originate from the <b>NASA GPM IMERG satellite dataset</b> and are processed through a grid-based flood risk model.

The most recent rainfall dataset currently available in the system was recorded at:

<b>{latest_dataset_timestamp}</b>

This timestamp represents the latest hourly record from the most recent dataset that contains rainfall observations.

<br>

<b>Current Data Snapshot</b><br>

1h rainfall: <b>{avg_rain_1h} mm</b><br>
6h accumulated rainfall: <b>{avg_rain_6h} mm</b><br>
24h accumulated rainfall: <b>{avg_rain_24h} mm</b>

<br>

Mean elevation: <b>{avg_elevation} m</b><br>
Percent lowland: <b>{avg_lowland}%</b><br>
Urban surface ratio: <b>{avg_urban}</b>

<br>

These variables interact to produce a <b>risk_score</b> for every grid cell.

<hr>

<b>Average Risk</b><br>
<b>{avg_risk}</b> represents the mean flood susceptibility across all grid cells in Denpasar at the selected timestamp.

<hr>

<b>Max Risk</b><br>
<b>{max_risk}</b> indicates the highest flood susceptibility observed in a single grid cell.

</div>
""",
unsafe_allow_html=True
)

st.markdown("### Rainfall (24h Accumulation)")

full_day_df = pd.read_parquet(path)
full_day_df["timestamp_utc"] = pd.to_datetime(full_day_df["timestamp_utc"])

rainfall_series = (
    full_day_df.groupby("timestamp_utc")["rainfall_24h_sum"]
    .mean()
    .reset_index()
)

fig = px.line(
    rainfall_series,
    x="timestamp_utc",
    y="rainfall_24h_sum",
    template="plotly_dark"
)

fig.update_traces(line=dict(color="red", width=3))

fig.update_layout(
    xaxis_title="Time",
    yaxis_title="Rainfall 24h (mm)",
    height=400
)

st.plotly_chart(fig, use_container_width=True)

st.markdown(
    "<div style='position: fixed; bottom: 10px; right: 15px; font-size: 10px; color: rgba(255,255,255,0.25);'>Denpasar Grid-Based Flood Risk Modeling System | a portfolio by tianyus</div>",
    unsafe_allow_html=True
)