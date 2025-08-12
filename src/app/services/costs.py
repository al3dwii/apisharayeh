import json, os
from functools import lru_cache

DEFAULT = {"gpt-4o-mini": {"in": 0.15, "out": 0.60}}  # USD per 1k

@lru_cache(maxsize=1)
def cost_table():
    raw = os.getenv("COST_TABLE_JSON")
    if not raw:
        return DEFAULT
    try:
        t = json.loads(raw)
        return {**DEFAULT, **t}
    except Exception:
        return DEFAULT

def estimate_cost_cents(model: str, in_tokens: int, out_tokens: int) -> int:
    table = cost_table()
    row = table.get(model, list(table.values())[0])
    cents = (row["in"] * in_tokens / 1000.0 + row["out"] * out_tokens / 1000.0) * 100
    return int(round(cents))
