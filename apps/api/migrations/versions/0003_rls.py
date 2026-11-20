"""Install least-privilege grants and row policies."""

from alembic import op

revision = "0003_rls"
down_revision = "0002_core_schema"
branch_labels = None
depends_on = None

RUNTIME = "roghyar_runtime"
MAINTENANCE = "roghyar_maintenance"
PUBLISHER = "roghyar_publisher"
TABLES = (
    "hospitals", "doctors", "doctor_schedules", "patients", "assessments", "appointments",
    "chat_messages", "formulary", "red_flag_rules", "otp_codes", "admin_users", "audit_log",
)
CLINICAL = ("formulary", "red_flag_rules")
DIRECTORY = {
    "hospitals": "id,name,aliases,address,city,district,province,location,phone,is_emergency_24_7,"
                 "departments,verified,published,verified_on",
    "doctors": "id,hospital_id,full_name,specialty,qualifications,experience_years,bio,pmdc_reg_no,"
               "fee,languages,photo_url,verified,published,verified_on",
    "doctor_schedules": "id,doctor_id,weekday,start_time,end_time,slot_minutes,location_note",
}
KIND = "current_setting('roghyar.kind', true)"
SUBJECT = "nullif(current_setting('roghyar.subject', true), '')::uuid"
ADMIN = f"{KIND} = 'hospital_admin'"
PATIENT = f"{KIND} = 'patient'"
ANONYMOUS = f"{KIND} = 'anonymous'"
HOSPITALS = f"SELECT hospital_id FROM roghyar.admin_users WHERE active AND auth_subject = {SUBJECT}"
PATIENTS = f"SELECT id FROM roghyar.patients WHERE auth_subject = {SUBJECT} AND phone_verified_at IS NOT NULL"


def policy(table, role, predicate):
    op.execute(f"CREATE POLICY read_{role} ON roghyar.{table} FOR SELECT TO {role} USING ({predicate})")


