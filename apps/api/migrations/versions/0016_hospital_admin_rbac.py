"""Separate facility staff custody. Legacy administration flags confer no authority here."""
from alembic import op

revision = "0016_hospital_admin_rbac"
down_revision = "0015_notification_outbox"
branch_labels = None
depends_on = None

ROLES = ("staff_provisioner", "staff_issuer", "staff_runtime", "staff_custody")


def upgrade():
    for role in ROLES:
        login = "NOLOGIN" if role == "staff_custody" else "LOGIN"
        op.execute(f"CREATE ROLE roghyar_{role} {login} NOINHERIT NOSUPERUSER NOBYPASSRLS")
        op.execute(f"GRANT USAGE ON SCHEMA roghyar TO roghyar_{role}")
    op.execute("""
        CREATE TABLE roghyar.staff_memberships (
            subject uuid NOT NULL,
            revision integer NOT NULL CHECK(revision>0),
            hospital uuid NOT NULL REFERENCES roghyar.hospitals(id),
            role text NOT NULL CHECK(role IN ('hospital_admin','appointment_manager','schedule_manager','provider_editor','auditor')),
            active boolean NOT NULL,
            expires_at timestamptz NOT NULL,
            approver uuid NOT NULL CHECK(approver<>subject),
            evidence_digest bytea NOT NULL CHECK(octet_length(evidence_digest)=32),
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(subject,revision)
        );
        CREATE TABLE roghyar.staff_sessions (
            id uuid PRIMARY KEY,
            subject uuid NOT NULL,
            revision integer NOT NULL,
            digest bytea NOT NULL UNIQUE CHECK(octet_length(digest)=32),
            issued_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL CHECK(expires_at>issued_at AND expires_at<=issued_at+interval '10 minutes'),
            revoked_at timestamptz,
            FOREIGN KEY(subject,revision) REFERENCES roghyar.staff_memberships(subject,revision)
        );
        CREATE TABLE roghyar.staff_audit (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            actor uuid NOT NULL,
            revision integer NOT NULL,
            hospital uuid NOT NULL REFERENCES roghyar.hospitals(id),
            action text NOT NULL CHECK(action IN ('doctor_proposal','doctor_correction','doctor_withdrawal',
                    'schedule_proposal','schedule_correction','schedule_withdrawal')),
            target uuid NOT NULL,
            key_digest bytea NOT NULL CHECK(octet_length(key_digest)=32),
            fingerprint bytea NOT NULL CHECK(octet_length(fingerprint)=32),
            submission jsonb NOT NULL CHECK(jsonb_typeof(submission)='object' AND octet_length(submission::text)<=8192),
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY(actor,revision) REFERENCES roghyar.staff_memberships(subject,revision),
            UNIQUE(actor,revision,key_digest)
        );
        CREATE INDEX ix_staff_audit_facility ON roghyar.staff_audit(hospital,recorded_at DESC,id);
    """)
    for table in ("staff_memberships", "staff_sessions", "staff_audit"):
        op.execute(f"ALTER TABLE roghyar.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE roghyar.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON roghyar.{table} FROM PUBLIC")
    op.execute("""
        GRANT SELECT,INSERT ON roghyar.staff_memberships TO roghyar_staff_provisioner;
        GRANT SELECT ON roghyar.staff_memberships TO roghyar_staff_issuer,roghyar_staff_custody;
        CREATE POLICY staff_provisioning ON roghyar.staff_memberships TO roghyar_staff_provisioner USING(true);
        CREATE POLICY staff_memberships_read ON roghyar.staff_memberships FOR SELECT
            TO roghyar_staff_issuer,roghyar_staff_custody USING(true);
        GRANT INSERT ON roghyar.staff_sessions TO roghyar_staff_issuer;
        CREATE POLICY staff_issuance ON roghyar.staff_sessions FOR INSERT TO roghyar_staff_issuer WITH CHECK(revoked_at IS NULL);
        GRANT SELECT ON roghyar.staff_sessions TO roghyar_staff_custody;
        GRANT UPDATE(revoked_at) ON roghyar.staff_sessions TO roghyar_staff_custody;
        CREATE POLICY staff_session_custody ON roghyar.staff_sessions TO roghyar_staff_custody USING(true);

        CREATE FUNCTION roghyar.staff_access() RETURNS TABLE(subject uuid,revision integer,hospital uuid,staff_role text)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE owner uuid;
        BEGIN
            SELECT s.subject INTO owner FROM roghyar.staff_sessions s
            WHERE s.digest=sha256(convert_to(current_setting('roghyar.staff_capability',true),'UTF8'));
            IF owner IS NULL THEN RETURN; END IF;
            PERFORM pg_advisory_xact_lock_shared(16100,hashtext(owner::text));
            RETURN QUERY SELECT m.subject,m.revision,m.hospital,m.role
              FROM roghyar.staff_sessions s JOIN roghyar.staff_memberships m
                ON m.subject=s.subject AND m.revision=s.revision
              WHERE s.subject=owner AND s.digest=sha256(convert_to(current_setting('roghyar.staff_capability',true),'UTF8'))
                AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
                AND m.active AND m.expires_at>clock_timestamp()
                AND NOT EXISTS(SELECT 1 FROM roghyar.staff_memberships n WHERE n.subject=owner AND n.revision>m.revision)
              FOR SHARE OF s;
        END $$;
        ALTER FUNCTION roghyar.staff_access() OWNER TO roghyar_staff_custody;
        REVOKE ALL ON FUNCTION roghyar.staff_access() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.staff_access() TO roghyar_staff_runtime;

        CREATE FUNCTION roghyar.staff_logout() RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
            UPDATE roghyar.staff_sessions SET revoked_at=clock_timestamp()
            WHERE digest=sha256(convert_to(current_setting('roghyar.staff_capability',true),'UTF8'))
              AND revoked_at IS NULL
        $$;
        ALTER FUNCTION roghyar.staff_logout() OWNER TO roghyar_staff_custody;
        REVOKE ALL ON FUNCTION roghyar.staff_logout() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.staff_logout() TO roghyar_staff_runtime;

        GRANT SELECT(id,full_name,specialty,hospital_id,published,verified) ON roghyar.doctors TO roghyar_staff_runtime;
        CREATE POLICY staff_doctors ON roghyar.doctors FOR SELECT TO roghyar_staff_runtime
            USING(hospital_id IN (SELECT a.hospital FROM roghyar.staff_access() a));
        GRANT SELECT(doctor_id,hospital_id,revision,hospital_revision,schedule,expires_at)
            ON roghyar.doctor_current TO roghyar_staff_runtime;
        CREATE POLICY staff_schedule ON roghyar.doctor_current FOR SELECT TO roghyar_staff_runtime
            USING(hospital_id IN (SELECT a.hospital FROM roghyar.staff_access() a));
        GRANT SELECT(id,doctor_id,hospital_id,slot_start,slot_end,status,status_changed_at)
            ON roghyar.appointments TO roghyar_staff_runtime;
        CREATE POLICY staff_appointments ON roghyar.appointments FOR SELECT TO roghyar_staff_runtime
            USING(hospital_id IN (SELECT a.hospital FROM roghyar.staff_access() a
                                  WHERE a.staff_role IN ('hospital_admin','appointment_manager','auditor')));
        GRANT SELECT ON roghyar.staff_audit TO roghyar_staff_runtime;
        GRANT INSERT(actor,revision,hospital,action,target,key_digest,fingerprint,submission)
            ON roghyar.staff_audit TO roghyar_staff_runtime;
        CREATE POLICY staff_audit_read ON roghyar.staff_audit FOR SELECT TO roghyar_staff_runtime
            USING(EXISTS(SELECT 1 FROM roghyar.staff_access() a WHERE a.hospital=staff_audit.hospital
                          AND (a.staff_role IN ('hospital_admin','auditor') OR a.subject=staff_audit.actor)));
        CREATE POLICY staff_audit_insert ON roghyar.staff_audit FOR INSERT TO roghyar_staff_runtime
            WITH CHECK(EXISTS(SELECT 1 FROM roghyar.staff_access() a
              WHERE a.subject=staff_audit.actor AND a.revision=staff_audit.revision AND a.hospital=staff_audit.hospital
                AND (a.staff_role='hospital_admin' OR (a.staff_role='provider_editor' AND action LIKE 'doctor_%')
                                                  OR (a.staff_role='schedule_manager' AND action LIKE 'schedule_%'))));
    """)


def downgrade():
    op.execute("SET LOCAL row_security=off")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM roghyar.staff_memberships) OR EXISTS(SELECT 1 FROM roghyar.staff_sessions)
                OR EXISTS(SELECT 1 FROM roghyar.staff_audit)
            THEN RAISE EXCEPTION 'Staff history requires approved archival'; END IF;
        END $$;
        DROP POLICY staff_doctors ON roghyar.doctors;
        DROP POLICY staff_appointments ON roghyar.appointments;
        DROP POLICY staff_schedule ON roghyar.doctor_current;
        DROP TABLE roghyar.staff_audit,roghyar.staff_sessions,roghyar.staff_memberships;
        DROP FUNCTION roghyar.staff_access(),roghyar.staff_logout();
    """)
    for role in ROLES:
        op.execute(f"DROP OWNED BY roghyar_{role}")
        op.execute(f"DROP ROLE roghyar_{role}")
