import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
BRONZE_PATH = BASE_DIR / "bronze" / "imerg"
SILVER_PATH = BASE_DIR / "silver" / "rainfall"

# --------------------------------------------------
# Collect bronze files
# --------------------------------------------------
bronze_files = sorted(BRONZE_PATH.rglob("day=*.parquet"))

if not bronze_files:
    print("No bronze files found.")
    exit()

def extract_date_from_path(f):
    year = int(f.parts[-3].split("=")[1])
    month = int(f.parts[-2].split("=")[1])
    day = int(f.name.replace("day=", "").replace(".parquet", ""))
    return datetime(year, month, day).date()

bronze_dates = sorted([extract_date_from_path(f) for f in bronze_files])
latest_bronze = max(bronze_dates)

# --------------------------------------------------
# Collect silver files (if any)
# --------------------------------------------------
silver_files = sorted(SILVER_PATH.rglob("day=*.parquet"))

if not silver_files:
    print("Mode: FULL HISTORICAL BUILD")
    mode = "full"
    write_dates = bronze_dates
else:
    silver_dates = sorted([extract_date_from_path(f) for f in silver_files])
    latest_silver = max(silver_dates)

    print("Latest bronze:", latest_bronze)
    print("Latest silver:", latest_silver)

    utc_today = datetime.now(timezone.utc).date()

    rolling_dates = {
        utc_today,
        utc_today - timedelta(days=1),
        utc_today - timedelta(days=2),
    }

    # If silver behind bronze → rebuild missing gap
    if latest_silver < latest_bronze:
        print("Mode: GAP + ROLLING UPDATE")
        gap_start = latest_silver + timedelta(days=1)
        gap_dates = [
            d for d in bronze_dates if d >= gap_start
        ]
        write_dates = set(gap_dates) | rolling_dates
    else:
        print("Mode: ROLLING UPDATE")
        write_dates = rolling_dates

print("Write dates:", sorted(write_dates))

# --------------------------------------------------
# Determine compute start date
# We need 1 extra day before earliest write date
# --------------------------------------------------
compute_start = min(write_dates) - timedelta(days=1)

selected_files = []

for f in bronze_files:
    file_date = extract_date_from_path(f)
    if file_date >= compute_start:
        selected_files.append(f)

print(f"Processing {len(selected_files)} bronze files")

# --------------------------------------------------
# Load bronze
# --------------------------------------------------
dfs = [pd.read_parquet(f) for f in selected_files]
df = pd.concat(dfs, ignore_index=True)

df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
df = df.sort_values(["grid_id", "timestamp_utc"])

# --------------------------------------------------
# Rolling features
# --------------------------------------------------
df["rainfall_1h"] = df["rainfall_mm"]

for window in [3, 6, 12, 24]:
    df[f"rainfall_{window}h_sum"] = (
        df.groupby("grid_id")["rainfall_mm"]
          .rolling(window=window, min_periods=1)
          .sum()
          .reset_index(level=0, drop=True)
    )

# --------------------------------------------------
# Write silver partitions
# --------------------------------------------------
for date, group in df.groupby(df["timestamp_utc"].dt.date):

    if date not in write_dates:
        continue

    year = f"{date.year:04d}"
    month = f"{date.month:02d}"
    day = f"{date.day:02d}"

    partition_path = SILVER_PATH / f"year={year}" / f"month={month}"
    partition_path.mkdir(parents=True, exist_ok=True)

    output_file = partition_path / f"day={day}.parquet"

    group.to_parquet(output_file, index=False)

    print("Saved →", output_file)

print("\nSilver build complete.")