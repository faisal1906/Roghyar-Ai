"""Custody for doctor profiles and schedules plus a minimal occupancy boundary."""
from alembic import op

revision = "0010_doctor_governance"
down_revision = "0009_directory_governance"
branch_labels = None
depends_on = None

ROLES = ("importer", "credential", "schedule", "operator", "projection", "occupancy")
TABLES = ("doctor_operator_scope", "doctor_revisions", "doctor_reviews", "doctor_events",
          "doctor_current", "doctor_revocations")


def upgrade():
    for role in ROLES:
        op.execute(f"CREATE ROLE roghyar_doc_{role} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS")
        op.execute(f"GRANT USAGE ON SCHEMA roghyar TO roghyar_doc_{role}")
    op.execute("""
        CREATE TABLE roghyar.doctor_operator_scope (
            login name NOT NULL, hospital_id uuid NOT NULL REFERENCES roghyar.hospitals(id),
            kind text NOT NULL CHECK (kind IN ('importer','credential','schedule','operator')),
            PRIMARY KEY (login,hospital_id,kind)
        );
        CREATE TABLE roghyar.doctor_revisions (
            doctor_id uuid NOT NULL, revision integer NOT NULL CHECK (revision>0),
            hospital_id uuid NOT NULL REFERENCES roghyar.hospitals(id), hospital_revision integer NOT NULL,
            registry text NOT NULL, registration text NOT NULL, digest text NOT NULL, body jsonb NOT NULL,
            submitter name NOT NULL DEFAULT session_user, submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(doctor_id,revision), UNIQUE(doctor_id,revision,hospital_id,digest)
        );
        CREATE INDEX ix_doctor_revision_registration ON roghyar.doctor_revisions(registry,registration);
        CREATE TABLE roghyar.doctor_reviews (
            doctor_id uuid NOT NULL, revision integer NOT NULL, hospital_id uuid NOT NULL, digest text NOT NULL,
            kind text NOT NULL CHECK (kind IN ('credential','schedule')), credential text NOT NULL,
            reviewer name NOT NULL DEFAULT session_user, proof jsonb NOT NULL, expires_at timestamptz NOT NULL,
            PRIMARY KEY(doctor_id,revision,kind),
            FOREIGN KEY(doctor_id,revision,hospital_id,digest)
                REFERENCES roghyar.doctor_revisions(doctor_id,revision,hospital_id,digest)
        );
        CREATE TABLE roghyar.doctor_current (
            doctor_id uuid PRIMARY KEY REFERENCES roghyar.doctors(id), revision integer NOT NULL,
            hospital_id uuid NOT NULL, hospital_revision integer NOT NULL,
            taxonomy_version integer NOT NULL, taxonomy_digest text NOT NULL, specialties text[] NOT NULL,
            schedule jsonb NOT NULL, expires_at timestamptz NOT NULL, reviewed_at timestamptz NOT NULL
        );
        CREATE INDEX ix_doctor_current_hospital ON roghyar.doctor_current(hospital_id,doctor_id);
        CREATE INDEX ix_doctor_current_specialties ON roghyar.doctor_current USING gin(specialties);
        CREATE TABLE roghyar.doctor_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(), doctor_id uuid NOT NULL,
            revision integer NOT NULL, hospital_id uuid NOT NULL,
            action text NOT NULL CHECK (action IN ('published','withdrawn')),
            actor name NOT NULL DEFAULT session_user, occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id()
        );
        CREATE INDEX ix_doctor_events_history ON roghyar.doctor_events(doctor_id,revision);
        CREATE TABLE roghyar.doctor_revocations (
            hospital_id uuid NOT NULL, kind text NOT NULL CHECK (kind IN ('key','source','revision')),
            subject text NOT NULL, actor name NOT NULL DEFAULT session_user,
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(hospital_id,kind,subject)
        );
        CREATE INDEX ix_doc_active_overlap ON roghyar.appointments(doctor_id,slot_start,slot_end)
            WHERE status IN ('pending','confirmed')
    """)
    for table in TABLES:
        op.execute(f"ALTER TABLE roghyar.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE roghyar.{table} FORCE ROW LEVEL SECURITY")
    private = {
        "doctor_revisions": ROLES[:4], "doctor_reviews": ("credential", "schedule", "operator"),
        "doctor_current": ("operator",), "doctor_events": ("operator",), "doctor_revocations": ROLES[:4],
    }
    for kind in ROLES[:4]:
        principal = f"roghyar_doc_{kind}"
        op.execute(f"GRANT SELECT ON roghyar.doctor_operator_scope TO {principal}")
        op.execute(f"CREATE POLICY doc_{kind} ON roghyar.doctor_operator_scope FOR SELECT TO {principal} "
                   "USING(login=session_user)")
    for table, roles in private.items():
        for kind in roles:
            principal = f"roghyar_doc_{kind}"
            op.execute(f"GRANT SELECT ON roghyar.{table} TO {principal}")
            scope = ("EXISTS(SELECT 1 FROM roghyar.doctor_operator_scope s "
                     f"WHERE s.login=session_user AND s.kind='{kind}' AND s.hospital_id={table}.hospital_id)")
            if table == "doctor_reviews" and kind != "operator":
                scope += f" AND kind='{kind}'"
            op.execute(f"CREATE POLICY doc_{kind} ON roghyar.{table} TO {principal} "
                       f"USING ({scope}) WITH CHECK ({scope})")
        op.execute(f"GRANT SELECT ON roghyar.{table} TO roghyar_doc_projection")
        op.execute(f"CREATE POLICY doc_projection ON roghyar.{table} TO roghyar_doc_projection "
                   "USING(true) WITH CHECK(true)")
    op.execute("""
        GRANT INSERT(doctor_id,revision,hospital_id,hospital_revision,registry,registration,digest,body)
            ON roghyar.doctor_revisions TO roghyar_doc_importer;
        GRANT INSERT(doctor_id,revision,hospital_id,digest,kind,credential,proof,expires_at)
            ON roghyar.doctor_reviews TO roghyar_doc_credential,roghyar_doc_schedule;
        GRANT INSERT(doctor_id,revision,hospital_id), DELETE ON roghyar.doctor_current TO roghyar_doc_operator;
        GRANT INSERT(hospital_id,kind,subject) ON roghyar.doctor_revocations TO roghyar_doc_operator;
        GRANT INSERT ON roghyar.doctor_events TO roghyar_doc_projection;
        GRANT DELETE ON roghyar.doctor_current TO roghyar_doc_projection;
        REVOKE INSERT, UPDATE, DELETE ON roghyar.doctors,roghyar.doctor_schedules FROM roghyar_maintenance;
        GRANT SELECT,INSERT,UPDATE ON roghyar.doctors TO roghyar_doc_projection;
        CREATE POLICY doc_projection ON roghyar.doctors TO roghyar_doc_projection USING(true) WITH CHECK(true);
        GRANT SELECT ON roghyar.directory_current TO roghyar_doc_projection,roghyar_doc_occupancy;
        CREATE POLICY doc_projection ON roghyar.directory_current FOR SELECT TO roghyar_doc_projection
            USING(expires_at>statement_timestamp());
        CREATE POLICY doc_occupancy ON roghyar.directory_current FOR SELECT TO roghyar_doc_occupancy
            USING(expires_at>statement_timestamp());
        GRANT SELECT(id,verified,published) ON roghyar.hospitals TO roghyar_doc_projection,roghyar_doc_occupancy;
        CREATE POLICY doc_projection ON roghyar.hospitals FOR SELECT TO roghyar_doc_projection USING(verified AND published);
        CREATE POLICY doc_occupancy ON roghyar.hospitals FOR SELECT TO roghyar_doc_occupancy USING(verified AND published);
        GRANT SELECT ON roghyar.doctor_current TO roghyar_runtime,roghyar_doc_occupancy;
        CREATE POLICY doc_fresh ON roghyar.doctor_current FOR SELECT TO roghyar_runtime,roghyar_doc_occupancy
            USING(expires_at>statement_timestamp());
        GRANT SELECT(doctor_id,hospital_id,status,slot_start,slot_end) ON roghyar.appointments TO roghyar_doc_occupancy;
        CREATE POLICY doc_occupancy ON roghyar.appointments FOR SELECT TO roghyar_doc_occupancy
            USING(status IN ('pending','confirmed'));
    """)
    op.execute("""
        CREATE FUNCTION roghyar.publish_doctor_projection() RETURNS trigger LANGUAGE plpgsql
          SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE candidate record; credential record; hours record;
        BEGIN
          PERFORM pg_advisory_xact_lock(79350919);
          SELECT * INTO STRICT candidate FROM roghyar.doctor_revisions
            WHERE doctor_id=NEW.doctor_id AND revision=NEW.revision AND hospital_id=NEW.hospital_id;
          SELECT * INTO STRICT credential FROM roghyar.doctor_reviews
            WHERE doctor_id=NEW.doctor_id AND revision=NEW.revision AND kind='credential';
          SELECT * INTO STRICT hours FROM roghyar.doctor_reviews
            WHERE doctor_id=NEW.doctor_id AND revision=NEW.revision AND kind='schedule';
          IF credential.reviewer IN (session_user,candidate.submitter,hours.reviewer)
              OR hours.reviewer IN (session_user,candidate.submitter) OR candidate.submitter=session_user
              OR credential.proof->>'decision' IS DISTINCT FROM 'approved'
              OR hours.proof->>'decision' IS DISTINCT FROM 'approved'
              OR least(credential.expires_at,hours.expires_at) <= clock_timestamp()
              OR NOT EXISTS(SELECT 1 FROM roghyar.directory_current c JOIN roghyar.hospitals h ON h.id=c.hospital_id
                WHERE c.hospital_id=NEW.hospital_id AND c.revision=candidate.hospital_revision
                  AND h.verified AND h.published)
              OR EXISTS(SELECT 1 FROM roghyar.doctor_revisions d WHERE d.doctor_id=NEW.doctor_id
                  AND d.revision>NEW.revision)
              OR EXISTS(SELECT 1 FROM roghyar.doctor_events e WHERE e.doctor_id=NEW.doctor_id
                  AND e.revision>=NEW.revision AND e.action='published')
              OR EXISTS(SELECT 1 FROM roghyar.doctor_revocations r WHERE r.hospital_id=NEW.hospital_id AND
                 ((r.kind='key' AND r.subject IN (credential.credential,hours.credential))
                  OR (r.kind='source' AND r.subject IN (
                    candidate.body->'professional'->>'reference',candidate.body->'affiliation'->>'reference',
                    candidate.body->'schedule_source'->>'reference'))
                  OR (r.kind='revision' AND r.subject=NEW.doctor_id::text || ':' || NEW.revision::text)))
          THEN RAISE EXCEPTION 'Doctor publication denied' USING ERRCODE='23514'; END IF;
          NEW.hospital_revision := candidate.hospital_revision;
          NEW.taxonomy_version := (candidate.body->>'taxonomy_version')::integer;
          NEW.taxonomy_digest := candidate.body->>'taxonomy_digest';
          NEW.specialties := ARRAY(SELECT jsonb_array_elements_text(candidate.body->'specialties'));
          NEW.schedule := candidate.body->'schedule';
          NEW.expires_at := least(credential.expires_at,hours.expires_at);
          NEW.reviewed_at := (credential.proof->>'reviewed_at')::timestamptz;
          INSERT INTO roghyar.doctors(id,hospital_id,full_name,specialty,pmdc_reg_no,qualifications,verified,published,
                                      verified_on,verified_by,source)
          VALUES (NEW.doctor_id,NEW.hospital_id,candidate.body->>'name',NEW.specialties[1],candidate.registration,
                  ARRAY(SELECT jsonb_array_elements_text(candidate.body->'qualifications')),true,true,
                  NEW.reviewed_at,(credential.proof->>'reviewer_id')::uuid,'governed-doctor-publication')
          ON CONFLICT(id) DO UPDATE SET full_name=EXCLUDED.full_name,specialty=EXCLUDED.specialty,
              qualifications=EXCLUDED.qualifications,verified=true,published=true,verified_on=EXCLUDED.verified_on,
              verified_by=EXCLUDED.verified_by,source=EXCLUDED.source
              WHERE roghyar.doctors.hospital_id=EXCLUDED.hospital_id AND
                    lower(trim(roghyar.doctors.pmdc_reg_no))=lower(trim(EXCLUDED.pmdc_reg_no));
          IF NOT FOUND THEN RAISE EXCEPTION 'Doctor identity conflict' USING ERRCODE='23514'; END IF;
          INSERT INTO roghyar.doctor_events(doctor_id,revision,hospital_id,action)
              VALUES(NEW.doctor_id,NEW.revision,NEW.hospital_id,'published');
          RETURN NEW;
        EXCEPTION WHEN no_data_found THEN
          RAISE EXCEPTION 'Doctor publication requires both reviews' USING ERRCODE='23514';
        END $$;
        CREATE TRIGGER doctor_projection BEFORE INSERT ON roghyar.doctor_current FOR EACH ROW
             EXECUTE FUNCTION roghyar.publish_doctor_projection();
        ALTER FUNCTION roghyar.publish_doctor_projection() OWNER TO roghyar_doc_projection;
        REVOKE ALL ON FUNCTION roghyar.publish_doctor_projection() FROM PUBLIC;

        CREATE FUNCTION roghyar.audit_doctor_withdrawal() RETURNS trigger LANGUAGE plpgsql
          SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(79350919);
          UPDATE roghyar.doctors SET published=false WHERE id=OLD.doctor_id;
          INSERT INTO roghyar.doctor_events(doctor_id,revision,hospital_id,action)
              VALUES(OLD.doctor_id,OLD.revision,OLD.hospital_id,'withdrawn');
          RETURN OLD;
        END $$;
        CREATE TRIGGER doctor_withdrawal AFTER DELETE ON roghyar.doctor_current FOR EACH ROW
             EXECUTE FUNCTION roghyar.audit_doctor_withdrawal();
        ALTER FUNCTION roghyar.audit_doctor_withdrawal() OWNER TO roghyar_doc_projection;
        REVOKE ALL ON FUNCTION roghyar.audit_doctor_withdrawal() FROM PUBLIC;

        CREATE FUNCTION roghyar.apply_doctor_revocation() RETURNS trigger LANGUAGE plpgsql
          SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(79350919);
          DELETE FROM roghyar.doctor_current c USING roghyar.doctor_revisions d
          WHERE c.hospital_id=NEW.hospital_id AND (d.doctor_id,d.revision)=(c.doctor_id,c.revision)
            AND ((NEW.kind='revision' AND NEW.subject=c.doctor_id::text || ':' || c.revision::text)
              OR (NEW.kind='source' AND NEW.subject IN (d.body->'professional'->>'reference',
                  d.body->'affiliation'->>'reference',d.body->'schedule_source'->>'reference'))
              OR (NEW.kind='key' AND EXISTS(SELECT 1 FROM roghyar.doctor_reviews r
                  WHERE (r.doctor_id,r.revision)=(c.doctor_id,c.revision) AND r.credential=NEW.subject)));
          RETURN NEW;
        END $$;
        CREATE TRIGGER doctor_revocation AFTER INSERT ON roghyar.doctor_revocations FOR EACH ROW
            EXECUTE FUNCTION roghyar.apply_doctor_revocation();
        ALTER FUNCTION roghyar.apply_doctor_revocation() OWNER TO roghyar_doc_projection;
        REVOKE ALL ON FUNCTION roghyar.apply_doctor_revocation() FROM PUBLIC;

        CREATE FUNCTION roghyar.doctor_slot_occupied(doctor uuid,hospital uuid,starts timestamptz,ends timestamptz)
        RETURNS boolean LANGUAGE sql STABLE STRICT SECURITY DEFINER SET search_path=pg_catalog AS $$
          SELECT NOT EXISTS (
            SELECT 1 FROM roghyar.doctor_current c JOIN roghyar.directory_current h ON h.hospital_id=c.hospital_id
            JOIN roghyar.hospitals p ON p.id=h.hospital_id
            WHERE c.doctor_id=doctor AND c.hospital_id=hospital AND c.hospital_revision=h.revision
              AND p.verified AND p.published AND ends>starts AND ends-starts<=interval '2 hours'
          ) OR EXISTS (
            SELECT 1 FROM roghyar.appointments a
            WHERE a.doctor_id=doctor AND a.hospital_id=hospital AND a.status IN ('pending','confirmed')
              AND a.slot_start<ends AND a.slot_end>starts
          )
        $$;
        ALTER FUNCTION roghyar.doctor_slot_occupied(uuid,uuid,timestamptz,timestamptz) OWNER TO roghyar_doc_occupancy;
        REVOKE ALL ON FUNCTION roghyar.doctor_slot_occupied(uuid,uuid,timestamptz,timestamptz) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.doctor_slot_occupied(uuid,uuid,timestamptz,timestamptz) TO roghyar_runtime;
    """)


