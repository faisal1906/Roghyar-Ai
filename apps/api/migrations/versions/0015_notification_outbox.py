"""Private consent, transactional notification outbox, and authenticated receipt custody."""
from alembic import op

revision = "0015_notification_outbox"
down_revision = "0014_appointment_lifecycle"
branch_labels = None
depends_on = None

ROLES = ("notification_worker", "notification_receipt", "notification_custody")


def upgrade():
    for name in ROLES:
        login = "NOLOGIN" if name.endswith("custody") else "LOGIN"
        op.execute(f"CREATE ROLE roghyar_{name} {login} NOINHERIT NOSUPERUSER NOBYPASSRLS")
        op.execute(f"GRANT USAGE ON SCHEMA roghyar TO roghyar_{name}")
    op.execute("""
        CREATE TABLE roghyar.notification_consent (
            sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
            id uuid PRIMARY KEY,
            subject uuid NOT NULL REFERENCES roghyar.otp_identities(subject),
            channel text NOT NULL CHECK(channel IN ('sms','whatsapp')),
            locale text NOT NULL CHECK(locale IN ('en','ur','ps')),
            policy integer NOT NULL CHECK(policy=1),
            reminders boolean NOT NULL,
            enabled boolean NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ix_notification_consent_current ON roghyar.notification_consent(subject,sequence DESC);
        CREATE TABLE roghyar.notification_outbox (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            appointment_id uuid NOT NULL REFERENCES roghyar.appointments(id),
            event_id uuid NOT NULL,
            patient_id uuid NOT NULL REFERENCES roghyar.patients(id),
            consent_id uuid NOT NULL REFERENCES roghyar.notification_consent(id),
            kind text NOT NULL CHECK(kind IN ('booking','cancelled','rescheduled','successor','reminder24','reminder2')),
            channel text NOT NULL CHECK(channel IN ('sms','whatsapp')),
            locale text NOT NULL CHECK(locale IN ('en','ur','ps')),
            version integer NOT NULL DEFAULT 1 CHECK(version=1),
            slot_start timestamptz NOT NULL,
            run_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL CHECK(expires_at>run_at),
            state text NOT NULL CHECK(state IN ('pending','scheduled','leased','submitting','retry','accepted',
                                                'delivered','failed','cancelled','expired','unknown')),
            attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 4),
            lease uuid,
            worker uuid,
            lease_until timestamptz,
            provider text CHECK(provider ~ '^[a-z][a-z0-9_]{0,39}$'),
            message_id text CHECK(message_id ~ '^[A-Za-z0-9._-]{1,100}$'),
            failure text CHECK(failure IN ('quota','unavailable','rejected','timeout','attempts','cancelled','expired')),
            invalidated_at timestamptz,
            clinic_proof jsonb CHECK(clinic_proof IS NULL OR octet_length(clinic_proof::text)<=2048),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(appointment_id,kind,channel,version),
            UNIQUE(provider,message_id),
            CHECK((kind IN ('reminder24','reminder2'))=(clinic_proof IS NOT NULL)),
            CHECK((state IN ('leased','submitting'))=(num_nonnulls(lease,worker,lease_until)=3)),
            CHECK(state IN ('leased','submitting') OR num_nonnulls(lease,worker,lease_until)=0),
            CHECK(state NOT IN ('accepted','delivered') OR (provider IS NOT NULL AND message_id IS NOT NULL))
        );
        CREATE INDEX ix_notification_due ON roghyar.notification_outbox(run_at,id)
            WHERE state IN ('pending','scheduled','retry');
        CREATE INDEX ix_notification_lease ON roghyar.notification_outbox(lease_until,id)
            WHERE state IN ('leased','submitting');
        CREATE INDEX ix_notification_recent ON roghyar.notification_outbox(created_at);
        CREATE INDEX ix_notification_owner ON roghyar.notification_outbox(appointment_id,created_at DESC,id);
        CREATE TABLE roghyar.notification_receipts (
            provider text NOT NULL CHECK(provider ~ '^[a-z][a-z0-9_]{0,39}$'),
            event_id uuid NOT NULL,
            job_id uuid NOT NULL REFERENCES roghyar.notification_outbox(id),
            message_id text NOT NULL CHECK(message_id ~ '^[A-Za-z0-9._-]{1,100}$'),
            status text NOT NULL CHECK(status IN ('accepted','delivered','failed','expired')),
            occurred_at timestamptz NOT NULL,
            PRIMARY KEY(provider,event_id)
        );
        ALTER TABLE roghyar.notification_consent ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.notification_consent FORCE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.notification_outbox ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.notification_outbox FORCE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.notification_receipts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.notification_receipts FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON roghyar.notification_consent,roghyar.notification_outbox,roghyar.notification_receipts FROM PUBLIC;

        GRANT SELECT ON roghyar.notification_consent TO roghyar_notification_custody,roghyar_appointment_writer;
        GRANT INSERT(id,subject,channel,locale,policy,reminders,enabled)
            ON roghyar.notification_consent TO roghyar_appointment_writer;
        CREATE POLICY notification_consent_owner ON roghyar.notification_consent TO roghyar_appointment_writer
            USING(subject IN (SELECT i.subject FROM roghyar.appointment_identity() i))
            WITH CHECK(subject IN (SELECT i.subject FROM roghyar.appointment_identity() i));
        CREATE POLICY notification_consent_custody ON roghyar.notification_consent
            FOR SELECT TO roghyar_notification_custody USING(true);

        GRANT SELECT,INSERT ON roghyar.notification_outbox TO roghyar_notification_custody;
        GRANT UPDATE(state,invalidated_at,lease,worker,lease_until,failure) ON roghyar.notification_outbox
            TO roghyar_notification_custody;
        GRANT SELECT ON roghyar.notification_outbox TO roghyar_notification_worker,roghyar_notification_receipt;
        GRANT UPDATE(state,attempts,run_at,lease,worker,lease_until,provider,message_id,failure)
            ON roghyar.notification_outbox TO roghyar_notification_worker;
        GRANT UPDATE(state,message_id,lease,worker,lease_until) ON roghyar.notification_outbox TO roghyar_notification_receipt;
        GRANT SELECT(appointment_id,state,created_at,id) ON roghyar.notification_outbox TO roghyar_appointment_writer;
        CREATE POLICY notification_custody ON roghyar.notification_outbox TO roghyar_notification_custody USING(true);
        CREATE POLICY notification_work ON roghyar.notification_outbox TO roghyar_notification_worker USING(true);
        CREATE POLICY notification_callback ON roghyar.notification_outbox TO roghyar_notification_receipt USING(true);
        CREATE POLICY notification_owner ON roghyar.notification_outbox FOR SELECT TO roghyar_appointment_writer
            USING(patient_id IN (SELECT id FROM roghyar.patients));

        GRANT SELECT,INSERT ON roghyar.notification_receipts TO roghyar_notification_receipt;
        CREATE POLICY notification_receipt_only ON roghyar.notification_receipts TO roghyar_notification_receipt USING(true);

        GRANT SELECT(id,patient_id,doctor_id,hospital_id,status,slot_start) ON roghyar.appointments TO roghyar_notification_custody;
        GRANT SELECT(id,auth_subject,phone_verified_at,phone_lookup,phone_ciphertext)
            ON roghyar.patients TO roghyar_notification_custody;
        GRANT SELECT(subject,phone_lookup,revoked_at) ON roghyar.otp_identities TO roghyar_notification_custody;
        CREATE POLICY notification_custody ON roghyar.appointments FOR SELECT TO roghyar_notification_custody USING(true);
        CREATE POLICY notification_custody ON roghyar.patients FOR SELECT TO roghyar_notification_custody USING(true);
        CREATE POLICY notification_custody ON roghyar.otp_identities FOR SELECT TO roghyar_notification_custody USING(true);

        CREATE FUNCTION roghyar.enqueue_notification(aid uuid,event_uuid uuid,event_kind text) RETURNS void
        LANGUAGE sql SET search_path=pg_catalog AS $$
            INSERT INTO roghyar.notification_outbox(appointment_id,event_id,patient_id,consent_id,kind,
                                                    channel,locale,slot_start,run_at,expires_at,state)
            SELECT a.id,event_uuid,a.patient_id,c.id,event_kind,c.channel,c.locale,a.slot_start,
                   clock_timestamp(),clock_timestamp()+interval '24 hours','pending'
            FROM roghyar.appointments a JOIN roghyar.patients p ON p.id=a.patient_id JOIN LATERAL (
                SELECT * FROM roghyar.notification_consent c WHERE c.subject=p.auth_subject
                ORDER BY sequence DESC LIMIT 1
            ) c ON c.enabled WHERE a.id=aid
            ON CONFLICT (appointment_id,kind,channel,version) DO NOTHING
        $$;
        ALTER FUNCTION roghyar.enqueue_notification(uuid,uuid,text) OWNER TO roghyar_notification_custody;
        REVOKE ALL ON FUNCTION roghyar.enqueue_notification(uuid,uuid,text) FROM PUBLIC;

        CREATE FUNCTION roghyar.notification_booking_event() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            PERFORM roghyar.enqueue_notification(NEW.appointment_id,NEW.appointment_id,'booking');
            RETURN NEW;
        END $$;
        ALTER FUNCTION roghyar.notification_booking_event() OWNER TO roghyar_notification_custody;
        CREATE TRIGGER notification_booking_event AFTER INSERT ON roghyar.booking_events
            FOR EACH ROW EXECUTE FUNCTION roghyar.notification_booking_event();

        CREATE FUNCTION roghyar.notification_lifecycle_event() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            UPDATE roghyar.notification_outbox SET invalidated_at=clock_timestamp(),
              state=CASE WHEN state IN ('scheduled','pending','retry','leased') THEN 'cancelled' ELSE state END,
              lease=CASE WHEN state='leased' THEN NULL ELSE lease END,
              worker=CASE WHEN state='leased' THEN NULL ELSE worker END,
              lease_until=CASE WHEN state='leased' THEN NULL ELSE lease_until END
            WHERE appointment_id=NEW.appointment_id AND kind IN ('reminder24','reminder2') AND invalidated_at IS NULL;
            PERFORM roghyar.enqueue_notification(NEW.appointment_id,NEW.appointment_id,
                CASE WHEN NEW.action='cancel' THEN 'cancelled' ELSE 'rescheduled' END);
            IF NEW.successor_id IS NOT NULL THEN
                PERFORM roghyar.enqueue_notification(NEW.successor_id,NEW.appointment_id,'successor');
            END IF;
            RETURN NEW;
        END $$;
        ALTER FUNCTION roghyar.notification_lifecycle_event() OWNER TO roghyar_notification_custody;
        CREATE TRIGGER notification_lifecycle_event AFTER INSERT ON roghyar.appointment_lifecycle_events
            FOR EACH ROW EXECUTE FUNCTION roghyar.notification_lifecycle_event();

        CREATE FUNCTION roghyar.invalidate_notification_consent() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            UPDATE roghyar.notification_outbox SET invalidated_at=clock_timestamp(),
              state=CASE WHEN state IN ('scheduled','pending','retry','leased') THEN 'cancelled' ELSE state END,
              lease=CASE WHEN state='leased' THEN NULL ELSE lease END,
              worker=CASE WHEN state='leased' THEN NULL ELSE worker END,
              lease_until=CASE WHEN state='leased' THEN NULL ELSE lease_until END
            WHERE patient_id IN (SELECT id FROM roghyar.patients WHERE auth_subject=NEW.subject)
              AND consent_id<>NEW.id AND invalidated_at IS NULL;
            RETURN NEW;
        END $$;
        ALTER FUNCTION roghyar.invalidate_notification_consent() OWNER TO roghyar_notification_custody;
        CREATE TRIGGER notification_consent_change AFTER INSERT ON roghyar.notification_consent
            FOR EACH ROW EXECUTE FUNCTION roghyar.invalidate_notification_consent();

        CREATE FUNCTION roghyar.resolve_notification_recipient(job uuid,fence uuid,at_time timestamptz)
        RETURNS TABLE(patient uuid, ciphertext bytea, lookup bytea, appointment_status text)
        LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
            SELECT p.id,p.phone_ciphertext,p.phone_lookup,a.status
            FROM roghyar.notification_outbox j JOIN roghyar.appointments a
                ON a.id=j.appointment_id AND a.patient_id=j.patient_id AND a.slot_start=j.slot_start
            JOIN roghyar.patients p ON p.id=a.patient_id AND p.phone_verified_at IS NOT NULL
            JOIN roghyar.otp_identities i ON i.subject=p.auth_subject AND i.phone_lookup=p.phone_lookup
                                         AND i.revoked_at IS NULL
            JOIN LATERAL (SELECT * FROM roghyar.notification_consent c WHERE c.subject=p.auth_subject
                         ORDER BY sequence DESC LIMIT 1) c ON c.id=j.consent_id AND c.enabled
                         AND c.channel=j.channel AND c.locale=j.locale AND c.policy=j.version
            WHERE j.id=job AND j.lease=fence AND j.state IN ('leased','submitting')
              AND j.lease_until>at_time AND j.expires_at>at_time AND j.invalidated_at IS NULL
              AND CASE j.kind WHEN 'cancelled' THEN a.status='cancelled'
                  WHEN 'rescheduled' THEN a.status='rescheduled'
                  WHEN 'reminder24' THEN c.reminders AND a.status='confirmed' AND a.slot_start>at_time
                  WHEN 'reminder2' THEN c.reminders AND a.status='confirmed' AND a.slot_start>at_time
                  ELSE a.status IN ('pending','confirmed') END
        $$;
        ALTER FUNCTION roghyar.resolve_notification_recipient(uuid,uuid,timestamptz) OWNER TO roghyar_notification_custody;
        REVOKE ALL ON FUNCTION roghyar.resolve_notification_recipient(uuid,uuid,timestamptz) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.resolve_notification_recipient(uuid,uuid,timestamptz) TO roghyar_notification_worker;

        CREATE FUNCTION roghyar.schedule_notification_reminders(aid uuid,starts timestamptz,at_time timestamptz,
                                                               deadline timestamptz,proof jsonb) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
            DECLARE physician uuid; added_count integer;
            BEGIN
            SELECT doctor_id INTO physician FROM roghyar.appointments WHERE id=aid;
            IF physician IS NULL THEN RETURN 0; END IF;
            PERFORM pg_advisory_xact_lock(13301,hashtext(physician::text));
            WITH added AS (
                INSERT INTO roghyar.notification_outbox(appointment_id,event_id,patient_id,consent_id,kind,channel,
                                                         locale,slot_start,run_at,expires_at,state,clinic_proof)
                SELECT a.id,a.id,a.patient_id,c.id,r.kind,c.channel,c.locale,a.slot_start,
                       a.slot_start-r.distance,least(deadline,a.slot_start),'scheduled',proof
                FROM roghyar.appointments a JOIN roghyar.patients p ON p.id=a.patient_id JOIN LATERAL (
                    SELECT * FROM roghyar.notification_consent c WHERE c.subject=p.auth_subject
                    ORDER BY sequence DESC LIMIT 1
                ) c ON c.enabled AND c.reminders
                CROSS JOIN (VALUES ('reminder24',interval '24 hours'),('reminder2',interval '2 hours')) r(kind,distance)
                WHERE a.id=aid AND a.slot_start=starts AND a.status='confirmed'
                  AND a.hospital_id::text=proof->>'hospital'
                  AND a.slot_start-r.distance>=at_time AND a.slot_start-r.distance<least(deadline,a.slot_start)
                ON CONFLICT (appointment_id,kind,channel,version) DO NOTHING RETURNING 1
            ) SELECT count(*)::integer INTO added_count FROM added;
            RETURN added_count;
            END
        $$;
        ALTER FUNCTION roghyar.schedule_notification_reminders(uuid,timestamptz,timestamptz,timestamptz,jsonb)
            OWNER TO roghyar_notification_custody;
        REVOKE ALL ON FUNCTION roghyar.schedule_notification_reminders(uuid,timestamptz,timestamptz,timestamptz,jsonb)
            FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.schedule_notification_reminders(uuid,timestamptz,timestamptz,timestamptz,jsonb)
            TO roghyar_notification_worker;

        CREATE FUNCTION roghyar.verify_notification_transition() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF OLD.state<>NEW.state AND NOT (CASE current_user
              WHEN 'roghyar_notification_worker' THEN
                (OLD.state IN ('pending','scheduled','retry') AND NEW.state IN ('leased','expired','failed'))
                OR (OLD.state='leased' AND NEW.state IN ('submitting','retry','failed','cancelled','expired'))
                OR (OLD.state='submitting' AND NEW.state IN ('accepted','retry','failed','unknown','cancelled'))
              WHEN 'roghyar_notification_receipt' THEN
                OLD.state IN ('submitting','accepted','unknown') AND NEW.state IN ('accepted','delivered','failed','expired')
              WHEN 'roghyar_notification_custody' THEN NEW.state='cancelled'
                   AND OLD.state IN ('scheduled','pending','retry','leased')
              ELSE false END)
            THEN RAISE EXCEPTION 'Invalid notification transition' USING ERRCODE='23514'; END IF;
            IF current_user='roghyar_notification_receipt' THEN
                IF NOT EXISTS(
                    SELECT 1 FROM roghyar.notification_receipts r WHERE r.job_id=NEW.id AND r.provider=NEW.provider
                      AND r.message_id=NEW.message_id AND r.status=NEW.state
                ) THEN RAISE EXCEPTION 'Notification receipt required' USING ERRCODE='23514'; END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER notification_transition BEFORE UPDATE ON roghyar.notification_outbox
            FOR EACH ROW EXECUTE FUNCTION roghyar.verify_notification_transition();
        REVOKE ALL ON FUNCTION roghyar.notification_booking_event(),roghyar.notification_lifecycle_event(),
            roghyar.invalidate_notification_consent() FROM PUBLIC;
    """)


