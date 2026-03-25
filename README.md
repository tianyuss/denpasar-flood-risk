# Denpasar Flood Risk Monitoring

This project shows flood risk in Denpasar using satellite rainfall data.
It turns raw rainfall into a simple map that shows which areas are more likely to flood.

Data comes from NASA IMERG (satellite rainfall).
Then it is processed in 3 steps:

* Bronze: raw rainfall data
* Silver: rainfall accumulation (1h, 6h, 24h)
* Gold: flood risk score based on rainfall, elevation, and urbanization

Dark red means higher flood risk.
Light color means lower risk.
Each grid represents a small area in Denpasar, so you can check if your kost is in a higher risk zone.

This repo only includes January-February 2026 data.
The system is designed to update near real-time using Google Earth Engine.

How to run:
pip install -r requirements.txt
python -m streamlit run app/streamlit_app.py

Demo: https://denpasar-flood-risk.streamlit.app/
