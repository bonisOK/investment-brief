"""Gemini: batched why-moved / SO WHAT / sentiment + order sizing.

The current Gemini free tier allows only ~20 requests/day per model, so
per-stock analysis is batched (~15 stocks per call, ~5 calls per run)
instead of one call per ticker. On daily-quota exhaustion we fall back to
the -lite model (separate per-model quota) before giving up.
"""
import json
import logging
import os
import re
import time

from .config import CFG, GEMINI_API_KEY
from .news import age_label

log = logging.getLogger(__name__)
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
LITE_MODEL = "gemini-flash-lite-latest"
CHUNK_SIZE = 15
CALL_GAP_S = 7  # free tier allows 10 requests/min; pace between calls

_models: dict = {}
_active = MODEL


def _get(name: str):
    if name not in _models:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _models[name] = genai.GenerativeModel(name)
    return _models[name]


def _generate(prompt: str) -> str:
    """One Gemini call, falling back to the lite model (its own daily quota)
    when the primary model's quota is exhausted."""
    global _active
    try:
        return _get(_active).generate_content(prompt).text
    except Exception as e:
        if _active != LITE_MODEL and ("429" in str(e) or "quota" in str(e).lower()):
            log.warning("quota hit on %s, switching to %s", _active, LITE_MODEL)
            _active = LITE_MODEL
            return _get(_active).generate_content(prompt).text
        raise


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text)


def fallback(chg_1d) -> dict:
    move = "flat"
    if chg_1d is not None:
        move = "up" if chg_1d > 0 else "down" if chg_1d < 0 else "flat"
    return {
        "why_moved": f"No AI analysis available; the stock is {move} on the day.",
        "so_what": "",
        "sentiment": 0.0,
    }


BATCH_PROMPT = """You are an equity analyst writing a twice-daily brief for a retail holder.

For EACH stock below, produce:
- "why_moved": one or two plain-English sentences on the LIKELY driver of the
  recent move (say "likely" — this is inference from headlines, not proven
  causation).
- "so_what": one sentence on the implication for someone holding this stock.
- "sentiment": a number in [-1, 1] for the overall news tone.

Each headline is tagged with its age. Only attribute the 1-day move to
headlines fresh enough to explain it; older items are background context.

Stocks:
{blocks}

Reply with ONLY a JSON object (no markdown fences) mapping each stock's KEY
exactly as given to {{"why_moved": ..., "so_what": ..., "sentiment": ...}}.
All text in English."""


