import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from roghyar.main import app, engine

APP_COLUMNS = {
    "admin_users": "active auth_subject created_at email hospital_id id role",
    "appointments": "assessment_id booking_code consent_at created_at doctor_id hospital_id id patient_id reason_ciphertext slot_end slot_start status status_changed_at",
    "assessments": "age anonymized_at answers_ciphertext condition_code created_at duration_hours id language level more_than_one_year patient_id red_flags safety_details_ciphertext session_id severity specialty text_ciphertext",
    "audit_log": "action actor actor_db_role at entity entity_id id",
    "chat_messages": "confidence created_at id language patient_id relevance_category role session_id text_ciphertext",
    "doctor_schedules": "created_at doctor_id end_time id location_note slot_minutes start_time weekday",
    "doctors": "bio created_at experience_years fee full_name hospital_id id languages photo_url pmdc_reg_no published qualifications source specialty verified verified_by verified_on",
    "formulary": "active adult_dose child_dose_table condition_code contraindications created_at frequency home_steps id max_age max_daily medicine min_age review_status reviewed_at reviewed_by see_doctor_if version",
    "hospitals": "address aliases city created_at departments district id is_emergency_24_7 location name phone province published source verified verified_by verified_on",
    "otp_codes": "attempts code_hash consumed_at created_at expires_at id phone_lookup purpose",
    "patients": "age auth_subject consent_at created_at full_name_ciphertext gender guardian_name_ciphertext id phone_ciphertext phone_lookup phone_verified_at",
    "red_flag_rules": "active category created_at id level pattern review_status reviewed_at reviewed_by rule_code version",
}


def require_disposable_postgis():
    if os.getenv("ROGHYAR_TEST_POSTGIS") != "1":
        if os.getenv("GITHUB_ACTIONS") == "true":
            pytest.fail("The required CI job must enable live PostGIS verification")
        pytest.skip("Set ROGHYAR_TEST_POSTGIS=1 to test a live PostGIS database")
    assert engine.url.database == "roghyar_phase0_ci"
    assert engine.url.host in {"127.0.0.1", "::1", "localhost"}
    assert os.getenv("ROGHYAR_ENVIRONMENT") == "test"


def test_migrated_postgis_readiness_and_utc():
    require_disposable_postgis()
    client = TestClient(app)
    response = client.get("/api/v1/health/ready")
    state = os.getenv("ROGHYAR_POSTGIS_STATE", "head")
    assert state in {"base", "core", "head"}
    assert response.headers["cache-control"] == "no-store"
    with engine.begin() as connection:
        version = connection.execute(text(
            "SELECT extversion FROM pg_extension WHERE extname = 'postgis'"
        )).scalar_one()
        assert connection.execute(text("SHOW timezone")).scalar_one() == "UTC"
        revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if state != "head":
            assert revisions == ([] if state == "base" else ["0002_core_schema"])
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
            assert client.get("/api/v1/health/live").status_code == 200
            protected = connection.execute(text("""
                SELECT count(*) FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = 'roghyar' AND c.relrowsecurity
            """)).scalar_one()
            assert protected == 0
            assert connection.execute(text("""
                SELECT count(*) FROM pg_tables WHERE schemaname = 'roghyar'
            """)).scalar_one() == (0 if state == "base" else len(APP_COLUMNS))
            print(f"Downgrade verified: {state}, PostGIS {version}, readiness HTTP 503")
            return

        assert revisions == ["0003_rls"]
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        app_relations = connection.execute(text("""
            SELECT n.nspname, c.relname, a.attname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND n.nspname <> 'information_schema'
              AND left(n.nspname, 3) <> 'pg_'
              AND NOT EXISTS (
                  SELECT 1 FROM pg_depend d
                  WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                    AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
              )
            ORDER BY n.nspname, c.relname, a.attnum
        """)).all()
        expected = {("public", "alembic_version", "version_num")}
        expected.update(("roghyar", table, col) for table, cols in APP_COLUMNS.items() for col in cols.split())
        assert set(app_relations) == expected
        assert connection.execute(text("""
            SELECT srid FROM geography_columns
            WHERE f_table_schema = 'roghyar' AND f_table_name = 'hospitals'
              AND f_geography_column = 'location' AND type = 'Point'
        """)).scalar_one() == 4326
        assert connection.execute(text("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = 'roghyar' AND c.relrowsecurity AND c.relforcerowsecurity
        """)).scalar_one() == len(APP_COLUMNS)
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
        hospital_plan = connection.execute(text("""
            EXPLAIN SELECT id FROM roghyar.hospitals
            WHERE ST_DWithin(location, ST_MakePoint(0, 0)::geography, 1000)
        """)).scalars().all()
        assert any("ix_hospitals_location" in line for line in hospital_plan)
        print(f"Upgrade verified: revision {revisions[0]}, PostGIS {version}, readiness HTTP 200, geography SRID 4326 and GiST query")
