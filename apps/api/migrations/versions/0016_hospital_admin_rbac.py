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
        CREATE TABLE roghyar.staff_clinic_approvals (
            id uuid PRIMARY KEY, actor uuid NOT NULL, revision integer NOT NULL,
            hospital uuid NOT NULL REFERENCES roghyar.hospitals(id),
            appointment uuid NOT NULL, expected_change timestamptz NOT NULL,
            slot_start timestamptz NOT NULL, slot_end timestamptz NOT NULL CHECK(slot_end>slot_start),
            action text NOT NULL CHECK(action IN ('confirm','cancel','complete','no_show')),
            expires_at timestamptz NOT NULL,
            basis text NOT NULL CHECK(basis ~ '^[a-f0-9]{64}$'),
            proof_digest text CHECK(proof_digest ~ '^[a-f0-9]{64}$'),
            proof_day date,
            approver uuid NOT NULL CHECK(approver<>actor),
            FOREIGN KEY(actor,revision) REFERENCES roghyar.staff_memberships(subject,revision),
            CHECK((action='confirm')=(proof_digest IS NOT NULL AND proof_day IS NOT NULL))
        );
        CREATE TABLE roghyar.staff_clinic_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            decision uuid NOT NULL UNIQUE REFERENCES roghyar.staff_clinic_approvals(id),
            appointment_id uuid NOT NULL REFERENCES roghyar.appointments(id),
            actor uuid NOT NULL, revision integer NOT NULL, hospital uuid NOT NULL,
            key_digest bytea NOT NULL CHECK(octet_length(key_digest)=32),
            prior_status text NOT NULL, result_status text NOT NULL,
            happened_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY(actor,revision) REFERENCES roghyar.staff_memberships(subject,revision),
            UNIQUE(actor,revision,key_digest)
        );
        ALTER TABLE roghyar.staff_clinic_approvals ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.staff_clinic_approvals FORCE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.staff_clinic_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.staff_clinic_events FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON roghyar.staff_clinic_approvals,roghyar.staff_clinic_events FROM PUBLIC;
        GRANT INSERT ON roghyar.staff_clinic_approvals TO roghyar_staff_provisioner;
        CREATE POLICY staff_clinic_provision ON roghyar.staff_clinic_approvals
            FOR INSERT TO roghyar_staff_provisioner WITH CHECK(true);
        GRANT SELECT ON roghyar.staff_clinic_approvals TO roghyar_staff_custody;
        CREATE POLICY staff_clinic_read ON roghyar.staff_clinic_approvals
            FOR SELECT TO roghyar_staff_custody USING(true);
        GRANT SELECT,INSERT ON roghyar.staff_clinic_events TO roghyar_staff_custody;
        CREATE POLICY staff_clinic_write ON roghyar.staff_clinic_events TO roghyar_staff_custody USING(true);

        GRANT SELECT(id,doctor_id,hospital_id,slot_start,slot_end,status,status_changed_at)
            ON roghyar.appointments TO roghyar_staff_custody;
        GRANT UPDATE(status,status_changed_at) ON roghyar.appointments TO roghyar_staff_custody;
        CREATE POLICY staff_custody_appointments ON roghyar.appointments TO roghyar_staff_custody
            USING(true) WITH CHECK(status IN ('confirmed','cancelled','completed','no_show'));
        GRANT SELECT(doctor_id,hospital_id,revision,hospital_revision,expires_at)
            ON roghyar.doctor_current TO roghyar_staff_custody;
        GRANT SELECT(hospital_id,revision,expires_at) ON roghyar.directory_current TO roghyar_staff_custody;
        GRANT SELECT(doctor_id,hospital_id,day,sequence,doctor_revision,hospital_revision,proof)
            ON roghyar.slot_confirmations TO roghyar_staff_custody;
        CREATE POLICY staff_clinic_current ON roghyar.doctor_current FOR SELECT TO roghyar_staff_custody USING(true);
        CREATE POLICY staff_clinic_current ON roghyar.directory_current FOR SELECT TO roghyar_staff_custody USING(true);
        CREATE POLICY staff_clinic_current ON roghyar.slot_confirmations FOR SELECT TO roghyar_staff_custody USING(true);

        CREATE FUNCTION roghyar.apply_staff_clinic_decision(target uuid,decision_id uuid,replay_key bytea)
        RETURNS TABLE(event_id uuid,status text,denial text)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE staff record; decision_row record; entry record; visit record; slot_proof record;
                next_status text; now_at timestamptz;
        BEGIN
            SELECT * INTO staff FROM roghyar.staff_access();
            IF NOT FOUND OR staff.staff_role NOT IN ('hospital_admin','appointment_manager') THEN
                RETURN QUERY SELECT NULL::uuid,NULL::text,'forbidden'::text;
                RETURN;
            END IF;
            PERFORM pg_advisory_xact_lock(16101,hashtext(staff.subject::text));
            SELECT * INTO entry FROM roghyar.staff_clinic_events e WHERE e.actor=staff.subject
                AND e.revision=staff.revision AND e.key_digest=replay_key;
            IF FOUND THEN
                IF entry.decision<>decision_id OR entry.appointment_id<>target THEN
                    RETURN QUERY SELECT NULL::uuid,NULL::text,'idempotency_conflict'::text;
                ELSE
                    RETURN QUERY SELECT entry.id,entry.result_status,NULL::text;
                END IF;
                RETURN;
            END IF;
            SELECT * INTO decision_row FROM roghyar.staff_clinic_approvals d WHERE d.id=decision_id
                AND d.actor=staff.subject AND d.revision=staff.revision
                AND d.hospital=staff.hospital AND d.appointment=target;
            IF NOT FOUND THEN
                RETURN QUERY SELECT NULL::uuid,NULL::text,'not_found'::text;
                RETURN;
            END IF;
            IF decision_row.action='confirm' THEN
                PERFORM pg_advisory_xact_lock_shared(79350919);
            END IF;
            SELECT a.doctor_id INTO visit FROM roghyar.appointments a WHERE a.id=target AND a.hospital_id=staff.hospital;
            IF NOT FOUND THEN
                RETURN QUERY SELECT NULL::uuid,NULL::text,'not_found'::text;
                RETURN;
            END IF;
            IF decision_row.action='confirm' THEN
                PERFORM pg_advisory_xact_lock_shared(roghyar.booking_day_lock(
                    visit.doctor_id,staff.hospital,decision_row.proof_day));
            END IF;
            PERFORM pg_advisory_xact_lock(13301,hashtext(visit.doctor_id::text));
            SELECT a.id,a.doctor_id,a.status,a.status_changed_at,a.slot_start,a.slot_end INTO visit
                FROM roghyar.appointments a WHERE a.id=target AND a.hospital_id=staff.hospital FOR UPDATE;
            now_at := clock_timestamp();
            IF NOT FOUND OR decision_row.expires_at<=now_at OR visit.status_changed_at<>decision_row.expected_change
               OR visit.slot_start<>decision_row.slot_start OR visit.slot_end<>decision_row.slot_end
               OR EXISTS(SELECT 1 FROM roghyar.staff_clinic_events e WHERE e.decision=decision_id) THEN
                RETURN QUERY SELECT NULL::uuid,NULL::text,'stale_revision'::text;
                RETURN;
            END IF;
            IF decision_row.action='confirm' AND visit.status='pending' AND visit.slot_start>now_at THEN
                SELECT s.proof,s.doctor_revision,s.hospital_revision INTO slot_proof
                FROM roghyar.slot_confirmations s WHERE s.doctor_id=visit.doctor_id AND s.hospital_id=staff.hospital
                    AND s.day=decision_row.proof_day ORDER BY s.sequence DESC LIMIT 1;
                IF NOT FOUND OR encode(sha256(convert_to(slot_proof.proof,'UTF8')),'hex')<>decision_row.proof_digest
                    OR (visit.slot_start AT TIME ZONE 'Asia/Karachi')::date<>decision_row.proof_day
                    OR NOT EXISTS (
                        SELECT 1 FROM roghyar.doctor_current c JOIN roghyar.directory_current h
                          ON h.hospital_id=c.hospital_id AND h.revision=c.hospital_revision
                        WHERE c.doctor_id=visit.doctor_id AND c.hospital_id=staff.hospital
                          AND c.revision=slot_proof.doctor_revision AND h.revision=slot_proof.hospital_revision
                          AND least(c.expires_at,h.expires_at)>now_at
                    ) THEN
                    RETURN QUERY SELECT NULL::uuid,NULL::text,'schedule_unavailable'::text;
                    RETURN;
                END IF;
                next_status:='confirmed';
            ELSIF decision_row.action='cancel' AND visit.status IN ('pending','confirmed') THEN
                next_status:='cancelled';
            ELSIF decision_row.action IN ('complete','no_show') AND visit.status='confirmed' AND visit.slot_end<=now_at THEN
                next_status:=CASE decision_row.action WHEN 'complete' THEN 'completed' ELSE 'no_show' END;
            ELSE
                RETURN QUERY SELECT NULL::uuid,NULL::text,'invalid_transition'::text;
                RETURN;
            END IF;
            UPDATE roghyar.appointments a SET status=next_status,status_changed_at=now_at WHERE a.id=target;
            RETURN QUERY INSERT INTO roghyar.staff_clinic_events AS e
                (decision,appointment_id,actor,revision,hospital,key_digest,prior_status,result_status)
                VALUES(decision_id,target,staff.subject,staff.revision,staff.hospital,replay_key,visit.status,next_status)
                RETURNING e.id,e.result_status,NULL::text;
        END $$;
        ALTER FUNCTION roghyar.apply_staff_clinic_decision(uuid,uuid,bytea) OWNER TO roghyar_staff_custody;
        REVOKE ALL ON FUNCTION roghyar.apply_staff_clinic_decision(uuid,uuid,bytea) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.apply_staff_clinic_decision(uuid,uuid,bytea) TO roghyar_staff_runtime;
        GRANT EXECUTE ON FUNCTION roghyar.booking_day_lock(uuid,uuid,date) TO roghyar_staff_custody;

        ALTER TABLE roghyar.notification_outbox DROP CONSTRAINT notification_outbox_kind_check;
        ALTER TABLE roghyar.notification_outbox ADD CONSTRAINT notification_outbox_kind_check
            CHECK(kind IN ('booking','cancelled','rescheduled','successor','reminder24','reminder2','clinic_confirmed'));
        CREATE FUNCTION roghyar.staff_clinic_notification() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
            IF NEW.result_status='confirmed' THEN
                PERFORM roghyar.enqueue_notification(NEW.appointment_id,NEW.id,'clinic_confirmed');
            ELSIF NEW.result_status='cancelled' THEN
                UPDATE roghyar.notification_outbox SET invalidated_at=clock_timestamp(),
                    state=CASE WHEN state IN ('scheduled','pending','retry','leased') THEN 'cancelled' ELSE state END,
                    lease=CASE WHEN state='leased' THEN NULL ELSE lease END,
                    worker=CASE WHEN state='leased' THEN NULL ELSE worker END,
                    lease_until=CASE WHEN state='leased' THEN NULL ELSE lease_until END
                WHERE appointment_id=NEW.appointment_id AND kind IN ('reminder24','reminder2') AND invalidated_at IS NULL;
                PERFORM roghyar.enqueue_notification(NEW.appointment_id,NEW.id,'cancelled');
            END IF;
            RETURN NEW;
        END $$;
        ALTER FUNCTION roghyar.staff_clinic_notification() OWNER TO roghyar_notification_custody;
        REVOKE ALL ON FUNCTION roghyar.staff_clinic_notification() FROM PUBLIC;
        CREATE TRIGGER staff_clinic_notification AFTER INSERT ON roghyar.staff_clinic_events
            FOR EACH ROW EXECUTE FUNCTION roghyar.staff_clinic_notification();

    """)


def downgrade():
    op.execute("SET LOCAL row_security=off")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM roghyar.staff_memberships) OR EXISTS(SELECT 1 FROM roghyar.staff_sessions)
                OR EXISTS(SELECT 1 FROM roghyar.staff_audit)
                OR EXISTS(SELECT 1 FROM roghyar.staff_clinic_approvals)
                OR EXISTS(SELECT 1 FROM roghyar.staff_clinic_events)
            THEN RAISE EXCEPTION 'Staff history requires approved archival'; END IF;
        END $$;
        DROP TRIGGER staff_clinic_notification ON roghyar.staff_clinic_events;
        DROP FUNCTION roghyar.staff_clinic_notification();
        ALTER TABLE roghyar.notification_outbox DROP CONSTRAINT notification_outbox_kind_check;
        ALTER TABLE roghyar.notification_outbox ADD CONSTRAINT notification_outbox_kind_check
            CHECK(kind IN ('booking','cancelled','rescheduled','successor','reminder24','reminder2'));
        DROP FUNCTION roghyar.apply_staff_clinic_decision(uuid,uuid,bytea);
        DROP POLICY staff_custody_appointments ON roghyar.appointments;
        DROP POLICY staff_clinic_current ON roghyar.doctor_current;
        DROP POLICY staff_clinic_current ON roghyar.directory_current;
        DROP POLICY staff_clinic_current ON roghyar.slot_confirmations;
        DROP TABLE roghyar.staff_clinic_events,roghyar.staff_clinic_approvals;
        DROP POLICY staff_doctors ON roghyar.doctors;
        DROP POLICY staff_appointments ON roghyar.appointments;
        DROP POLICY staff_schedule ON roghyar.doctor_current;
        DROP TABLE roghyar.staff_audit,roghyar.staff_sessions,roghyar.staff_memberships;
        DROP FUNCTION roghyar.staff_access(),roghyar.staff_logout();
    """)
    for role in ROLES:
        op.execute(f"DROP OWNED BY roghyar_{role}")
        op.execute(f"DROP ROLE roghyar_{role}")
