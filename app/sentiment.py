"""CNN Fear & Greed index (US market gauge)."""
import logging

import requests

from .config import UA

log = logging.getLogger(__name__)
FNG_URL = "https://production.dataviz.cnn.com/index/fearandgreed/graphdata"


def fetch_fng() -> dict:
    """Return {score: 0-100, label, as_of}; neutral 50 if the endpoint is down."""
    try:
        r = requests.get(FNG_URL, headers=UA, timeout=30)
        r.raise_for_status()
        d = r.json()["fear_and_greed"]
        return {
            "score": round(float(d["score"])),
            "label": d.get("rating", "").title(),
            "as_of": d.get("timestamp"),
        }
    except Exception as e:
        log.warning("Fear & Greed fetch failed: %s", e)
        return {"score": 50, "label": "Unavailable (neutral)", "as_of": None}
