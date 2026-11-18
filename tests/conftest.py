import base64
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

os.environ.update({
    "ASSURANCE_ENV": "test",
    "ASSURANCE_DATABASE_URL": os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5433/assurance_test"),
    "ASSURANCE_OBJECT_STORE": "filesystem",
    "ASSURANCE_MASTER_KEY": base64.b64encode(b"k" * 32).decode(),
    "ASSURANCE_COLLECTOR": "mock",
    "ASSURANCE_MOCK_ROOT": str(ROOT / "mock_data"),
    "ASSURANCE_JOB_BACKEND": "sync",
    "ASSURANCE_LOG_JSON": "false",
    "ASSURANCE_COLLECT_JITTER_SECONDS": "0",
    "ASSURANCE_WEBHOOK_SECRET": "test-webhook-secret",
    "ASSURANCE_UI_DIST": str(ROOT / "tests" / "no-ui"),
})

from sqlalchemy import text  # noqa: E402

from assurance.config import get_settings  # noqa: E402
from assurance.db import get_engine, session_scope  # noqa: E402
from assurance.models import Base, Role, User  # noqa: E402
from assurance.security.passwords import hash_password  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session", autouse=True)
def _schema(tmp_path_factory):
    os.environ["ASSURANCE_OBJECT_STORE_PATH"] = str(tmp_path_factory.mktemp("objects"))
    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.drop_all(engine)
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS alembic_version"))
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean():
    from assurance.services import diff

    diff._cache.clear()
    with get_engine().begin() as c:
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    with session_scope() as db:
        for name, role in (("admin", Role.admin), ("oper", Role.operator), ("view", Role.viewer)):
            db.add(User(username=name, password_hash=hash_password(PASSWORD), role=role))
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from assurance.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def login(client, user="admin"):
    r = client.post("/api/v1/auth/login", json={"username": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def admin_h(client):
    return login(client, "admin")


@pytest.fixture
def oper_h(client):
    return login(client, "oper")


@pytest.fixture
def view_h(client):
    return login(client, "view")


INVENTORY_CSV = """name,host,platform,site,role,tags
yyc-core-rtr01,10.10.0.1,iosxe,YYC-DC1,core-router,core
yyc-dc-nxs01,10.10.0.11,nxos,YYC-DC1,dc-spine,dc;spine
"""


@pytest.fixture
def inventory(client, admin_h):
    r = client.post("/api/v1/devices/import", params={"dry_run": "false"}, headers=admin_h,
                    files={"file": ("d.csv", INVENTORY_CSV, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def two_snapshots(client, inventory, oper_h):
    ids = []
    for scen in ("t1", "t2"):
        r = client.post("/api/v1/snapshots", json={"mock_scenario": scen, "labels": [f"test:{scen}"]},
                        headers=oper_h)
        assert r.status_code == 202, r.text
        ids.append(r.json()["id"])
    return ids
