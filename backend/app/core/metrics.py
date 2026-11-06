"""Prometheus metric definitions (names follow docs/ARCHITECTURE.md §7).

They live in one module so a name is defined exactly once. The /metrics endpoint on its own
internal port arrives with P3; until then the counters are recorded but not yet scraped.
"""

from prometheus_client import Counter, Gauge, Histogram

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

# Requests refused with 429 because a limit was exceeded (not Redis failures: those are
# redis_errors_total). scope = redirect | login | create.
RATE_LIMIT_REJECTIONS = Counter(
    "rate_limit_rejections_total",
    "Requests rejected by a rate limit",
    ["scope"],
)

# Clicks queued on the Redis stream, and clicks lost because Redis could not take them (the
# redirect still succeeded). Their gap to click_events_processed_total (worker) is the loss.
CLICKS_ENQUEUED = Counter("click_events_enqueued_total", "Click events added to the stream")
CLICKS_DROPPED = Counter(
    "click_events_dropped_total", "Click events not queued because Redis failed or was unreachable"
)

# Worker side (ARCHITECTURE §7): what happened to each message it finished.
CLICKS_PROCESSED = Counter(
    "click_events_processed_total",
    "Click events the worker finished, by outcome",
    ["status"],  # ok | duplicate | orphan | dead
)
CLICK_BATCH_SECONDS = Histogram(
    "click_worker_batch_seconds",
    "Seconds the worker spent on one batch (database work and acknowledgement)",
)

# --- HTTP (ARCHITECTURE §7) -------------------------------------------------------------------
# `route` is the route TEMPLATE ("/{code}", "/api/v1/links/{id}"), never the raw path, so the number
# of time series stays small no matter how many links or junk URLs exist.
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by method, route template and status",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "Server-side request duration up to the last response byte",
    ["method", "route", "status"],
    buckets=[0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1],
)

# --- the click stream (refreshed by core/stream_metrics.py) -----------------------------------
CLICK_STREAM_LENGTH = Gauge("click_stream_length", "Entries in the click stream (XLEN)")
CLICK_STREAM_LAG = Gauge(
    "click_stream_lag", "Click events no worker has received yet (XLEN when Redis cannot tell)"
)
CLICK_STREAM_PENDING = Gauge(
    "click_stream_pending", "Click events delivered to a worker but not yet acknowledged"
)
