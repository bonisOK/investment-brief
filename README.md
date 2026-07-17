# Investment Brief

Personal advisory dashboard: reads a Google Sheet (MINT MEGA growth book + Thai
Dividend book), resolves Thai DR codes to real global tickers, and twice a day
(06:00 / 18:00 Asia/Bangkok) pulls prices, valuation, CNN Fear & Greed and news
to produce a per-stock BUY/SELL suggestion with a news-backed "why it moved".

**Advisory only — not financial advice. No orders are executed.**

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in FMP_API_KEY, GEMINI_API_KEY
.venv/bin/uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. On first boot with an empty DB a snapshot is built
automatically (takes a few minutes — yfinance + news + Gemini for every name).
`GET/POST /run` triggers a run manually; `GET /api/latest` returns the raw JSON.

## Keys (.env)

| Var | What | Where |
|---|---|---|
| `FMP_API_KEY` | DCF fair value | financialmodelingprep.com (free tier) |
| `GEMINI_API_KEY` | why-moved / sentiment | aistudio.google.com (free tier) |
| `APP_PASSWORD` | optional page login (basic auth, any username) | you |
| `SHEET_ID` | the Google Sheet | defaults to the configured sheet |
| `DB_PATH` | SQLite location | default `data/brief.db` |
| `APPS_SCRIPT_URL` | runs the sheet's `updateMINTprices()` before each pipeline run | deploy the sheet's Apps Script as a web app (see below) |

### Apps Script price refresh

In the sheet: **Extensions → Apps Script**, add:

```javascript
function doGet() {
  updateMINTprices();
  return ContentService.createTextOutput("ok");
}
```

Then **Deploy → New deployment → Web app**, *Execute as: Me*, *Who has
access: Anyone with the link*, and put the deployment URL in `.env` as
`APPS_SCRIPT_URL`. The pipeline calls it before reading the sheet so
Current Price / Current Value are fresh.

Without the two API keys the app still works: valuation falls back to analyst
targets (yfinance) and news sentiment is neutral with headline-only cards.

## Tuning

Everything tunable lives in `config.yaml`: score weights (drift 35% / value 30%
/ F&G 20% / news 15%), label buckets, run times, saturation thresholds.
Mis-resolved DR codes get a manual entry in `overrides.yaml` (base token →
real ticker); delete the cached row from the `ticker_map` table (or the whole
DB) after editing so the override is picked up.

## Deploy (GitHub Actions + encrypted Pages — $0)

The workflow in `.github/workflows/brief.yml` runs the pipeline at 06:00 and
18:00 Bangkok, renders the dashboard to static HTML, encrypts it with
StatiCrypt (AES, password required to view), and publishes to GitHub Pages.
The repo can be public: keys and the sheet ID live only in Actions secrets,
and the published page is unreadable without the password.

1. Create a **public** repo on github.com, then push:
   `git remote add origin https://github.com/<you>/<repo>.git && git push -u origin main`
2. Repo → Settings → Secrets and variables → Actions → add:
   `SHEET_ID`, `FMP_API_KEY`, `GEMINI_API_KEY`, `APPS_SCRIPT_URL`,
   `PAGE_PASSWORD` (the password you'll type to open the page).
3. Repo → Settings → Pages → Source: **GitHub Actions**.
4. Actions tab → "Investment Brief" → **Run workflow** to test.
   Page appears at `https://<you>.github.io/<repo>/`.

Limits vs a real server: scheduled runs can start 5–15 min late, and manual
refresh = the Run workflow button instead of a button on the page.

## Deploy (Railway)

1. Push this repo to GitHub, create a Railway project from it.
2. Add a **volume** mounted at `/data`, set env var `DB_PATH=/data/brief.db`.
3. Set `FMP_API_KEY`, `GEMINI_API_KEY`, `APP_PASSWORD`.
4. `railway.toml` handles the start command and healthcheck. The scheduler runs
   inside the web process — keep it always-on (no sleep-on-idle).

## How the score works

Composite 0–100 (higher = more attractive to buy):

| Sub-score | Weight | Signal |
|---|---|---|
| Drift | 35% | Growth: `Target Cost − Current Value` THB; Dividend: sheet `Real Have to buy` |
| Value | 30% | FMP DCF + analyst target vs price; Thai: yield vs 5-year average |
| Fear & Greed | 20% | Company-specific, contrarian: 40% CNN market gauge (dampened for Thai) + 60% the stock's own CNN-style greed composite — RSI(14), momentum vs 125-day MA, drawdown from 52-week high, volatility spike, up-day volume share (weights in `stock_fng_weights`) |
| News | 15% | Gemini sentiment of recent headlines, −1…+1 |

Buckets: ≥75 STRONG BUY · 60–74 ADD · 40–59 HOLD · 25–39 TRIM · <25 SELL.
Non-HOLD labels also show a suggested THB amount. With a Gemini key the LLM
sizes every order in one batched call (given label, score, gap to target,
moves, valuation, news sentiment and stock greed; sanity-capped at 1.5x the
gap). Without a key it falls back to a rule: a conviction-scaled slice of
the gap (STRONG BUY/SELL = 100%, ADD/TRIM = 50%; `action_fractions`).
Sub-scores are always shown in the row's expanded card so every suggestion is
explainable.
