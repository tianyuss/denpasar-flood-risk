import io
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseDownload


# ========= CONFIG =========
FOLDER_ID = "1NpS0HCHcSXdr-VgefrjMqiYHHj1Sj82i"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_BASE_PATH = BASE_DIR / "bronze" / "imerg"
SERVICE_ACCOUNT_FILE = BASE_DIR / "secrets" / "service_account.json"


# ========= AUTH =========
creds = service_account.Credentials.from_service_account_file(
    str(SERVICE_ACCOUNT_FILE),
    scopes=SCOPES
)

drive_service = build("drive", "v3", credentials=creds)

print("Connected to Google Drive")


# ========= DETERMINE TARGET DATES =========
today = datetime.now(timezone.utc).date()

target_dates = {
    today,
    today - timedelta(days=1),
    today - timedelta(days=2)
}

print("Target dates:", sorted(target_dates))


# ========= LIST FILES =========
query = f"'{FOLDER_ID}' in parents and mimeType='text/csv'"

results = drive_service.files().list(
    q=query,
    fields="files(id, name)",
    pageSize=1000
).execute()

files = results.get("files", [])

if not files:
    print("No CSV files found in Drive folder.")
    exit()

print(f"Found {len(files)} CSV files in Drive.")


# ========= PROCESS FILES =========
for file in files:

    file_id = file["id"]
    filename = file["name"]

    match = re.search(r"(\d{4})_(\d{2})_(\d{2})", filename)
    if not match:
        continue

    year, month, day = match.groups()
    file_date = datetime(int(year), int(month), int(day)).date()

    # Only process rolling 3-day window
    if file_date not in target_dates:
        continue

    print(f"\nProcessing {filename}")

    try:
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

        partition_path = LOCAL_BASE_PATH / f"year={year}" / f"month={month}"
        partition_path.mkdir(parents=True, exist_ok=True)

        parquet_path = partition_path / f"day={day}.parquet"

        # Idempotent overwrite
        df.to_parquet(parquet_path, index=False)

        print(f"Overwritten parquet → {parquet_path}")

    except Exception as e:
        print(f"Error processing {filename}: {e}")
        continue

print("\nRolling 3-day ingestion complete.")