def downgrade():
    op.execute("SET LOCAL row_security=off")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM roghyar.doctor_revisions) OR EXISTS(SELECT 1 FROM roghyar.doctor_revocations)
               OR EXISTS(SELECT 1 FROM roghyar.doctor_operator_scope) THEN
                RAISE EXCEPTION 'Doctor custody history requires audited archival before rollback';
            END IF;
        END $$
    """)
    op.execute("DROP FUNCTION roghyar.doctor_slot_occupied(uuid,uuid,timestamptz,timestamptz)")
    for trigger, table, function in (
        ("doctor_revocation", "doctor_revocations", "apply_doctor_revocation"),
        ("doctor_withdrawal", "doctor_current", "audit_doctor_withdrawal"),
        ("doctor_projection", "doctor_current", "publish_doctor_projection"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON roghyar.{table}")
        op.execute(f"DROP FUNCTION roghyar.{function}()")
    for policy, table in (("doc_projection","doctors"), ("doc_projection","hospitals"),
                          ("doc_occupancy","hospitals"), ("doc_occupancy","appointments"),
                          ("doc_projection","directory_current"), ("doc_occupancy","directory_current")):
        op.execute(f"DROP POLICY {policy} ON roghyar.{table}")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE roghyar.{table}")
    op.execute("DROP INDEX roghyar.ix_doc_active_overlap")
    op.execute("GRANT INSERT,UPDATE,DELETE ON roghyar.doctors,roghyar.doctor_schedules TO roghyar_maintenance")
    for role in ROLES:
        op.execute(f"DROP OWNED BY roghyar_doc_{role}")
        op.execute(f"DROP ROLE roghyar_doc_{role}")
