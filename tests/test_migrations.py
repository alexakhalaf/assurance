"""Alembic migrations must produce exactly the ORM schema."""
import os

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.skipif("postgresql" not in os.environ["ASSURANCE_DATABASE_URL"], reason="needs PostgreSQL")
def test_migrations_match_models():
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from assurance.cli.admin import _alembic_cfg
    from assurance.models import Base

    base = os.environ["ASSURANCE_DATABASE_URL"]
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS assurance_migrations"))
        c.execute(text("CREATE DATABASE assurance_migrations"))
    url = base.rsplit("/", 1)[0] + "/assurance_migrations"
    cfg = _alembic_cfg()
    cfg.set_main_option("sqlalchemy.url", url)
    try:
        command.upgrade(cfg, "head")
        eng = create_engine(url)
        with eng.connect() as c:
            diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
        eng.dispose()
        assert diff == []
        command.downgrade(cfg, "base")
    finally:
        with admin.connect() as c:
            c.execute(text("DROP DATABASE IF EXISTS assurance_migrations WITH (FORCE)"))
