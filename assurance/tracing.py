"""Optional OpenTelemetry tracing. Off unless ASSURANCE_OTEL_ENDPOINT is set and the `otel` extra is installed.

Spans: every API request, every SQL statement, each device collection (collect, parse, store), and
snapshot finalization. Trace context follows a request into the jobs it queues, so one trace shows
an API call, the workers that collected each device, and the finalize step. Log lines carry trace_id.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager, nullcontext

from . import __version__
from .config import get_settings

log = logging.getLogger(__name__)
_enabled = False

try:
    from opentelemetry import propagate, trace
except ImportError:  # pragma: no cover - the extra is not installed
    trace = propagate = None


def enabled() -> bool:
    return _enabled


def setup(service: str, app=None, exporter=None) -> bool:
    """Configure the tracer provider once per process; instrument SQLAlchemy and (if given) the API app."""
    global _enabled
    s = get_settings()
    if not (s.otel_endpoint or exporter):
        return False
    if not _enabled:
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
            from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        except ImportError:
            log.warning("ASSURANCE_OTEL_ENDPOINT is set but OpenTelemetry is missing: pip install 'assurance[otel]'")
            return False
        resource = Resource.create({"service.name": service, "service.version": __version__,
                                    "deployment.environment": s.env, "service.namespace": s.otel_service_namespace})
        provider = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(s.otel_sample_ratio)))
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
                endpoint=s.otel_endpoint.rstrip("/") + "/v1/traces")))
        else:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        from .db import get_engine

        _trace_sql(get_engine())
        _enabled = True
        log.info("tracing to %s as %s", s.otel_endpoint or "a custom exporter", service)
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics,assets")
    return True


def _trace_sql(engine) -> None:
    """A client span per SQL statement. The statement text is recorded, never its parameters."""
    from opentelemetry.trace import SpanKind
    from sqlalchemy import event

    tracer = trace.get_tracer("assurance.sql")
    db_name = engine.url.database

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        op = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else "SQL"
        sp = tracer.start_span(f"{op} {db_name}", kind=SpanKind.CLIENT, attributes={
            "db.system": engine.dialect.name, "db.name": db_name or "", "db.operation": op,
            "db.statement": statement[:2000]})
        conn.info.setdefault("_otel_spans", []).append(sp)

    def _end(conn, exc=None):
        stack = conn.info.get("_otel_spans")
        if stack:
            sp = stack.pop()
            if exc is not None:
                sp.record_exception(exc)
                from opentelemetry.trace import Status, StatusCode

                sp.set_status(Status(StatusCode.ERROR))
            sp.end()

    event.listen(engine, "after_cursor_execute", lambda conn, *a: _end(conn))
    event.listen(engine, "handle_error", lambda ctx: _end(ctx.connection, ctx.original_exception)
                 if ctx.connection is not None else None)


def span(name: str, **attrs):
    """A child span when tracing is on, else a no-op context."""
    if not _enabled:
        return nullcontext()
    return trace.get_tracer("assurance").start_as_current_span(
        name, attributes={f"assurance.{k}": v for k, v in attrs.items() if v is not None})


def annotate(**attrs) -> None:
    if _enabled:
        cur = trace.get_current_span()
        for k, v in attrs.items():
            if v is not None:
                cur.set_attribute(f"assurance.{k}", v)


def error(exc: BaseException) -> None:
    """Mark the current span failed for an exception that is handled (not re-raised)."""
    if _enabled:
        from opentelemetry.trace import Status, StatusCode

        cur = trace.get_current_span()
        cur.record_exception(exc)
        cur.set_status(Status(StatusCode.ERROR, type(exc).__name__))


def carrier() -> dict | None:
    """The current trace context, to send with a queued job."""
    if not _enabled:
        return None
    c: dict = {}
    propagate.inject(c)
    return c or None


@contextmanager
def _attached(ctx: dict | None):
    if not (_enabled and ctx):
        yield
        return
    from opentelemetry import context

    token = context.attach(propagate.extract(ctx))
    try:
        yield
    finally:
        context.detach(token)


def run_traced(func, ctx: dict | None, *args, **kwargs):
    """Job wrapper: continue the caller's trace in the worker."""
    with _attached(ctx), span(f"job {getattr(func, '__name__', 'job')}"):
        return func(*args, **kwargs)


def current_trace_id() -> str | None:
    if not _enabled:
        return None
    sc = trace.get_current_span().get_span_context()
    return format(sc.trace_id, "032x") if sc.is_valid else None
