import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from roghyar.main import app, engine


def test_migrated_postgis_readiness_and_utc():
    if os.getenv("ROGHYAR_TEST_POSTGIS") != "1":
        if os.getenv("GITHUB_ACTIONS") == "true":
            pytest.fail("The required CI job must enable live PostGIS verification")
        pytest.skip("Set ROGHYAR_TEST_POSTGIS=1 to test a live PostGIS database")

    client = TestClient(app)
    response = client.get("/api/v1/health/ready")
    state = os.getenv("ROGHYAR_POSTGIS_STATE", "head")
    assert state in {"base", "head"}
    assert response.headers["cache-control"] == "no-store"
    with engine.begin() as connection:
        version = connection.execute(text(
            "SELECT extversion FROM pg_extension WHERE extname = 'postgis'"
        )).scalar_one()
        assert connection.execute(text("SHOW timezone")).scalar_one() == "UTC"
        revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if state == "base":
            assert revisions == []
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
            assert client.get("/api/v1/health/live").status_code == 200
            print(f"Downgrade verified: no Alembic revisions, PostGIS {version}, readiness HTTP 503")
            return

        assert revisions == ["0001_postgis"]
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        connection.execute(text("""
            CREATE TEMP TABLE postgis_probe (location geography(Point, 4326) NOT NULL)
            ON COMMIT DROP
        """))
        connection.execute(text("CREATE INDEX postgis_probe_location ON postgis_probe USING gist (location)"))
        connection.execute(text("""
            INSERT INTO postgis_probe (location)
            VALUES (ST_MakePoint(0, 0)::geography), (ST_MakePoint(1, 0)::geography)
        """))
        query = """
            SELECT ST_SRID(location) FROM postgis_probe
            WHERE ST_DWithin(location, ST_MakePoint(0, 0)::geography, 1000)
        """
        assert connection.execute(text(query)).scalars().all() == [4326]
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = connection.execute(text("EXPLAIN " + query)).scalars().all()
        assert any("postgis_probe_location" in line for line in plan)
        print(f"Upgrade verified: revision {revisions[0]}, PostGIS {version}, readiness HTTP 200, geography SRID 4326 and GiST query")
