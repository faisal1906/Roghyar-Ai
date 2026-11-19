import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from roghyar.main import app, engine


@pytest.mark.skipif(os.getenv("ROGHYAR_TEST_POSTGIS") != "1", reason="Requires migrated PostGIS")
def test_migrated_postgis_readiness_and_utc():
    assert TestClient(app).get("/api/v1/health/ready").json() == {"status": "ok"}
    with engine.connect() as connection:
        assert connection.execute(text("SHOW timezone")).scalar_one() == "UTC"
        assert connection.execute(text("SELECT ST_SRID(ST_MakePoint(0, 0)::geography)")).scalar_one() == 4326
