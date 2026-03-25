import ee
import geopandas as gpd
from datetime import datetime, timezone, timedelta

PROJECT_ID = "useful-patrol-477308-d5"
GRID_PATH = "bronze/static/denpasar_grid_static.geojson"
DRIVE_FOLDER = "Denpasar Rainfall"

ee.Initialize(project=PROJECT_ID)

print("Connected to project:", PROJECT_ID)

print("Loading grid locally...")
gdf = gpd.read_file(GRID_PATH)

features = []

for _, row in gdf.iterrows():
    geom = ee.Geometry.MultiPolygon(
        row.geometry.__geo_interface__["coordinates"]
    )

    feature = ee.Feature(
        geom,
        {
            "grid_id": int(row["grid_id"]),
            "mean_elevation": float(row["mean_elevation"]),
            "percent_lowland": float(row["percent_lowland"]),
            "urban_ratio": float(row["urban_ratio"])
        }
    )

    features.append(feature)

grid_fc = ee.FeatureCollection(features)

print("Preparing IMERG V07...")
imerg = ee.ImageCollection("NASA/GPM_L3/IMERG_V07") \
    .select("precipitation")

start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
end_date = datetime.now(timezone.utc).date()

current = start_date.date()

while current <= end_date:

    next_day = current + timedelta(days=1)
    day_label = current.strftime("%Y_%m_%d")

    print(f"Preparing export for {day_label}")

    start = ee.Date(current.strftime("%Y-%m-%d"))
    end = ee.Date(next_day.strftime("%Y-%m-%d"))

    total_hours = end.difference(start, "hour")
    hours = ee.List.sequence(0, total_hours.subtract(1))

    def hourly_reduce(hour_offset):

        hour_start = start.advance(hour_offset, "hour")
        hour_end = hour_start.advance(1, "hour")

        hourly_coll = imerg.filterDate(hour_start, hour_end)

        def compute_when_exists():
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

        def compute_when_empty():
            return grid_fc.map(
                lambda f: f.set({
                    "timestamp_utc": hour_start.format("YYYY-MM-dd HH:mm:ss"),
                    "rainfall_mm": None
                })
            )

        return ee.FeatureCollection(
            ee.Algorithms.If(
                hourly_coll.size().gt(0),
                compute_when_exists(),
                compute_when_empty()
            )
        )

    daily_collection = ee.FeatureCollection(
        hours.map(hourly_reduce)
    ).flatten()

    task = ee.batch.Export.table.toDrive(
        collection=daily_collection,
        description=f"denpasar_rainfall_{day_label}",
        folder=DRIVE_FOLDER,
        fileNamePrefix=f"denpasar_rainfall_{day_label}",
        fileFormat="CSV"
    )

    task.start()

    print(f"Started export for {day_label}")

    current = next_day

print("All daily exports submitted.")
print("Go to Earth Engine → Tasks tab → Run tasks.")