import ee
import json
import os

ee.Initialize()

os.makedirs("bronze/static", exist_ok=True)

output_path = "bronze/static/denpasar_grid_static.geojson"

# =========================
# Load Denpasar boundary
# =========================

adm2 = (
    ee.FeatureCollection("FAO/GAUL/2015/level2")
    .filter(ee.Filter.eq("ADM0_NAME", "Indonesia"))
    .filter(ee.Filter.eq("ADM1_NAME", "Bali"))
    .filter(ee.Filter.eq("ADM2_NAME", "Kota Denpasar"))
)

denpasar_geom = adm2.geometry()

# =========================
# Create 1km grid manually
# =========================

scale_km = 1
deg_per_km = 1 / 111.32  # approx conversion

bounds = denpasar_geom.bounds().coordinates().get(0).getInfo()

min_lon = bounds[0][0]
min_lat = bounds[0][1]
max_lon = bounds[2][0]
max_lat = bounds[2][1]

lon_list = []
lat_list = []

lon = min_lon
while lon < max_lon:
    lon_list.append(lon)
    lon += deg_per_km

lat = min_lat
while lat < max_lat:
    lat_list.append(lat)
    lat += deg_per_km

grid_features = []

for lon in lon_list:
    for lat in lat_list:
        cell = ee.Geometry.Rectangle([
            lon,
            lat,
            lon + deg_per_km,
            lat + deg_per_km
        ])
        if cell.intersects(denpasar_geom).getInfo():
            grid_features.append(ee.Feature(cell))

grid = ee.FeatureCollection(grid_features)

# =========================
# Load datasets
# =========================

dem = ee.Image("USGS/SRTMGL1_003")
worldcover = ee.Image("ESA/WorldCover/v100/2020")

# =========================
# Elevation
# =========================

grid_elev = dem.reduceRegions(
    collection=grid,
    reducer=ee.Reducer.mean(),
    scale=30
).map(lambda f: f.set("mean_elevation", f.get("mean")))

# =========================
# Lowland percentage (<20m)
# =========================

lowland_img = dem.lt(20)

grid_lowland = lowland_img.reduceRegions(
    collection=grid_elev,
    reducer=ee.Reducer.mean(),
    scale=30
).map(lambda f: f.set("percent_lowland", f.get("mean")))

# =========================
# Urban ratio (class 50)
# =========================

urban_img = worldcover.eq(50)

grid_final = urban_img.reduceRegions(
    collection=grid_lowland,
    reducer=ee.Reducer.mean(),
    scale=10
).map(lambda f: f.set("urban_ratio", f.get("mean")))

# =========================
# Convert to GeoJSON
# =========================

features = grid_final.getInfo()["features"]

geojson_output = {
    "type": "FeatureCollection",
    "features": []
}

for i, f in enumerate(features):
    geojson_output["features"].append({
        "type": "Feature",
        "geometry": f["geometry"],
        "properties": {
            "grid_id": i,
            "mean_elevation": f["properties"].get("mean_elevation"),
            "percent_lowland": f["properties"].get("percent_lowland"),
            "urban_ratio": f["properties"].get("urban_ratio")
        }
    })

with open(output_path, "w") as f:
    json.dump(geojson_output, f, indent=2)

print("GeoJSON grid static features saved.")
print("Total grid cells:", len(geojson_output["features"]))