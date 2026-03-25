import pandas as pd
import geopandas as gpd
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

FLOOD_RISK_PATH = BASE_DIR / "gold" / "flood_risk"
GEO_PATH = BASE_DIR / "gold" / "flood_risk_geo"
STATIC_GRID_PATH = BASE_DIR / "bronze" / "static" / "denpasar_grid_static.geojson"

ROLLING_DAYS = 3

def extract_date_from_path(f):
    year = int(f.parts[-3].split("=")[1])
    month = int(f.parts[-2].split("=")[1])
    day = int(f.name.replace("day=", "").replace(".parquet", ""))
    return datetime(year, month, day).date()

def get_partition_dates(base_path):
    if not base_path.exists():
        return []
    files = list(base_path.rglob("day=*.parquet"))
    return sorted({extract_date_from_path(f) for f in files})

def determine_rebuild_dates():
    risk_dates = get_partition_dates(FLOOD_RISK_PATH)
    geo_dates = get_partition_dates(GEO_PATH / "hourly")

    if not risk_dates:
        return []

    if not geo_dates:
        return risk_dates

    latest_risk = max(risk_dates)

    rolling = {
        latest_risk - timedelta(days=i)
        for i in range(ROLLING_DAYS)
    }

    gap = {d for d in risk_dates if d not in geo_dates}

    return sorted(gap | rolling)

def load_risk_partition(date):
    path = (
        FLOOD_RISK_PATH
        / f"year={date.year:04d}"
        / f"month={date.month:02d}"
        / f"day={date.day:02d}.parquet"
    )
    return pd.read_parquet(path)

def write_partition(base_path, df, date):
    out_dir = (
        base_path
        / f"year={date.year:04d}"
        / f"month={date.month:02d}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / f"day={date.day:02d}.parquet"
    df.to_parquet(out_file, index=False)

    print("Saved →", out_file)

static_gdf = gpd.read_file(STATIC_GRID_PATH)[["grid_id", "geometry"]]
static_gdf["grid_id"] = static_gdf["grid_id"].astype(int)
static_gdf["geometry_wkt"] = static_gdf["geometry"].to_wkt()
static_gdf = static_gdf.drop(columns=["geometry"])

def build_geo_for_date(date):

    print("Building GEO for", date)

    df = load_risk_partition(date)
    df["grid_id"] = df["grid_id"].astype(int)

    required_cols = [
        "grid_id",
        "timestamp_utc",
        "risk_score",
        "risk_category",
        "rainfall_24h_sum",
        "rainfall_6h_sum",
        "mean_elevation",
        "percent_lowland",
        "urban_ratio"
    ]

    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"{col} missing from flood risk partition")

    hourly = df.merge(static_gdf, on="grid_id", how="left")

    if hourly["geometry_wkt"].isna().any():
        raise ValueError("Geometry merge failed for some grid_id")

    write_partition(GEO_PATH / "hourly", hourly, date)

def main():

    rebuild_dates = determine_rebuild_dates()

    if not rebuild_dates:
        print("No geo dates to rebuild.")
        return

    for date in rebuild_dates:
        build_geo_for_date(date)

    print("Gold GEO layer build complete.")

if __name__ == "__main__":
    main()