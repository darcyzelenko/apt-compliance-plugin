# Apartment Compliance Checker

Checks apartment designs against the ADG Victoria 2017 (Clause 55 & 58) and NSW Apartment Design Guide 2015.

## Quick deploy to Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select the repo — it auto-detects and deploys
4. Done — Railway gives you a public URL

## Run locally

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## Usage

Upload a DXF file with `APT_` layer names, or use the Plan Tracer at `/trace` to draw from a floor plan image.