def upgrade():
    op.execute("""
        DO $browser$
        DECLARE browser_role text;
        BEGIN
          FOR browser_role IN
            SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role')
          LOOP
            EXECUTE format('REVOKE ALL ON SCHEMA roghyar FROM %I', browser_role);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA roghyar FROM %I', browser_role);
          END LOOP;
        END $browser$
    """)
    # Provision these reserved, cluster-wide roles through the migration owner.
    # Existing roles must already satisfy the same security requirements.
    for role, login in ((RUNTIME, "LOGIN"), (MAINTENANCE, "NOLOGIN"), (PUBLISHER, "NOLOGIN")):
        expected_login = "true" if login == "LOGIN" else "false"
        op.execute(f"""
            DO $role$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} {login} NOINHERIT NOBYPASSRLS;
              END IF;
              IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = '{role}'
                AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb
                  OR rolreplication OR rolinherit OR rolcanlogin <> {expected_login})
              ) OR EXISTS (
                SELECT 1 FROM pg_auth_members WHERE member = '{role}'::regrole
              ) THEN
                RAISE EXCEPTION 'Unexpected application role attributes or memberships: {role}';
              END IF;
            END $role$
        """)
        op.execute(f"GRANT USAGE ON SCHEMA roghyar TO {role}")
    op.execute(f"GRANT SELECT ON public.alembic_version TO {RUNTIME}")

    for table in TABLES:
        op.execute(f"ALTER TABLE roghyar.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE roghyar.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON roghyar.{table} FROM PUBLIC")
        if table not in CLINICAL and table != "audit_log":
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON roghyar.{table} TO {MAINTENANCE}")
            op.execute(f"CREATE POLICY maintain ON roghyar.{table} TO {MAINTENANCE} "
                       "USING (true) WITH CHECK (true)")
    op.execute(f"GRANT SELECT ON roghyar.audit_log TO {MAINTENANCE}")
    policy("audit_log", MAINTENANCE, "true")

    for table, columns in DIRECTORY.items():
        op.execute(f"GRANT SELECT ({columns}) ON roghyar.{table} TO {RUNTIME}")
    # An administrator's facility memberships are resolved in the database.
    op.execute(f"GRANT SELECT (auth_subject,hospital_id,active) ON roghyar.admin_users TO {RUNTIME}")
    policy("admin_users", RUNTIME, f"{ADMIN} AND active AND auth_subject = {SUBJECT}")
    policy("hospitals", RUNTIME, f"(verified AND published) OR ({ADMIN} AND id IN ({HOSPITALS}))")
    policy("doctors", RUNTIME, "(verified AND published AND hospital_id IN "
           "(SELECT id FROM roghyar.hospitals WHERE verified AND published)) "
           f"OR ({ADMIN} AND hospital_id IN ({HOSPITALS}))")
    policy("doctor_schedules", RUNTIME, "doctor_id IN (SELECT id FROM roghyar.doctors)")
    for table in ("patients", "appointments", "assessments", "chat_messages", *CLINICAL):
        op.execute(f"GRANT SELECT ON roghyar.{table} TO {RUNTIME}")
    policy("patients", RUNTIME, f"{PATIENT} AND auth_subject = {SUBJECT} AND phone_verified_at IS NOT NULL")
    policy("appointments", RUNTIME, f"({PATIENT} AND patient_id IN ({PATIENTS})) "
           f"OR ({ADMIN} AND hospital_id IN ({HOSPITALS}))")
    for table in ("assessments", "chat_messages"):
        policy(table, RUNTIME, f"({PATIENT} AND patient_id IN ({PATIENTS})) OR "
               f"({ANONYMOUS} AND patient_id IS NULL AND session_id = {SUBJECT})")

    op.execute("""
        CREATE FUNCTION roghyar.guard_clinical_history() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, roghyar AS $guard$
        DECLARE event_action text;
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF OLD.review_status <> 'draft'
               AND (to_jsonb(OLD) - 'active') <> (to_jsonb(NEW) - 'active') THEN
              RAISE EXCEPTION 'Reviewed versions are immutable; create a new version';
            END IF;
          END IF;
          IF NEW.review_status <> 'draft' OR NEW.active THEN
            event_action := CASE WHEN NEW.active THEN 'published' ELSE NEW.review_status END;
            IF TG_OP = 'UPDATE' THEN
              IF OLD.active AND NOT NEW.active THEN
                event_action := 'deactivated';
              END IF;
            END IF;
            INSERT INTO roghyar.audit_log(actor, actor_db_role, action, entity, entity_id)
              VALUES (NEW.reviewed_by, session_user, event_action, TG_TABLE_NAME, NEW.id);
          END IF;
          RETURN NEW;
        END $guard$
    """)
    op.execute("REVOKE ALL ON FUNCTION roghyar.guard_clinical_history() FROM PUBLIC")
    # The definer owns the audit table, whose FORCE policy also covers that owner.
    op.execute("""
        DO $owner$
        BEGIN
          EXECUTE format('CREATE POLICY append_clinical_audit ON roghyar.audit_log FOR INSERT TO %I WITH CHECK (true)',
                         current_user);
        END $owner$
    """)
    for table in CLINICAL:
        policy(table, RUNTIME, "active AND review_status = 'approved'")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON roghyar.{table} TO {PUBLISHER}")
        op.execute(f"CREATE POLICY publish ON roghyar.{table} TO {PUBLISHER} USING (true) WITH CHECK (true)")
        op.execute(f"CREATE TRIGGER guard_clinical_history BEFORE INSERT OR UPDATE ON roghyar.{table} "
                   "FOR EACH ROW EXECUTE FUNCTION roghyar.guard_clinical_history()")


def downgrade():
    # Reserved cluster roles survive rollback so credentials are not destroyed.
    for table in CLINICAL:
        op.execute(f"DROP TRIGGER guard_clinical_history ON roghyar.{table}")
        op.execute(f"DROP POLICY publish ON roghyar.{table}")
    op.execute("DROP POLICY append_clinical_audit ON roghyar.audit_log")
    op.execute("DROP FUNCTION roghyar.guard_clinical_history()")
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS read_{RUNTIME} ON roghyar.{table}")
        op.execute(f"DROP POLICY IF EXISTS read_{MAINTENANCE} ON roghyar.{table}")
        op.execute(f"DROP POLICY IF EXISTS maintain ON roghyar.{table}")
        op.execute(f"ALTER TABLE roghyar.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE roghyar.{table} DISABLE ROW LEVEL SECURITY")
        for role in (RUNTIME, MAINTENANCE, PUBLISHER):
            op.execute(f"REVOKE ALL ON roghyar.{table} FROM {role}")
    for table, columns in DIRECTORY.items():
        op.execute(f"REVOKE SELECT ({columns}) ON roghyar.{table} FROM {RUNTIME}")
    op.execute(f"REVOKE SELECT (auth_subject,hospital_id,active) ON roghyar.admin_users FROM {RUNTIME}")
    for role in (RUNTIME, MAINTENANCE, PUBLISHER):
        op.execute(f"REVOKE USAGE ON SCHEMA roghyar FROM {role}")
    op.execute(f"REVOKE SELECT ON public.alembic_version FROM {RUNTIME}")