def analyze_batch(rows: list[dict]) -> dict[str, dict]:
    """Batched analysis for every row that has headlines. Returns
    {'book:DR_CODE': {why_moved, so_what, sentiment}}; missing keys mean the
    caller should use fallback()."""
    if not GEMINI_API_KEY:
        return {}
    with_news = [r for r in rows if r.get("news")]
    out: dict[str, dict] = {}
    for i in range(0, len(with_news), CHUNK_SIZE):
        chunk = with_news[i:i + CHUNK_SIZE]
        blocks = []
        for r in chunk:
            heads = "\n".join(
                f"  - [{age_label(h.get('age_h'))}] {h['title']} ({h['publisher']})"
                for h in r["news"])
            blocks.append(
                f"KEY: {r['book']}:{r['dr_code']}\n"
                f"Company: {r['company_name']} ({r['real_ticker']})\n"
                f"Moves: 1d {r.get('chg_1d')}%, 1w {r.get('chg_1w')}%, 1m {r.get('chg_1m')}%\n"
                f"Headlines:\n{heads}"
            )
        if i:
            time.sleep(CALL_GAP_S)
        try:
            data = _parse_json(_generate(BATCH_PROMPT.format(blocks="\n\n".join(blocks))))
        except Exception as e:
            log.warning("Gemini batch %d failed: %s", i // CHUNK_SIZE, str(e)[:300])
            continue
        for key, a in data.items():
            if not isinstance(a, dict):
                continue
            try:
                out[key] = {
                    "why_moved": str(a.get("why_moved", ""))[:600],
                    "so_what": str(a.get("so_what", ""))[:400],
                    "sentiment": max(-1.0, min(1.0, float(a.get("sentiment", 0)))),
                }
            except (TypeError, ValueError):
                continue
    return out


BRIEF_PROMPT = """You are choosing the MOST IMPORTANT news of the day for the
owner of this personal portfolio. Pick what they genuinely need to know today.

Rank candidates by this weighting:
- {w[actionability]}%: actionability — the stock is labeled STRONG BUY, SELL or
  TRIM today, so the news supports or contradicts an action about to be taken.
- {w[impact]}%: confirmed impact — the headline coincides with a real 1-day move.
- {w[position]}%: position size — news about a large THB holding outranks a
  small one.
- {w[severity]}%: event severity — earnings/guidance, M&A, regulation, dividend
  changes, CEO changes > analyst notes > product PR.
Hard rules: merge duplicates (one story affecting several stocks = ONE item
listing all affected tickers). Drop listicles, "stocks to watch" fluff and
pure PR. Macro items (M*) qualify when they move the whole portfolio.
Freshness rules: every headline is tagged with its age. Prefer items under
24h. Items older than 24h must never be "act". Drop retrospectives,
anniversary pieces and anything reporting an event that is not from the
last two days.

Portfolio (KEY | company | position_thb | label | today_action_thb | 1d%):
{stocks}

Stock headlines (each starts with its ID):
{headlines}

Macro headlines:
{macro}

Reply with ONLY a JSON object, no markdown fences:
{{"summary": ["<2-4 bullets, one short plain-English sentence each: the day's
               theme for THIS portfolio. No bullet characters, just the text.>"],
  "items": [{{"id": "<headline ID>", "tickers": ["<DR codes affected, empty for macro>"],
             "headline": "<max 12 words: the IMPLICATION for this portfolio,
                          written as a headline. NOT the source's headline and
                          not a restatement of it — say what it MEANS for the
                          holder (e.g. 'Margin guidance cut puts the buy case
                          on hold', not 'Acme reports Q3 earnings').>",
             "implication": "bullish" | "bearish" | "neutral",
             "why": "<one sentence: why it matters to THIS portfolio>",
             "action": "<one short imperative sentence: what to DO about it today,
                        consistent with today_action_thb (e.g. 'Buy the planned
                        tranche on this dip', 'Pause buying until guidance call',
                        'No trade — reassess if it breaks 60'); never invent
                        amounts that contradict the portfolio table>",
             "urgency": "act" | "watch" | "fyi"}}]}}
At most {n} items, ranked most important first. English only."""


def _summary_bullets(raw) -> list[str]:
    """The summary renders as a bullet list. Gemini sometimes still returns one
    paragraph (and snapshots saved before this was a list hold a plain string),
    so split prose back into sentences rather than showing a wall of text."""
    if isinstance(raw, str):
        # Either a newline-separated list or one paragraph of sentences.
        raw = [s for s in re.split(r"\n+|(?<=[.!?])\s+", raw) if s.strip()]
    if not isinstance(raw, list):
        return []
    return [str(s).lstrip("-•* \t").strip()[:200] for s in raw if str(s).strip()][:5]


def daily_brief(rows: list[dict], macro: list[dict]) -> dict | None:
    """One Gemini call that picks the day's most important news. Returns
    {summary: [bullet, ...],
     items: [{headline, title, link, publisher, tickers, implication, why,
              action, urgency}]}."""
    if not GEMINI_API_KEY:
        return None
    cfg = CFG["news_brief"]
    registry: dict[str, dict] = {}
    stock_lines, head_lines = [], []
    hid = 0
    max_age = cfg.get("max_age_hours", 48)
    for r in rows:
        fresh = [h for h in r.get("news", [])
                 if h.get("age_h") is not None and h["age_h"] <= max_age]
        if not fresh:
            continue
        pos = r.get("current_value") or r.get("bought") or 0
        act = r["scores"].get("act_thb") or 0
        stock_lines.append(
            f"{r['dr_code']} | {r['company_name']} | {pos:,.0f} | "
            f"{r['scores']['label']} | {act:,.0f} | {r.get('chg_1d')}%")
        for h in fresh:
            hid += 1
            key = f"H{hid}"
            registry[key] = {**h, "ticker": r["dr_code"]}
            head_lines.append(f"{key} [{r['dr_code']}] [{age_label(h.get('age_h'))}] "
                              f"{h['title']} ({h['publisher']})")
    macro_lines = []
    for i, h in enumerate(macro, 1):
        key = f"M{i}"
        registry[key] = {**h, "ticker": None}
        macro_lines.append(f"{key} [{age_label(h.get('age_h'))}] {h['title']} ({h['publisher']})")

    prompt = BRIEF_PROMPT.format(
        w=cfg["weights"], n=cfg["max_items"],
        stocks="\n".join(stock_lines),
        headlines="\n".join(head_lines),
        macro="\n".join(macro_lines) or "(none)",
    )
    try:
        time.sleep(CALL_GAP_S)
        data = _parse_json(_generate(prompt))
        items = []
        for it in data.get("items", [])[:cfg["max_items"]]:
            src = registry.get(str(it.get("id", "")))
            if not src:
                continue
            impl = it.get("implication")
            items.append({
                # The card shows `headline` (the AI's implication); `title` is
                # kept as the source's own wording for the link tooltip.
                "headline": str(it.get("headline", "")).strip()[:140] or src["title"],
                "title": src["title"],
                "link": src["link"],
                "publisher": src["publisher"],
                "tickers": [str(t) for t in (it.get("tickers") or []) if t][:6],
                "implication": impl if impl in ("bullish", "bearish", "neutral") else "neutral",
                "why": str(it.get("why", ""))[:300],
                "action": str(it.get("action", ""))[:250],
                "urgency": it.get("urgency") if it.get("urgency") in ("act", "watch", "fyi") else "fyi",
            })
        return {"summary": _summary_bullets(data.get("summary")), "items": items}
    except Exception as e:
        log.warning("Gemini daily_brief failed: %s", str(e)[:300])
        return None


AMOUNTS_PROMPT = """You are sizing orders for a personal portfolio (currency: THB).
For each holding below decide how much to BUY (positive) or SELL (negative)
TODAY, as a plain THB amount. This is today's tranche, NOT the whole
rebalancing plan. Guidance:
- gap_thb is the TOTAL remaining distance to the owner's allocation target
  (positive = under-allocated). Today's amount should normally be a tranche
  of it — around 10-40% of the gap — scaled up toward the full gap only on
  the strongest convictions (extreme score + cheap + fear + positive news),
  and scaled down or 0 when conviction is weak. Never exceed 1.5x |gap_thb|.
  If gap_thb is 0, the amount is 0 unless conviction is extreme.
- Strong composite score + cheap valuation + fear (low greed) + positive
  news -> act on more of a positive gap today. Weak score + expensive +
  greed -> act on more of a negative gap today.
- label HOLD usually means 0. Round to hundreds. 0 = no action today.

Holdings (key | label | score 0-100 | gap_thb | price | currency | 1d% | 1m% | undervalued% | news_sentiment -1..1 | stock_greed 0-100):
{table}

Reply with ONLY a JSON object, no markdown fences, mapping every key to
{{"amount": <signed integer THB>, "reason": "<one short sentence, max 15
words, explaining the decision>", "limit_price": <suggested execution price
in the stock's OWN currency — a sensible limit near the current price (e.g.
a small pullback for buys, a small pop for sells); null when amount is 0>}}.
Example: {{"growth:PLTR01": {{"amount": 250000, "reason": "Far under target and oversold; start closing the gap.", "limit_price": 130.5}}}}"""


def decide_amounts(rows: list[dict]) -> dict[str, dict]:
    """One batched Gemini call that sizes every buy/sell and explains it.
    Returns {'book:DR_CODE': {'amount': signed_thb, 'reason': str}};
    empty dict -> caller keeps rule-based amounts and reasons."""
    if not GEMINI_API_KEY or not rows:
        return {}
    lines = []
    for r in rows:
        s = r["scores"]
        lines.append(
            f"{r['book']}:{r['dr_code']} | {s['label']} | {s['composite']} | "
            f"{(r.get('buy_sell_thb') or 0):.0f} | {r.get('price')} | {r.get('currency')} | "
            f"{r.get('chg_1d')} | {r.get('chg_1m')} | "
            f"{r.get('undervalued_pct')} | {r.get('news_sentiment')} | {r.get('stock_greed')}"
        )
    try:
        time.sleep(CALL_GAP_S)
        data = _parse_json(_generate(AMOUNTS_PROMPT.format(table="\n".join(lines))))
        out = {}
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(v.get("amount"), (int, float)):
                price = v.get("limit_price")
                out[k] = {"amount": float(v["amount"]),
                          "reason": str(v.get("reason", ""))[:200],
                          "limit_price": float(price) if isinstance(price, (int, float)) else None}
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = {"amount": float(v), "reason": "", "limit_price": None}
        return out
    except Exception as e:
        log.warning("Gemini decide_amounts failed: %s", str(e)[:300])
        return {}
