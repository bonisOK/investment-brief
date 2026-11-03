"""Pure trade-log maths for the portfolio tabs: parse the sheet's transaction
rows, replay them day by day, and compute realized gain / dividends exactly
the way the sheet's REALIZED GAIN tabs do. No network, no globals."""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

KINDS = {"deposit", "buy", "sell", "dividend"}
_MDY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DR_DIGITS = re.compile(r"\d+$")


@dataclass(frozen=True)
class Txn:
    date: date
    kind: str            # deposit | buy | sell | dividend
    code: str            # DR / Thai code; "" for deposits
    shares: float        # > 0 bought, < 0 sold, 0 for cash-only rows
    price: float | None  # ฿ per share for buys / sells
    cash: float          # signed cash flow, the sheet's "Final" column
    row: int             # sheet row number, for warnings


def is_dr(code: str) -> bool:
    """Thai DRs end in digits (NVDA01, PEP80); plain SET names don't."""
    return bool(_DR_DIGITS.search(code or ""))


def parse_date(text: str) -> date | None:
    """'2/27/2026', '5/10/2026 0:00:00', '6/10/2026 11:03:21' or '2026-02-27'."""
    s = (text or "").strip()
    m = _MDY.match(s)
    if m:
        mo, d, y = map(int, m.groups())
        return date(y, mo, d)
    m = _ISO.match(s)
    if m:
        return date(*map(int, m.groups()))
    return None


def parse(rows: list[dict]) -> tuple[list[Txn], list[str]]:
    """Sheet rows (sheets.read_transactions) -> Txns sorted by date, plus
    human-readable warnings for rows that had to be skipped."""
    txns, warns = [], []
    for i, r in enumerate(rows, start=2):  # row 1 is the header
        kind = (r.get("type") or "").strip().lower()
        if kind not in KINDS:
            continue
        d = parse_date(r.get("date"))
        if d is None:
            warns.append(f"row {i}: unreadable date {r.get('date')!r}, skipped")
            continue
        cash = r.get("final")
        if cash is None and kind == "deposit":
            cash = r.get("before_com")
        if cash is None:
            warns.append(f"row {i}: no amount, skipped")
            continue
        code = (r.get("ticker") or "").strip().upper()
        shares = abs(r.get("shares") or 0.0)
        if kind == "deposit":
            code, shares = "", 0.0
        elif kind == "dividend":
            shares = 0.0
        elif kind == "sell":
            shares = -shares
        if kind in ("buy", "sell") and (not code or not shares):
            warns.append(f"row {i}: {kind} without ticker or shares, skipped")
            continue
        txns.append(Txn(d, kind, code, shares, r.get("price"), float(cash), i))
    txns.sort(key=lambda t: t.date)  # stable: same-day rows keep sheet order
    return txns, warns


def business_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def replay(txns: list[Txn], days: list[date]) -> tuple[list[dict], list[float], list[str]]:
    """Shares held and cash at the close of each day. A transaction dated on a
    weekend lands on the next business day. Selling more than is held caps the
    holding at zero (the cash still moves, as in the sheet) and warns."""
    held: dict[str, float] = {}
    cash = 0.0
    holdings, cashes, warns = [], [], []
    i = 0
    for day in days:
        while i < len(txns) and txns[i].date <= day:
            t = txns[i]
            cash += t.cash
            if t.shares:
                new = held.get(t.code, 0.0) + t.shares
                if new < -1e-6:
                    warns.append(f"row {t.row}: sells {-t.shares:,.0f} {t.code} "
                                 f"but only {held.get(t.code, 0.0):,.0f} were held")
                    new = 0.0
                held[t.code] = new if abs(new) > 1e-6 else 0.0
            i += 1
        holdings.append({c: s for c, s in held.items() if s})
        cashes.append(cash)
    return holdings, cashes, warns


def ticker_stats(txns: list[Txn], held: dict[str, float]) -> dict[str, dict]:
    """Per ticker, the sheet's own REALIZED GAIN method: a running average
    cost (buys add their ฿ incl. fees; a sell takes shares out at the average
    of that moment, and a full sell-out resets it), and realized = shares x
    sell price (before the sell fee) - shares x that average. A sell larger
    than the holding still uses the last average, as the sheet does.
    `held` (shares held now) is reported alongside."""
    st: dict[str, dict] = {}
    for t in txns:
        if not t.code:
            continue
        s = st.setdefault(t.code, {"bought_shares": 0.0, "bought_thb": 0.0,
                                   "sold_shares": 0.0, "sold_thb": 0.0, "dividends": 0.0,
                                   "realized": 0.0, "_sh": 0.0, "_cost": 0.0, "_avg": 0.0})
        if t.kind == "buy":
            s["bought_shares"] += t.shares
            s["bought_thb"] += -t.cash
            s["_sh"] += t.shares
            s["_cost"] += -t.cash
            s["_avg"] = s["_cost"] / s["_sh"] if s["_sh"] > 1e-9 else s["_avg"]
        elif t.kind == "sell":
            n = -t.shares
            gross = n * t.price if t.price else t.cash
            s["sold_shares"] += n
            s["sold_thb"] += t.cash
            s["realized"] += gross - n * s["_avg"]
            s["_sh"] -= n
            s["_cost"] -= n * s["_avg"]
            if s["_sh"] <= 1e-6:
                s["_sh"], s["_cost"] = 0.0, 0.0
        elif t.kind == "dividend":
            s["dividends"] += t.cash
    for code, s in st.items():
        s["cost_per_share"] = s.pop("_avg")
        s.pop("_sh"), s.pop("_cost")
        s["held_shares"] = held.get(code, 0.0)
        s["held_cost"] = s["held_shares"] * s["cost_per_share"]
    return st


def deposits(txns: list[Txn]) -> list[tuple[date, float]]:
    return [(t.date, t.cash) for t in txns if t.kind == "deposit"]


def bad_price_rows(txns: list[Txn], factor: float = 5.0) -> set[int]:
    """Trade rows whose price is more than `factor` x off the ticker's median
    trade price: almost always shares and price typed into swapped cells."""
    prices: dict[str, list[float]] = {}
    for t in txns:
        if t.kind in ("buy", "sell") and t.price:
            prices.setdefault(t.code, []).append(t.price)
    meds = {c: median(p) for c, p in prices.items()}
    bad = set()
    for t in txns:
        if t.kind in ("buy", "sell"):
            m = meds.get(t.code)
            if not t.price or (m and not (m / factor <= t.price <= m * factor)):
                bad.add(t.row)
    return bad
