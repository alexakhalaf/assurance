"""OpenTelemetry: request, SQL and collection spans in one trace, trace ids in logs. Runs last in its own
module because the tracer provider is process global."""
import json
import logging

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from assurance import tracing  # noqa: E402
from assurance.logging_setup import JsonFormatter  # noqa: E402

exporter = InMemorySpanExporter()


def test_snapshot_trace(inventory, oper_h):
    from fastapi.testclient import TestClient

    from assurance.api.app import create_app

    assert tracing.setup("assurance-test", exporter=exporter)
    with TestClient(create_app()) as c:
        exporter.clear()
        r = c.post("/api/v1/snapshots", headers=oper_h, json={"labels": ["traced"], "mock_scenario": "t1"})
        assert r.status_code in (201, 202), r.text
    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    for n in ("collect device", "device commands", "genie parse", "finalize snapshot"):
        assert n in names, names
    assert any(s.name.startswith("POST /api/v1/snapshots") for s in spans)
    assert any(s.attributes.get("db.system") == "postgresql" for s in spans)
    # the queued jobs continue the request's trace
    assert len({s.context.trace_id for s in spans if s.name in ("collect device", "finalize snapshot")
                or s.name.startswith("POST /api/v1/snapshots")}) == 1
    dev = [s for s in spans if s.name == "collect device"]
    assert {s.attributes["assurance.device"] for s in dev} >= {"yyc-core-rtr01", "yyc-dc-nxs01"}


def test_log_lines_carry_trace_id():
    assert tracing.enabled()
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
    with tracing.span("unit"):
        doc = json.loads(JsonFormatter().format(rec))
    assert len(doc["trace_id"]) == 32
