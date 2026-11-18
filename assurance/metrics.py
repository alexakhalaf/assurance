"""Prometheus metrics. Exposed by the API at /metrics; workers push nothing (scrape the API +
use RQ's own exporter), so collection metrics are recorded where the work happens and also
summarized in the database for the UI."""
from prometheus_client import Counter, Gauge, Histogram

SNAPSHOTS = Counter("assurance_snapshots_total", "Snapshots finished", ["status", "trigger"])
COLLECTIONS = Counter("assurance_device_collections_total", "Device collections", ["status", "platform"])
COLLECTION_SECONDS = Histogram(
    "assurance_device_collection_seconds", "Time to collect and parse one device", ["platform"],
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
)
PARSER_WARNINGS = Counter("assurance_parser_warnings_total", "Genie parser failures", ["platform", "command"])
HTTP_REQUESTS = Counter("assurance_http_requests_total", "API requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("assurance_http_request_seconds", "API latency", ["method", "route"])
DEVICES = Gauge("assurance_devices", "Devices in inventory", ["status"])
