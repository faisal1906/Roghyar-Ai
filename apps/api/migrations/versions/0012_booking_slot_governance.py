"""Append-only provider confirmation custody. No appointment privileges."""
from alembic import op

revision = "0012_booking_slot_governance"
down_revision = "0011_otp_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE ROLE roghyar_slot_writer LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;
        GRANT USAGE ON SCHEMA roghyar TO roghyar_slot_writer;
        CREATE TABLE roghyar.slot_writer_scope (
            login name NOT NULL,
            hospital_id uuid NOT NULL REFERENCES roghyar.hospitals(id),
            PRIMARY KEY(login,hospital_id)
        );
        CREATE TABLE roghyar.slot_confirmations (
            doctor_id uuid NOT NULL,
            hospital_id uuid NOT NULL,
            day date NOT NULL,
            sequence integer NOT NULL CHECK(sequence>0),
            doctor_revision integer NOT NULL,
            hospital_revision integer NOT NULL CHECK(hospital_revision>0),
            proof text NOT NULL CHECK(octet_length(proof) BETWEEN 2 AND 8192),
            actor name NOT NULL DEFAULT session_user,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(doctor_id,hospital_id,day,sequence),
            FOREIGN KEY(doctor_id,doctor_revision) REFERENCES roghyar.doctor_revisions(doctor_id,revision)
        );
        ALTER TABLE roghyar.slot_writer_scope ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.slot_writer_scope FORCE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.slot_confirmations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.slot_confirmations FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON roghyar.slot_writer_scope,roghyar.slot_confirmations FROM PUBLIC;
        GRANT SELECT ON roghyar.slot_writer_scope,roghyar.slot_confirmations TO roghyar_slot_writer;
        GRANT SELECT(doctor_id,hospital_id,day,sequence,doctor_revision,hospital_revision,proof)
            ON roghyar.slot_confirmations TO roghyar_runtime;
        GRANT INSERT(doctor_id,hospital_id,day,sequence,doctor_revision,hospital_revision,proof)
            ON roghyar.slot_confirmations TO roghyar_slot_writer;
        CREATE POLICY slot_scope_read ON roghyar.slot_writer_scope FOR SELECT TO roghyar_slot_writer
            USING(login=session_user);
        CREATE POLICY slot_writer_read ON roghyar.slot_confirmations FOR SELECT TO roghyar_slot_writer
            USING(EXISTS(SELECT 1 FROM roghyar.slot_writer_scope s
                         WHERE s.login=session_user AND s.hospital_id=slot_confirmations.hospital_id));
        CREATE POLICY slot_writer_insert ON roghyar.slot_confirmations FOR INSERT TO roghyar_slot_writer
            WITH CHECK(actor=session_user AND EXISTS(SELECT 1 FROM roghyar.slot_writer_scope s
                       WHERE s.login=session_user AND s.hospital_id=slot_confirmations.hospital_id));
        CREATE POLICY slot_runtime_read ON roghyar.slot_confirmations FOR SELECT TO roghyar_runtime
            USING(EXISTS(SELECT 1 FROM roghyar.doctor_current c
                          WHERE c.doctor_id=slot_confirmations.doctor_id AND c.hospital_id=slot_confirmations.hospital_id));
    """)


def downgrade():
    op.execute("SET LOCAL row_security=off")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM roghyar.slot_confirmations) OR EXISTS(SELECT 1 FROM roghyar.slot_writer_scope)
            THEN RAISE EXCEPTION 'Schedule confirmation history requires approved archival'; END IF;
        END $$;
        DROP TABLE roghyar.slot_confirmations;
        DROP TABLE roghyar.slot_writer_scope;
        DROP OWNED BY roghyar_slot_writer;
        DROP ROLE roghyar_slot_writer;
    """)
