"""Prometheus metric definitions (names follow docs/ARCHITECTURE.md §7).

They live in one module so a name is defined exactly once. The /metrics endpoint on its own
internal port arrives with P3; until then the counters are recorded but not yet scraped.
"""

from prometheus_client import Counter

# `op` is always a fixed literal from the code (ratelimit, cache_get, neg_get, cache_set, neg_set,
# cache_del, enqueue, readyz, ...), never user input, so the label set stays small.
REDIS_ERRORS = Counter(
    "redis_errors_total",
    "Redis operations that failed or timed out and used their fallback",
    ["op"],
)

# Every redirect that reaches the cache lookup: hit (link cache), negative (neg cache), or miss
# (had to ask Postgres).
REDIRECT_CACHE = Counter(
    "redirect_cache_total",
    "Redirect lookups by cache result",
    ["result"],
)
