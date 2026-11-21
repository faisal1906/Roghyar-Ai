"""Restricted atomic reservation custody and active interval exclusion."""
from alembic import op

revision = "0013_atomic_reservations"
down_revision = "0012_booking_slot_governance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL row_security=off")
    op.execute("""
        CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;
        LOCK TABLE roghyar.appointments IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM roghyar.appointments a JOIN roghyar.appointments b
                ON a.doctor_id=b.doctor_id AND a.id<b.id AND a.slot_start<b.slot_end AND b.slot_start<a.slot_end
                WHERE a.status IN ('pending','confirmed') AND b.status IN ('pending','confirmed'))
            THEN RAISE EXCEPTION 'Existing active reservations overlap; resolve with the booking data owner'; END IF;
        END $$;
        ALTER TABLE roghyar.appointments ADD CONSTRAINT appointments_active_overlap
            EXCLUDE USING gist (doctor_id public.gist_uuid_ops WITH =, tstzrange(slot_start,slot_end,'[)') WITH &&)
            WHERE (status IN ('pending','confirmed'));
        CREATE ROLE roghyar_booking_writer LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;
        GRANT USAGE ON SCHEMA roghyar TO roghyar_booking_writer;
        CREATE TABLE roghyar.booking_requests (
            subject uuid NOT NULL,
            key_digest bytea NOT NULL CHECK(octet_length(key_digest)=32),
            fingerprint bytea NOT NULL CHECK(octet_length(fingerprint)=32),
            appointment_id uuid NOT NULL UNIQUE REFERENCES roghyar.appointments(id),
            result jsonb NOT NULL CHECK(jsonb_typeof(result)='object' AND octet_length(result::text)<=4096),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(subject,key_digest)
        );
        CREATE TABLE roghyar.booking_events (
            appointment_id uuid PRIMARY KEY REFERENCES roghyar.appointments(id),
            subject uuid NOT NULL,
            key_digest bytea NOT NULL,
            doctor_id uuid NOT NULL,
            hospital_id uuid NOT NULL,
            doctor_revision integer NOT NULL CHECK(doctor_revision>0),
            hospital_revision integer NOT NULL CHECK(hospital_revision>0),
            schedule_digest text NOT NULL CHECK(schedule_digest ~ '^[a-f0-9]{64}$'),
            confirmation_sequence integer NOT NULL CHECK(confirmation_sequence>0),
            slot_start timestamptz NOT NULL,
            slot_end timestamptz NOT NULL,
            evidence_deadline timestamptz NOT NULL,
            event text NOT NULL DEFAULT 'reservation_created' CHECK(event='reservation_created'),
            happened_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY(doctor_id,hospital_id) REFERENCES roghyar.doctors(id,hospital_id),
            FOREIGN KEY(subject,key_digest) REFERENCES roghyar.booking_requests(subject,key_digest),
            CHECK(slot_end>slot_start)
        );
        ALTER TABLE roghyar.booking_requests ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.booking_requests FORCE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.booking_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.booking_events FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON roghyar.booking_requests,roghyar.booking_events FROM PUBLIC;

        CREATE FUNCTION roghyar.booking_identity() RETURNS TABLE(
            subject uuid, phone_lookup bytea, verified_at timestamptz, issued_at timestamptz,
            expires_at timestamptz, signing_key text
        ) LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
            SELECT s.subject,s.phone_lookup,i.created_at,s.issued_at,s.expires_at,s.signing_key
            FROM roghyar.otp_sessions s
            JOIN roghyar.otp_identities i ON i.subject=s.subject AND i.phone_lookup=s.phone_lookup
            JOIN roghyar.otp_challenges c ON c.id=s.verification
            WHERE s.id=nullif(current_setting('roghyar.booking_session',true),'')::uuid
              AND s.purpose='booking' AND s.revoked_at IS NULL AND i.revoked_at IS NULL
              AND c.state='consumed' AND s.issued_at<=clock_timestamp() AND s.expires_at>clock_timestamp()
            FOR SHARE OF s,i
        $$;
        ALTER FUNCTION roghyar.booking_identity() OWNER TO roghyar_otp_writer;
        REVOKE ALL ON FUNCTION roghyar.booking_identity() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.booking_identity() TO roghyar_booking_writer;

        CREATE FUNCTION roghyar.booking_day_lock(doctor uuid,hospital uuid,day date)
        RETURNS bigint LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
            SELECT hashtextextended(doctor::text || ':' || hospital::text || ':' || (day-date '2000-01-01')::text, 133)
        $$;
        REVOKE ALL ON FUNCTION roghyar.booking_day_lock(uuid,uuid,date) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.booking_day_lock(uuid,uuid,date) TO roghyar_slot_writer,roghyar_booking_writer;
        CREATE FUNCTION roghyar.serialize_booking_day_confirmation() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(roghyar.booking_day_lock(NEW.doctor_id,NEW.hospital_id,NEW.day));
            RETURN NEW;
        END $$;
        CREATE TRIGGER booking_day_confirmation BEFORE INSERT ON roghyar.slot_confirmations
            FOR EACH ROW EXECUTE FUNCTION roghyar.serialize_booking_day_confirmation();

        GRANT SELECT(id, auth_subject, phone_lookup, phone_verified_at) ON roghyar.patients TO roghyar_booking_writer;
        GRANT INSERT(id,auth_subject,full_name_ciphertext,phone_ciphertext,phone_lookup,phone_verified_at,
                     guardian_name_ciphertext,age,gender,consent_at) ON roghyar.patients TO roghyar_booking_writer;
        CREATE POLICY booking_patient ON roghyar.patients FOR SELECT TO roghyar_booking_writer
            USING(EXISTS(SELECT 1 FROM roghyar.booking_identity() i
                         WHERE i.subject=auth_subject AND i.phone_lookup=patients.phone_lookup));
        CREATE POLICY booking_patient_insert ON roghyar.patients FOR INSERT TO roghyar_booking_writer
            WITH CHECK(EXISTS(SELECT 1 FROM roghyar.booking_identity() i
                       WHERE i.subject=auth_subject AND i.phone_lookup=patients.phone_lookup
                         AND phone_verified_at=i.verified_at) AND age>=18);

        GRANT SELECT(id,patient_id,doctor_id,hospital_id,status,slot_start,slot_end) ON roghyar.appointments
            TO roghyar_booking_writer;
        GRANT INSERT(id,booking_code,doctor_id,hospital_id,patient_id,slot_start,slot_end,
                     reason_ciphertext,status,consent_at) ON roghyar.appointments TO roghyar_booking_writer;
        CREATE POLICY booking_appointment_read ON roghyar.appointments FOR SELECT TO roghyar_booking_writer
            USING(patient_id IN (SELECT id FROM roghyar.patients));
        CREATE POLICY booking_appointment_insert ON roghyar.appointments FOR INSERT TO roghyar_booking_writer
            WITH CHECK(status='pending' AND assessment_id IS NULL AND patient_id IN (SELECT id FROM roghyar.patients)
                       AND EXISTS(SELECT 1 FROM roghyar.doctor_current c
                                  JOIN roghyar.directory_current h ON h.hospital_id=c.hospital_id
                                    AND h.revision=c.hospital_revision
                                  WHERE c.doctor_id=appointments.doctor_id AND c.hospital_id=appointments.hospital_id));

        GRANT SELECT ON roghyar.booking_requests TO roghyar_booking_writer;
        GRANT INSERT(subject,key_digest,fingerprint,appointment_id,result) ON roghyar.booking_requests TO roghyar_booking_writer;
        CREATE POLICY booking_request_read ON roghyar.booking_requests FOR SELECT TO roghyar_booking_writer
            USING(subject IN (SELECT i.subject FROM roghyar.booking_identity() i));
        CREATE POLICY booking_request_insert ON roghyar.booking_requests FOR INSERT TO roghyar_booking_writer
            WITH CHECK(subject IN (SELECT i.subject FROM roghyar.booking_identity() i)
                       AND appointment_id IN (SELECT id FROM roghyar.appointments));
        GRANT INSERT(appointment_id,subject,key_digest,doctor_id,hospital_id,doctor_revision,hospital_revision,
                     schedule_digest,confirmation_sequence,slot_start,slot_end,evidence_deadline)
            ON roghyar.booking_events TO roghyar_booking_writer;
        CREATE POLICY booking_event_insert ON roghyar.booking_events FOR INSERT TO roghyar_booking_writer
            WITH CHECK(EXISTS(SELECT 1 FROM roghyar.booking_requests r
                       JOIN roghyar.appointments a ON a.id=r.appointment_id
                       WHERE r.appointment_id=booking_events.appointment_id AND r.subject=booking_events.subject
                         AND r.key_digest=booking_events.key_digest AND a.doctor_id=booking_events.doctor_id
                         AND a.hospital_id=booking_events.hospital_id AND a.slot_start=booking_events.slot_start
                         AND a.slot_end=booking_events.slot_end));

        GRANT SELECT(id,hospital_id,verified,published) ON roghyar.doctors TO roghyar_booking_writer;
        GRANT SELECT(id,verified,published) ON roghyar.hospitals TO roghyar_booking_writer;
        GRANT SELECT ON roghyar.doctor_current,roghyar.directory_current TO roghyar_booking_writer;
        CREATE POLICY booking_doctor ON roghyar.doctors FOR SELECT TO roghyar_booking_writer USING(verified AND published);
        CREATE POLICY booking_hospital ON roghyar.hospitals FOR SELECT TO roghyar_booking_writer USING(verified AND published);
        CREATE POLICY booking_current ON roghyar.doctor_current FOR SELECT TO roghyar_booking_writer
            USING(expires_at>statement_timestamp());
        CREATE POLICY booking_current ON roghyar.directory_current FOR SELECT TO roghyar_booking_writer
            USING(expires_at>statement_timestamp());
        GRANT SELECT(doctor_id,hospital_id,day,sequence,doctor_revision,hospital_revision,proof)
            ON roghyar.slot_confirmations TO roghyar_booking_writer;
        CREATE POLICY booking_confirmation ON roghyar.slot_confirmations FOR SELECT TO roghyar_booking_writer
            USING(EXISTS(SELECT 1 FROM roghyar.doctor_current c
                          WHERE c.doctor_id=slot_confirmations.doctor_id AND c.hospital_id=slot_confirmations.hospital_id));
        GRANT EXECUTE ON FUNCTION roghyar.doctor_slot_occupied(uuid,uuid,timestamptz,timestamptz)
            TO roghyar_booking_writer;
    """)


