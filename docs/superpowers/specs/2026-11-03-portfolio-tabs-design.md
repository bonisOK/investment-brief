# Portfolio tabs: design

Approved 2026-11-03. Mockup: claude.ai artifact "Portfolio Tabs Mockup" (v2).

## Goal
Add two tabs next to the brief, **Growth** and **Dividend**, each showing the
book's summary, allocation, performance vs a shadow index, trades, buy win
rate, sell check and per-stock contribution. Same encrypted static page, same
twice-daily GitHub Actions run, no new secrets.

## Data flow
1. `sheets.read_transactions(tab)` reads `Transaction` / `DividendTransaction`
   (columns A:G: Date, Type, Ticker, Shares, Price/Share, Before Com, Final).
2. `ledger.py` (pure): parse rows into `Txn`s, replay them day by day into
   shares held + cash, and compute realized gain / dividends per ticker with
   the sheet's own method (cost per share = total buy ฿ / total shares bought).
   Sells beyond holdings are capped at zero shares and reported as warnings.
3. `pricehist.py`: daily ฿ price per ticker. Order of preference:
   a. the DR's own Yahoo history (`NVDA01.BK`), b. the resolved underlying ×
   its currency→THB rate × a DR ratio fitted to the user's own trade prices
   (median of trade price ÷ underlying ฿ close), c. the trade prices themselves
   (marked "est."). The sheet's Current Price (growth) is used for today.
4. `portfolio.py`: daily value = Σ shares × price + cash; shadow index =
   every deposit buys the index on its date (SPY / QQQ dividend-adjusted ×
   USD/THB; SET price only); summary, allocation, contribution, lots, sells,
   trade markers. Reconciles today's rebuilt value against the sheet total
   and saves a snapshot per run (`portfolio_snapshots` table).
5. `pipeline.run_pipeline` adds `payload["portfolio"]`; any failure there is
   caught and shown as "couldn't build" on that tab; the brief still ships.

## Definitions
- Buy win rate: each Buy row is a lot; win = today's value of its shares >
  ฿ paid incl. fees. Rows whose price is >5× off the ticker's median trade
  price are skipped as bad rows.
- Sell check: (today's price − sell price) × shares sold. Positive = sold early.
- Contribution: realized + unrealized + dividends per ticker (sold-out included).

## Page
Tabs Brief | Growth | Dividend (hash routed). Layout per mockup: summary,
performance (Vs index / Trades toggle, Compare dropdown, 1M/3M/YTD/All,
bought-vs-sold bars), allocation donut (every stock its own slice, logos on
slices, list shows top 10 + Cash with "Show all"), contribution, win rate,
sell check. Hide ฿ masks amounts and hides chart axes.

## Testing
Unit tests on tiny ledgers: replay = expected shares/cash; realized matches
the sheet method; shadow of a flat index = money in; ratio fit recovers a known
ratio; lots / sells with known answers; template renders with a fake payload.
