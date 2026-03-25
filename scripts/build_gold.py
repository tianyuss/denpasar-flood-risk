import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
SILVER_PATH = BASE_DIR / "silver" / "rainfall"
GOLD_PATH = BASE_DIR / "gold" / "flood_risk"

ROLLING_DAYS = 5

PHYSICAL_BASELINES = {
    "rainfall_6h_sum": 80.0,
    "rainfall_24h_sum": 120.0,
    "mean_elevation": 50.0
}

def normalize(series, baseline):
    return np.clip(series / baseline, 0, 1)

def nonlinear(series):
    return np.power(series, 1.5)

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
    silver_dates = get_partition_dates(SILVER_PATH)
    gold_dates = get_partition_dates(GOLD_PATH)

    if not silver_dates:
        return []

    if not gold_dates:
        return silver_dates

    latest_silver = max(silver_dates)

    rolling = {
        latest_silver - timedelta(days=i)
        for i in range(ROLLING_DAYS)
    }

    gap = {d for d in silver_dates if d not in gold_dates}

    return sorted(gap | rolling)

def load_silver_partition(date):
    path = (
        SILVER_PATH
        / f"year={date.year:04d}"
        / f"month={date.month:02d}"
        / f"day={date.day:02d}.parquet"
    )
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["date"] = date
    return df

def write_partition(df, date):
    out_dir = (
        GOLD_PATH
        / f"year={date.year:04d}"
        / f"month={date.month:02d}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / f"day={date.day:02d}.parquet"
    df.to_parquet(out_file, index=False)

    print("Saved →", out_file)

def compute_risk(df):

    df = df.sort_values(["grid_id", "timestamp_utc"])

    df["rain_t1"] = df.groupby("grid_id")["rainfall_24h_sum"].shift(24)
    df["rain_t2"] = df.groupby("grid_id")["rainfall_24h_sum"].shift(48)

    df[["rain_t1","rain_t2"]] = df[["rain_t1","rain_t2"]].fillna(0)

    df["antecedent_rainfall"] = (
        0.6 * df["rainfall_24h_sum"] +
        0.3 * df["rain_t1"] +
        0.1 * df["rain_t2"]
    )

    R24 = nonlinear(normalize(df["antecedent_rainfall"], PHYSICAL_BASELINES["rainfall_24h_sum"]))
    R6 = nonlinear(normalize(df["rainfall_6h_sum"], PHYSICAL_BASELINES["rainfall_6h_sum"]))

    rainfall_trigger = 0.7 * R24 + 0.3 * R6

    elevation_factor = 1 - normalize(
        df["mean_elevation"],
        PHYSICAL_BASELINES["mean_elevation"]
    )

    susceptibility = (
        0.5 * df["percent_lowland"] +
        0.3 * df["urban_ratio"] +
        0.2 * elevation_factor
    )

    risk_raw = rainfall_trigger * (0.6 + 0.4 * susceptibility)

    df["risk_score"] = np.clip(risk_raw * 100, 0, 100)

    df["risk_category"] = pd.cut(
        df["risk_score"],
        bins=[0,25,50,75,100],
        labels=["LOW","MODERATE","HIGH","EXTREME"]
    )

    return df

def build_gold():

    rebuild_dates = determine_rebuild_dates()

    if not rebuild_dates:
        print("No dates to rebuild.")
        return

    start = min(rebuild_dates) - timedelta(days=2)
    end = max(rebuild_dates)

    dates_to_load = [
        start + timedelta(days=i)
        for i in range((end-start).days+1)
    ]

    dfs = []

    for d in dates_to_load:
        df = load_silver_partition(d)
        if df is not None:
            dfs.append(df)

    df_all = pd.concat(dfs, ignore_index=True)

    df_all = compute_risk(df_all)

    for d in rebuild_dates:
        df_out = df_all[df_all["date"] == d].copy()
        df_out = df_out.drop(columns=["date","rain_t1","rain_t2"])
        write_partition(df_out, d)

    print("Gold layer build complete.")

if __name__ == "__main__":
    build_gold()