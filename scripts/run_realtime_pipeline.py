import io
import re
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

import ee
import geopandas as gpd
import pandas as pd
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseDownload


PROJECT_ID = "useful-patrol-477308-d5"
DRIVE_FOLDER = "Denpasar Rainfall"
FOLDER_ID = "1NpS0HCHcSXdr-VgefrjMqiYHHj1Sj82i"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

BASE_DIR = Path(__file__).resolve().parent.parent

BRONZE_PATH = BASE_DIR / "bronze" / "imerg"
GRID_PATH = BASE_DIR / "bronze" / "static" / "denpasar_grid_static.geojson"
STATE_FILE = BASE_DIR / "models" / "pipeline_state.json"
SERVICE_ACCOUNT_FILE = BASE_DIR / "secrets" / "service_account.json"

CHECK_INTERVAL = 300


ee.Initialize(project=PROJECT_ID)

creds = service_account.Credentials.from_service_account_file(
    str(SERVICE_ACCOUNT_FILE),
    scopes=SCOPES
)

drive_service = build("drive", "v3", credentials=creds)


gdf = gpd.read_file(GRID_PATH)

features = []

for _, row in gdf.iterrows():

    geom = ee.Geometry.MultiPolygon(
        row.geometry.__geo_interface__["coordinates"]
    )

    features.append(
        ee.Feature(
            geom,
            {
                "grid_id": int(row["grid_id"]),
                "mean_elevation": float(row["mean_elevation"]),
                "percent_lowland": float(row["percent_lowland"]),
                "urban_ratio": float(row["urban_ratio"])
            }
        )
    )

grid_fc = ee.FeatureCollection(features)

imerg = ee.ImageCollection("NASA/GPM_L3/IMERG_V07").select("precipitation")


def load_state():

    if not STATE_FILE.exists():
        return {"last_processed_timestamp": None}

    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save_state(state):

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def extract_date_from_path(f):

    year = int(f.parts[-3].split("=")[1])
    month = int(f.parts[-2].split("=")[1])
    day = int(f.name.replace("day=", "").replace(".parquet", ""))

    return datetime(year, month, day).date()


def get_latest_bronze_date():

    files = list(BRONZE_PATH.rglob("day=*.parquet"))

    if not files:
        return None

    dates = [extract_date_from_path(f) for f in files]

    return max(dates)


def get_target_dates():

    today = datetime.now(timezone.utc).date()

    latest_bronze = get_latest_bronze_date()

    if latest_bronze is None:
        return [today - timedelta(days=i) for i in range(7)]

    gap_dates = []

    current = latest_bronze + timedelta(days=1)

    while current <= today:
        gap_dates.append(current)
        current += timedelta(days=1)

    rolling_dates = [
        today,
        today - timedelta(days=1),
        today - timedelta(days=2)
    ]

    return sorted(set(gap_dates + rolling_dates))


def export_if_needed(target_date):

    label = target_date.strftime("%Y_%m_%d")

    query = f"name='denpasar_rainfall_{label}.csv' and '{FOLDER_ID}' in parents"

    files = drive_service.files().list(
        q=query,
        fields="files(id,name)"
    ).execute()["files"]

    if files:
        return

    start = ee.Date(target_date.strftime("%Y-%m-%d"))
    end = ee.Date((target_date + timedelta(days=1)).strftime("%Y-%m-%d"))

    total_hours = end.difference(start, "hour")

    hours = ee.List.sequence(0, total_hours.subtract(1))

    def hourly_reduce(hour_offset):

        hour_start = start.advance(hour_offset, "hour")
        hour_end = hour_start.advance(1, "hour")

        hourly_coll = imerg.filterDate(hour_start, hour_end)

        img = hourly_coll.sum()

        reduced = img.reduceRegions(
            collection=grid_fc,
            reducer=ee.Reducer.mean(),
            scale=10000
        )

        return reduced.map(
            lambda f: f.set({
                "timestamp_utc": hour_start.format("YYYY-MM-dd HH:mm:ss"),
                "rainfall_mm": f.get("mean")
            })
        )

    daily_collection = ee.FeatureCollection(
        hours.map(hourly_reduce)
    ).flatten()

    task = ee.batch.Export.table.toDrive(
        collection=daily_collection,
        description=f"denpasar_rainfall_{label}",
        folder=DRIVE_FOLDER,
        fileNamePrefix=f"denpasar_rainfall_{label}",
        fileFormat="CSV"
    )

    task.start()


def ingest_drive_csv():

    today = datetime.now(timezone.utc).date()

    rolling_window = {
        today,
        today - timedelta(days=1),
        today - timedelta(days=2)
    }

    query = f"'{FOLDER_ID}' in parents and mimeType='text/csv'"

    drive_files = drive_service.files().list(
        q=query,
        fields="files(id,name)",
        pageSize=1000
    ).execute()["files"]

    new_data_ingested = False
    latest_timestamp = None

    for file in drive_files:

        filename = file["name"]
        file_id = file["id"]

        match = re.search(r"(\d{4})_(\d{2})_(\d{2})", filename)

        if not match:
            continue

        year, month, day = match.groups()

        file_date = datetime(int(year), int(month), int(day)).date()

        partition_path = BRONZE_PATH / f"year={year}" / f"month={month}"
        parquet_path = partition_path / f"day={day}.parquet"

        parquet_exists = parquet_path.exists()

        if not parquet_exists:
            action = "INGEST_NEW"
        elif file_date in rolling_window:
            action = "OVERWRITE_ROLLING"
        else:
            action = "SKIP"

        if action == "SKIP":
            continue

        request = drive_service.files().get_media(fileId=file_id)

        fh = io.BytesIO()

        downloader = MediaIoBaseDownload(fh, request)

        done = False

        while not done:
            _, done = downloader.next_chunk()

        fh.seek(0)

        df = pd.read_csv(fh)

        if "timestamp_utc" in df.columns:
            df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])

        partition_path.mkdir(parents=True, exist_ok=True)

        df.to_parquet(parquet_path, index=False)

        latest_timestamp = df["timestamp_utc"].max()

        new_data_ingested = True

    return new_data_ingested, latest_timestamp


def run_cycle():

    state = load_state()

    targets = get_target_dates()

    for d in targets:
        export_if_needed(d)

    time.sleep(20)

    new_data, latest_timestamp = ingest_drive_csv()

    if not new_data:
        return

    subprocess.run(["python", str(BASE_DIR / "scripts" / "rainfall_features.py")])

    subprocess.run(["python", str(BASE_DIR / "scripts" / "build_gold.py")])

    subprocess.run(["python", str(BASE_DIR / "scripts" / "build_gold_geo.py")])

    if latest_timestamp is not None:
        state["last_processed_timestamp"] = latest_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(state)


def scheduler():

    while True:

        try:
            run_cycle()
        except Exception as e:
            print("ERROR:", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    scheduler()