def downgrade():
    op.execute("SET LOCAL row_security=off")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM roghyar.booking_requests) OR EXISTS(SELECT 1 FROM roghyar.booking_events)
            THEN RAISE EXCEPTION 'Booking history requires approved archival'; END IF;
        END $$;
        DROP TRIGGER booking_day_confirmation ON roghyar.slot_confirmations;
        DROP FUNCTION roghyar.serialize_booking_day_confirmation();
        DROP POLICY booking_appointment_insert ON roghyar.appointments;
        DROP POLICY booking_appointment_read ON roghyar.appointments;
        DROP POLICY booking_patient_insert ON roghyar.patients;
        DROP POLICY booking_patient ON roghyar.patients;
        DROP POLICY booking_doctor ON roghyar.doctors;
        DROP POLICY booking_hospital ON roghyar.hospitals;
        DROP POLICY booking_current ON roghyar.doctor_current;
        DROP POLICY booking_current ON roghyar.directory_current;
        DROP POLICY booking_confirmation ON roghyar.slot_confirmations;
        DROP TABLE roghyar.booking_events;
        DROP TABLE roghyar.booking_requests;
        DROP FUNCTION roghyar.booking_day_lock(uuid,uuid,date);
        DROP FUNCTION roghyar.booking_identity();
        ALTER TABLE roghyar.appointments DROP CONSTRAINT appointments_active_overlap;
        DROP OWNED BY roghyar_booking_writer;
        DROP ROLE roghyar_booking_writer;
    """)
