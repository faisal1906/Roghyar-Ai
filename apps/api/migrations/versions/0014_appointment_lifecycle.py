"""Appointments-purpose authorization, lifecycle history and private mutations."""
from alembic import op

revision = "0014_appointment_lifecycle"
down_revision = "0013_atomic_reservations"
branch_labels = None
depends_on = None

SHARED = (("booking_doctor", "doctors"), ("booking_hospital", "hospitals"),
          ("booking_current", "doctor_current"), ("booking_current", "directory_current"),
          ("booking_confirmation", "slot_confirmations"))


def upgrade():
    op.execute("""
        CREATE ROLE roghyar_appointment_writer LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;
        GRANT USAGE ON SCHEMA roghyar TO roghyar_appointment_writer;
        ALTER TABLE roghyar.otp_sessions ADD management_digest bytea
            CHECK(management_digest IS NULL OR (purpose='appointments' AND octet_length(management_digest)=32));
        GRANT INSERT(management_digest) ON roghyar.otp_sessions TO roghyar_otp_writer;

        CREATE FUNCTION roghyar.appointment_identity() RETURNS TABLE(
            subject uuid, phone_lookup bytea, issued_at timestamptz, expires_at timestamptz, signing_key text
        ) LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
            SELECT s.subject,s.phone_lookup,s.issued_at,s.expires_at,s.signing_key
            FROM roghyar.otp_sessions s
            JOIN roghyar.otp_identities i ON i.subject=s.subject AND i.phone_lookup=s.phone_lookup
            JOIN roghyar.otp_challenges c ON c.id=s.verification
            WHERE s.id=nullif(current_setting('roghyar.management_session',true),'')::uuid
              AND s.management_digest=sha256(convert_to(current_setting('roghyar.management_proof',true),'UTF8'))
              AND s.purpose='appointments' AND s.revoked_at IS NULL AND i.revoked_at IS NULL
              AND c.state='consumed' AND s.issued_at<=clock_timestamp() AND s.expires_at>clock_timestamp()
            FOR SHARE OF s,i
        $$;
        ALTER FUNCTION roghyar.appointment_identity() OWNER TO roghyar_otp_writer;
        REVOKE ALL ON FUNCTION roghyar.appointment_identity() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.appointment_identity() TO roghyar_appointment_writer;

        CREATE INDEX ix_appointments_lifecycle_page ON roghyar.appointments(patient_id,slot_start DESC,id DESC);
        CREATE TABLE roghyar.appointment_lifecycle_events (
            appointment_id uuid PRIMARY KEY REFERENCES roghyar.appointments(id),
            successor_id uuid UNIQUE REFERENCES roghyar.appointments(id),
            subject uuid NOT NULL,
            key_digest bytea NOT NULL CHECK(octet_length(key_digest)=32),
            fingerprint bytea NOT NULL CHECK(octet_length(fingerprint)=32),
            action text NOT NULL CHECK(action IN ('cancel','reschedule')),
            prior_status text NOT NULL CHECK(prior_status IN ('pending','confirmed')),
            result jsonb NOT NULL CHECK(jsonb_typeof(result)='object' AND octet_length(result::text)<=4096),
            doctor_revision integer,
            hospital_revision integer,
            schedule_digest text CHECK(schedule_digest IS NULL OR schedule_digest ~ '^[a-f0-9]{64}$'),
            confirmation_sequence integer,
            evidence_deadline timestamptz,
            happened_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(subject,key_digest),
            CHECK(successor_id IS DISTINCT FROM appointment_id),
            CHECK((action='reschedule')=(successor_id IS NOT NULL)),
            CHECK(CASE WHEN action='reschedule' THEN
                  num_nonnulls(doctor_revision,hospital_revision,schedule_digest,confirmation_sequence,evidence_deadline)=5
                  AND doctor_revision>0 AND hospital_revision>0 AND confirmation_sequence>0
                  ELSE num_nonnulls(doctor_revision,hospital_revision,schedule_digest,confirmation_sequence,evidence_deadline)=0
                  END)
        );
        ALTER TABLE roghyar.appointment_lifecycle_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.appointment_lifecycle_events FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON roghyar.appointment_lifecycle_events FROM PUBLIC;
        GRANT SELECT ON roghyar.appointment_lifecycle_events TO roghyar_appointment_writer;
        GRANT INSERT(appointment_id,successor_id,subject,key_digest,fingerprint,action,prior_status,result,
                     doctor_revision,hospital_revision,schedule_digest,confirmation_sequence,evidence_deadline)
            ON roghyar.appointment_lifecycle_events TO roghyar_appointment_writer;

        GRANT SELECT(id,auth_subject,phone_lookup,phone_verified_at)
            ON roghyar.patients TO roghyar_appointment_writer;
        CREATE POLICY lifecycle_patient ON roghyar.patients FOR SELECT TO roghyar_appointment_writer
            USING(phone_verified_at IS NOT NULL AND EXISTS(SELECT 1 FROM roghyar.appointment_identity() i
                         WHERE i.subject=auth_subject AND i.phone_lookup=patients.phone_lookup));
        GRANT SELECT(id,booking_code,doctor_id,hospital_id,patient_id,slot_start,slot_end,status,
                     status_changed_at,created_at,consent_at) ON roghyar.appointments TO roghyar_appointment_writer;
        GRANT UPDATE(status,status_changed_at) ON roghyar.appointments TO roghyar_appointment_writer;
        GRANT INSERT(id,booking_code,doctor_id,hospital_id,patient_id,slot_start,slot_end,status,consent_at)
            ON roghyar.appointments TO roghyar_appointment_writer;
        CREATE POLICY lifecycle_read ON roghyar.appointments FOR SELECT TO roghyar_appointment_writer
            USING(patient_id IN (SELECT id FROM roghyar.patients));
        CREATE POLICY lifecycle_update ON roghyar.appointments FOR UPDATE TO roghyar_appointment_writer
            USING(patient_id IN (SELECT id FROM roghyar.patients) AND status IN ('pending','confirmed'))
            WITH CHECK(patient_id IN (SELECT id FROM roghyar.patients) AND status IN ('cancelled','rescheduled'));
        CREATE POLICY lifecycle_successor ON roghyar.appointments FOR INSERT TO roghyar_appointment_writer
            WITH CHECK(status='pending' AND assessment_id IS NULL AND reason_ciphertext IS NULL
                       AND patient_id IN (SELECT id FROM roghyar.patients)
                       AND EXISTS(SELECT 1 FROM roghyar.doctor_current c
                           JOIN roghyar.directory_current h ON h.hospital_id=c.hospital_id AND h.revision=c.hospital_revision
                           WHERE c.doctor_id=appointments.doctor_id AND c.hospital_id=appointments.hospital_id));
        CREATE POLICY lifecycle_events_read ON roghyar.appointment_lifecycle_events FOR SELECT TO roghyar_appointment_writer
            USING(subject IN (SELECT i.subject FROM roghyar.appointment_identity() i));
        CREATE POLICY lifecycle_event_insert ON roghyar.appointment_lifecycle_events FOR INSERT TO roghyar_appointment_writer
            WITH CHECK(subject IN (SELECT i.subject FROM roghyar.appointment_identity() i)
                       AND EXISTS(SELECT 1 FROM roghyar.appointments a
                         WHERE a.id=appointment_id AND (
                          (action='cancel' AND a.status='cancelled' AND successor_id IS NULL) OR
                          (action='reschedule' AND a.status='rescheduled' AND EXISTS(
                             SELECT 1 FROM roghyar.appointments b WHERE b.id=successor_id AND b.status='pending'
                               AND b.patient_id=a.patient_id AND b.doctor_id=a.doctor_id AND b.hospital_id=a.hospital_id
                               AND a.created_at<b.created_at)))));

        GRANT SELECT(id,hospital_id,verified,published) ON roghyar.doctors TO roghyar_appointment_writer;
        GRANT SELECT(id,verified,published) ON roghyar.hospitals TO roghyar_appointment_writer;
        GRANT SELECT ON roghyar.doctor_current,roghyar.directory_current TO roghyar_appointment_writer;
        GRANT SELECT(doctor_id,hospital_id,day,sequence,doctor_revision,hospital_revision,proof)
            ON roghyar.slot_confirmations TO roghyar_appointment_writer;
        GRANT EXECUTE ON FUNCTION roghyar.doctor_slot_occupied(uuid,uuid,timestamptz,timestamptz),
            roghyar.booking_day_lock(uuid,uuid,date) TO roghyar_appointment_writer;

        CREATE FUNCTION roghyar.guard_lifecycle_commit() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF TG_OP='INSERT' THEN
                IF NOT EXISTS (SELECT 1 FROM roghyar.appointment_lifecycle_events e WHERE e.successor_id=NEW.id)
                THEN RAISE EXCEPTION 'Lifecycle successor requires history' USING ERRCODE='23514'; END IF;
            ELSE
                IF NOT EXISTS (SELECT 1 FROM roghyar.appointment_lifecycle_events e WHERE e.appointment_id=NEW.id
                    AND e.prior_status=OLD.status AND
                    NEW.status=CASE e.action WHEN 'cancel' THEN 'cancelled' ELSE 'rescheduled' END)
                THEN RAISE EXCEPTION 'Lifecycle transition requires history' USING ERRCODE='23514'; END IF;
            END IF;
            RETURN NULL;
        END $$;
        CREATE CONSTRAINT TRIGGER appointment_lifecycle_commit AFTER INSERT OR UPDATE
            ON roghyar.appointments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            WHEN (current_user='roghyar_appointment_writer') EXECUTE FUNCTION roghyar.guard_lifecycle_commit();
    """)
    for policy, table in SHARED:
        op.execute(f"ALTER POLICY {policy} ON roghyar.{table} TO roghyar_booking_writer,roghyar_appointment_writer")


def downgrade():
    op.execute("SET LOCAL row_security=off")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM roghyar.appointment_lifecycle_events)
            THEN RAISE EXCEPTION 'Appointment lifecycle history requires approved archival'; END IF;
        END $$;
        DROP TRIGGER appointment_lifecycle_commit ON roghyar.appointments;
        DROP FUNCTION roghyar.guard_lifecycle_commit();
        DROP TABLE roghyar.appointment_lifecycle_events;
        DROP INDEX roghyar.ix_appointments_lifecycle_page;
        DROP POLICY lifecycle_patient ON roghyar.patients;
        DROP POLICY lifecycle_read ON roghyar.appointments;
        DROP POLICY lifecycle_update ON roghyar.appointments;
        DROP POLICY lifecycle_successor ON roghyar.appointments;
        DROP FUNCTION roghyar.appointment_identity();
        ALTER TABLE roghyar.otp_sessions DROP COLUMN management_digest;
    """)
    for policy, table in SHARED:
        op.execute(f"ALTER POLICY {policy} ON roghyar.{table} TO roghyar_booking_writer")
    op.execute("DROP OWNED BY roghyar_appointment_writer")
    op.execute("DROP ROLE roghyar_appointment_writer")
