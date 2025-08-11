
# Personal Finance Tracker – Dark Dashboard

Sleek dark‑theme dashboard with Chart.js charts, basic AI insight, and Flask backend.

## Features

* Responsive card layout inspired by modern finance dashboards
* Line charts, combined chart & doughnut chart via Chart.js 4
* Add transactions (positive income or negative expense)
* Optional simple investments table for assets donut
* Basic AI heuristic flags if a single category >50 % of spending
* Works out‑of‑the‑box with SQLite; switch `DATABASE_URL` for Postgres
* CSS variables make theming easy

## Quick start

```bash
git clone https://github.com/yourusername/personal-finance-tracker.git
cd personal-finance-tracker
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# optional: export FLASK_APP=app.py
python app.py
```

Open `http://127.0.0.1:5000` – add a few transactions and see the dashboard update.

## Deploy

*Render / Railway / Fly / Heroku* – same as previous guide.  
Use gunicorn: `gunicorn app:app` and add a Dockerfile if desired.

## Screenshot

![Dashboard screenshot](screenshot.png)