def downgrade():
    op.execute("SET LOCAL row_security=off")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM roghyar.notification_outbox)
                OR EXISTS(SELECT 1 FROM roghyar.notification_receipts)
                OR EXISTS(SELECT 1 FROM roghyar.notification_consent)
            THEN RAISE EXCEPTION 'Notification history requires approved archival'; END IF;
        END $$;
        DROP TRIGGER notification_booking_event ON roghyar.booking_events;
        DROP TRIGGER notification_lifecycle_event ON roghyar.appointment_lifecycle_events;
        DROP POLICY notification_custody ON roghyar.otp_identities;
        DROP POLICY notification_custody ON roghyar.patients;
        DROP POLICY notification_custody ON roghyar.appointments;
        DROP TABLE roghyar.notification_receipts,roghyar.notification_outbox,roghyar.notification_consent;
        DROP FUNCTION roghyar.notification_booking_event(),roghyar.notification_lifecycle_event(),
            roghyar.invalidate_notification_consent(),roghyar.enqueue_notification(uuid,uuid,text),
            roghyar.resolve_notification_recipient(uuid,uuid,timestamptz),
            roghyar.schedule_notification_reminders(uuid,timestamptz,timestamptz,timestamptz,jsonb),
            roghyar.verify_notification_transition();
    """)
    for name in ROLES:
        op.execute(f"DROP OWNED BY roghyar_{name}")
        op.execute(f"DROP ROLE roghyar_{name}")
