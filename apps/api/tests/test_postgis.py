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
    "formulary": "active adult_dose child_dose_table condition_code contraindications created_at frequency home_steps id max_age max_daily medicine min_age review_status reviewed_at reviewed_by reviewed_payload see_doctor_if version",
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
    assert state in {"base", "core", "rls", "assessments", "red_flags", "formulary", "publication", "autocomplete", "directory", "doctor", "otp", "slots", "reservations", "lifecycle", "notifications", "head"}
    assert response.headers["cache-control"] == "no-store"
    with engine.begin() as connection:
        version = connection.execute(text(
            "SELECT extversion FROM pg_extension WHERE extname = 'postgis'"
        )).scalar_one()
        assert connection.execute(text("SHOW timezone")).scalar_one() == "UTC"
        revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if state == "notifications":
            assert revisions == ["0015_notification_outbox"]
            assert response.status_code == 503
            assert connection.scalar(text("SELECT to_regclass('roghyar.staff_memberships')")) is None
            assert connection.scalar(text("SELECT to_regclass('roghyar.notification_outbox')")) is not None
            print("BK06 previous revision restored")
            return
        if state == "lifecycle":
            assert revisions == ["0014_appointment_lifecycle"]
            assert response.status_code == 503
            assert connection.scalar(text("SELECT to_regclass('roghyar.notification_outbox')")) is None
            assert connection.scalar(text("SELECT to_regclass('roghyar.appointment_lifecycle_events')")) is not None
            print("BK05 previous revision restored")
            return
        if state == "reservations":
            assert revisions == ["0013_atomic_reservations"]
            assert response.status_code == 503
            assert connection.scalar(text("SELECT to_regclass('roghyar.appointment_lifecycle_events')")) is None
            assert connection.scalar(text("SELECT to_regclass('roghyar.booking_requests')")) is not None
            print("BK04 previous revision restored")
            return
        if state == "slots":
            assert revisions == ["0012_booking_slot_governance"]
            assert response.status_code == 503
            assert connection.scalar(text("SELECT to_regclass('roghyar.booking_requests')")) is None
            assert connection.scalar(text("SELECT to_regclass('roghyar.slot_confirmations')")) is not None
            print("BK03 previous revision restored")
            return
        if state == "otp":
            assert revisions == ["0011_otp_sessions"]
            assert response.status_code == 503
            assert connection.scalar(text("SELECT to_regclass('roghyar.slot_confirmations')")) is None
            assert connection.scalar(text("SELECT to_regclass('roghyar.otp_challenges')")) is not None
            print("BK02 previous revision restored")
            return
        if state == "doctor":
            assert revisions == ["0010_doctor_governance"]
            assert response.status_code == 503
            assert connection.scalar(text("SELECT to_regclass('roghyar.otp_challenges')")) is None
            assert connection.scalar(text("SELECT to_regclass('roghyar.otp_codes')")) is not None
            print("BK01 previous revision restored")
            return
        if state == "directory":
            assert revisions == ["0009_directory_governance"]
            assert response.status_code == 503
            assert connection.scalar(text("SELECT to_regclass('roghyar.doctor_current')")) is None
            assert connection.scalar(text("SELECT to_regclass('roghyar.directory_current')")) is not None
            print("LOC05 previous revision restored")
            return
        if state == "autocomplete":
            assert revisions == ["0008_hospital_autocomplete"]
            assert response.status_code == 503
            assert connection.scalar(text("SELECT to_regclass('roghyar.directory_current')")) is None
            assert connection.scalar(text("SELECT to_regclass('roghyar.ix_hospitals_search_trgm')")) is not None
            print("LOC04 previous revision restored")
            return
        if state == "publication":
            assert revisions == ["0007_home_care_publication"]
            assert response.status_code == 503
            assert connection.scalar(text("SELECT to_regclass('roghyar.ix_hospitals_search_trgm')")) is None
            assert connection.scalar(text("SELECT to_regclass('roghyar.home_care_revisions')")) is not None
            print("LOC03 downgrade previous revision verified")
            return
        if state != "head":
            assert revisions == {
                "base": [], "core": ["0002_core_schema"], "rls": ["0003_rls"],
                "assessments": ["0004_assessments"],
                "red_flags": ["0005_red_flag_attestation"], "formulary": ["0006_formulary_attestation"],
            }[state]
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
            assert client.get("/api/v1/health/live").status_code == 200
            protected = connection.execute(text("""
                SELECT count(*) FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = 'roghyar' AND c.relrowsecurity
            """)).scalar_one()
            assert protected == (len(APP_COLUMNS) if state in {"rls", "assessments", "red_flags", "formulary"} else 0)
            assert connection.execute(text("""
                SELECT count(*) FROM pg_tables WHERE schemaname = 'roghyar'
            """)).scalar_one() == (0 if state == "base" else len(APP_COLUMNS))
            if state == "core":
                assert connection.execute(text("""
                    SELECT has_schema_privilege('roghyar_runtime','roghyar','USAGE')
                        OR has_table_privilege('roghyar_runtime','public.alembic_version','SELECT')
                """)).scalar_one() is False
            if state == "rls":
                assert connection.execute(text("""
                    SELECT has_schema_privilege('roghyar_assessment_writer','roghyar','USAGE')
                """)).scalar_one() is False
            print(f"Downgrade verified: {state}, PostGIS {version}, readiness HTTP 503")
            return

        assert revisions == ["0016_hospital_admin_rbac"]
        print("Upgrade verified: revision 0015_notification_outbox compatibility")
        print("Upgrade verified: revision 0014_appointment_lifecycle compatibility")
        print("Upgrade verified: revision 0013_atomic_reservations compatibility")
        print("Upgrade verified: revision 0012_booking_slot_governance compatibility")
        print("Upgrade verified: revision 0011_otp_sessions compatibility")
        print("Upgrade verified: revision 0010_doctor_governance compatibility")
        # Existing governance acceptance confirms its tables also survive later revisions.
        print("Upgrade verified: revision 0009_directory_governance compatibility")
        columns = APP_COLUMNS | {
            "slot_writer_scope": "login hospital_id",
            "slot_confirmations": "doctor_id hospital_id day sequence doctor_revision hospital_revision proof actor recorded_at",
            "staff_clinic_approvals": "id actor revision hospital appointment expected_change slot_start slot_end action expires_at basis proof_digest proof_day approver",
            "staff_clinic_events": "id decision appointment_id actor revision hospital key_digest prior_status result_status happened_at",
            "staff_memberships": "subject revision hospital role active expires_at approver evidence_digest recorded_at",
            "staff_sessions": "id subject revision digest issued_at expires_at revoked_at",
            "staff_audit": "id actor revision hospital action target key_digest fingerprint submission recorded_at",
            "notification_consent": "sequence id subject channel locale policy reminders enabled recorded_at",
            "notification_outbox": "id appointment_id event_id patient_id consent_id kind channel locale version slot_start run_at expires_at state attempts lease worker lease_until provider message_id failure invalidated_at clinic_proof created_at",
            "notification_receipts": "provider event_id job_id message_id status occurred_at",
            "appointment_lifecycle_events": "appointment_id successor_id subject key_digest fingerprint action prior_status result doctor_revision hospital_revision schedule_digest confirmation_sequence evidence_deadline happened_at",
            "booking_requests": "subject key_digest fingerprint appointment_id result created_at",
            "booking_events": "appointment_id subject key_digest doctor_id hospital_id doctor_revision hospital_revision schedule_digest confirmation_sequence slot_start slot_end evidence_deadline event happened_at",
            "home_care_revisions": "content_id version condition applicability digest artifact translations state generation author",
            "otp_challenges": "id phone_lookup purpose verifier state attempts created_at expires_at",
            "otp_identities": "subject phone_lookup created_at revoked_at",
            "otp_sessions": "id subject phone_lookup verification purpose issued_at expires_at signing_key revoked_at management_digest",
            "doctor_operator_scope": "login hospital_id kind",
            "doctor_revisions": "doctor_id revision hospital_id hospital_revision registry registration digest body submitter submitted_at",
            "doctor_reviews": "doctor_id revision hospital_id digest kind credential reviewer proof expires_at",
            "doctor_current": "doctor_id revision hospital_id hospital_revision taxonomy_version taxonomy_digest specialties schedule expires_at reviewed_at",
            "doctor_events": "id doctor_id revision hospital_id action actor occurred_at transaction_id",
            "doctor_revocations": "hospital_id kind subject actor occurred_at",
            "directory_revisions": "hospital_id revision source_org source_record source_version batch_id body evidence_digest digest submitter created_at",
            "directory_reviews": "hospital_id revision digest credential reviewer approval expires_at",
            "directory_current": "hospital_id revision expires_at emergency_known",
            "directory_events": "id hospital_id revision action actor occurred_at transaction_id",
            "directory_revocations": "kind subject actor occurred_at",
            "home_care_events": "id content_id version digest previous_state state actor actor_role at transaction_id",
        }
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
        expected.update(("roghyar", table, col) for table, cols in columns.items() for col in cols.split())
        assert set(app_relations) == expected
        assert connection.execute(text("""
            SELECT srid FROM geography_columns
            WHERE f_table_schema = 'roghyar' AND f_table_name = 'hospitals'
              AND f_geography_column = 'location' AND type = 'Point'
        """)).scalar_one() == 4326
        assert connection.execute(text("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = 'roghyar' AND c.relrowsecurity AND c.relforcerowsecurity
        """)).scalar_one() == len(columns)
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
