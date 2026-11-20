"""Private directory revision custody and a current public eligibility projection."""
from alembic import op

revision = "0009_directory_governance"
down_revision = "0008_hospital_autocomplete"
branch_labels = None
depends_on = None

ROLES = ("importer", "reviewer", "operator", "projection")
TABLES = ("directory_revisions", "directory_reviews", "directory_events", "directory_current",
          "directory_revocations")


def upgrade():
    for role in ROLES:
        op.execute(f"CREATE ROLE roghyar_directory_{role} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS")
        op.execute(f"GRANT USAGE ON SCHEMA roghyar TO roghyar_directory_{role}")
    op.execute("""
        CREATE TABLE roghyar.directory_revisions (
            hospital_id uuid NOT NULL, revision integer NOT NULL CHECK (revision > 0),
            source_org text NOT NULL, source_record text NOT NULL, source_version integer NOT NULL CHECK (source_version > 0),
            batch_id uuid NOT NULL, body jsonb NOT NULL, evidence_digest text NOT NULL, digest text NOT NULL,
            submitter name NOT NULL DEFAULT session_user, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (hospital_id, revision), UNIQUE (source_org, source_record, source_version),
            UNIQUE (hospital_id, revision, digest)
        );
        CREATE TABLE roghyar.directory_reviews (
            hospital_id uuid NOT NULL, revision integer NOT NULL, digest text NOT NULL,
            credential text NOT NULL, reviewer name NOT NULL DEFAULT session_user,
            approval jsonb NOT NULL, expires_at timestamptz NOT NULL,
            PRIMARY KEY (hospital_id, revision),
            FOREIGN KEY (hospital_id, revision, digest) REFERENCES roghyar.directory_revisions(hospital_id, revision, digest)
        );
        CREATE TABLE roghyar.directory_current (
            hospital_id uuid PRIMARY KEY REFERENCES roghyar.hospitals(id), revision integer NOT NULL,
            expires_at timestamptz NOT NULL, emergency_known boolean NOT NULL DEFAULT false,
            FOREIGN KEY (hospital_id, revision) REFERENCES roghyar.directory_reviews(hospital_id,revision)
        );
        CREATE TABLE roghyar.directory_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(), hospital_id uuid NOT NULL, revision integer NOT NULL,
            action text NOT NULL CHECK (action IN ('published','withdrawn','superseded')),
            actor name NOT NULL DEFAULT session_user, occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
            FOREIGN KEY (hospital_id,revision) REFERENCES roghyar.directory_revisions(hospital_id,revision)
        );
        CREATE TABLE roghyar.directory_revocations (
            kind text NOT NULL CHECK (kind IN ('source','key','alias','revision')), subject text NOT NULL,
            actor name NOT NULL DEFAULT session_user, occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(kind,subject)
        )
    """)
    access = {
        "directory_revisions": {"importer": "SELECT,INSERT", "reviewer": "SELECT", "operator": "SELECT", "projection": "SELECT"},
        "directory_reviews": {"reviewer": "SELECT,INSERT", "operator": "SELECT", "projection": "SELECT"},
        "directory_events": {"operator": "SELECT", "projection": "SELECT,INSERT"},
        "directory_current": {"operator": "SELECT,INSERT,DELETE", "projection": "SELECT,DELETE"},
        "directory_revocations": {"importer": "SELECT", "reviewer": "SELECT", "operator": "SELECT,INSERT", "projection": "SELECT"},
    }
    for table, roles in access.items():
        op.execute(f"ALTER TABLE roghyar.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE roghyar.{table} FORCE ROW LEVEL SECURITY")
        for role, permissions in roles.items():
            principal = f"roghyar_directory_{role}"
            op.execute(f"GRANT {permissions} ON roghyar.{table} TO {principal}")
            op.execute(f"CREATE POLICY directory_{role} ON roghyar.{table} TO {principal} USING (true) WITH CHECK (true)")
    op.execute("""
        REVOKE INSERT ON roghyar.directory_revisions FROM roghyar_directory_importer;
        GRANT INSERT (hospital_id,revision,source_org,source_record,source_version,batch_id,body,evidence_digest,digest)
            ON roghyar.directory_revisions TO roghyar_directory_importer;
        REVOKE INSERT ON roghyar.directory_reviews FROM roghyar_directory_reviewer;
        GRANT INSERT (hospital_id,revision,digest,credential,approval,expires_at)
            ON roghyar.directory_reviews TO roghyar_directory_reviewer;
        REVOKE INSERT ON roghyar.directory_revocations FROM roghyar_directory_operator;
        GRANT INSERT (kind,subject) ON roghyar.directory_revocations TO roghyar_directory_operator;
        GRANT SELECT ON roghyar.directory_current TO roghyar_runtime;
        CREATE POLICY directory_runtime ON roghyar.directory_current FOR SELECT TO roghyar_runtime
            USING (expires_at > statement_timestamp());
        GRANT SELECT, INSERT, UPDATE ON roghyar.hospitals TO roghyar_directory_projection;
        GRANT EXECUTE ON FUNCTION roghyar.hospital_search_key(text) TO roghyar_directory_projection;
        CREATE POLICY directory_projection ON roghyar.hospitals TO roghyar_directory_projection
            USING (true) WITH CHECK (true);
        REVOKE INSERT, UPDATE, DELETE ON roghyar.hospitals FROM roghyar_maintenance
    """)
    op.execute("""
        CREATE FUNCTION roghyar.check_directory_publication() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE reviewed record;
        BEGIN
            PERFORM pg_advisory_xact_lock(79350919);
            SELECT r.*, d.submitter,d.source_org,d.source_record,d.body INTO reviewed
            FROM roghyar.directory_reviews r JOIN roghyar.directory_revisions d USING (hospital_id,revision)
            WHERE r.hospital_id=NEW.hospital_id AND r.revision=NEW.revision;
            IF NOT FOUND OR reviewed.reviewer=reviewed.submitter OR reviewed.reviewer=session_user
                OR reviewed.submitter=session_user OR reviewed.approval->>'decision' IS DISTINCT FROM 'approved'
                OR NEW.expires_at > reviewed.expires_at OR NEW.expires_at <= clock_timestamp()
                OR EXISTS (SELECT 1 FROM roghyar.directory_revocations WHERE
                      (kind='key' AND subject=reviewed.credential)
                      OR (kind='revision' AND subject=NEW.hospital_id::text || ':' || NEW.revision::text)
                      OR (kind='source' AND subject=reviewed.source_org || ':' || reviewed.source_record))
                OR EXISTS (SELECT 1 FROM roghyar.directory_revisions WHERE hospital_id=NEW.hospital_id
                           AND revision > NEW.revision)
                OR EXISTS (SELECT 1 FROM roghyar.directory_events WHERE hospital_id=NEW.hospital_id
                           AND revision >= NEW.revision AND action='published')
            THEN RAISE EXCEPTION 'Directory publication denied' USING ERRCODE='23514';
            END IF;
            NEW.emergency_known := jsonb_typeof(reviewed.body->'emergency') = 'boolean';
            UPDATE roghyar.hospitals SET verified=false,published=false WHERE id=NEW.hospital_id;
            INSERT INTO roghyar.hospitals
                (id,name,aliases,address,city,district,province,location,phone,departments,is_emergency_24_7,
                 verified,published,verified_on,verified_by,source)
            VALUES (NEW.hospital_id, reviewed.body->>'name',
                ARRAY(SELECT jsonb_array_elements_text(reviewed.body->'aliases')),
                reviewed.body->>'address',reviewed.body->>'city',reviewed.body->>'district',reviewed.body->>'province',
                public.ST_SetSRID(public.ST_MakePoint((reviewed.body->>'longitude')::float8,
                    (reviewed.body->>'latitude')::float8),4326)::public.geography,
                reviewed.body->>'phone',ARRAY(SELECT jsonb_array_elements_text(reviewed.body->'departments')),
                COALESCE((reviewed.body->>'emergency')::boolean,false),
                true,true,clock_timestamp(),(reviewed.approval->>'reviewer_id')::uuid,
                reviewed.source_org || ':' || reviewed.source_record)
            ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,aliases=EXCLUDED.aliases,
                address=EXCLUDED.address,city=EXCLUDED.city,district=EXCLUDED.district,
                province=EXCLUDED.province,location=EXCLUDED.location,phone=EXCLUDED.phone,
                departments=EXCLUDED.departments,is_emergency_24_7=EXCLUDED.is_emergency_24_7,
                verified=true,published=true,verified_on=EXCLUDED.verified_on,verified_by=EXCLUDED.verified_by,
                source=EXCLUDED.source;
            INSERT INTO roghyar.directory_events (hospital_id,revision,action)
                VALUES (NEW.hospital_id,NEW.revision,'published');
            RETURN NEW;
        END
        $$;
        ALTER FUNCTION roghyar.check_directory_publication() OWNER TO roghyar_directory_projection;
        REVOKE ALL ON FUNCTION roghyar.check_directory_publication() FROM PUBLIC;
        CREATE TRIGGER directory_publication_guard BEFORE INSERT ON roghyar.directory_current
            FOR EACH ROW EXECUTE FUNCTION roghyar.check_directory_publication()
    """)
    op.execute("""
        CREATE FUNCTION roghyar.directory_audit_withdrawal() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(79350919);
            INSERT INTO roghyar.directory_events(hospital_id,revision,action)
                VALUES (OLD.hospital_id,OLD.revision,'withdrawn');
            UPDATE roghyar.hospitals SET published=false WHERE id=OLD.hospital_id;
            RETURN OLD;
        END $$;
        CREATE TRIGGER directory_withdrawal_audit AFTER DELETE ON roghyar.directory_current
            FOR EACH ROW EXECUTE FUNCTION roghyar.directory_audit_withdrawal();
        ALTER FUNCTION roghyar.directory_audit_withdrawal() OWNER TO roghyar_directory_projection;
        REVOKE ALL ON FUNCTION roghyar.directory_audit_withdrawal() FROM PUBLIC;
        CREATE FUNCTION roghyar.directory_apply_revocation() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(79350919);
            DELETE FROM roghyar.directory_current c USING roghyar.directory_reviews r, roghyar.directory_revisions d
            WHERE (c.hospital_id,c.revision)=(r.hospital_id,r.revision)
                AND (c.hospital_id,c.revision)=(d.hospital_id,d.revision)
                AND ((NEW.kind='key' AND r.credential=NEW.subject)
                     OR (NEW.kind='source' AND d.source_org || ':' || d.source_record=NEW.subject));
            RETURN NEW;
        END $$;
        CREATE TRIGGER directory_revoke AFTER INSERT ON roghyar.directory_revocations
            FOR EACH ROW EXECUTE FUNCTION roghyar.directory_apply_revocation();
        ALTER FUNCTION roghyar.directory_apply_revocation() OWNER TO roghyar_directory_projection;
        REVOKE ALL ON FUNCTION roghyar.directory_apply_revocation() FROM PUBLIC;
        CREATE FUNCTION roghyar.directory_alias_current(hospital uuid, revision integer, alias text) RETURNS boolean
        LANGUAGE sql STABLE STRICT SECURITY DEFINER SET search_path = pg_catalog AS $$
            SELECT NOT EXISTS (SELECT 1 FROM roghyar.directory_revocations
                WHERE kind='alias' AND subject = hospital::text || ':' || revision::text || ':' ||
                  encode(sha256(convert_to(alias,'UTF8')),'hex'))
        $$;
        ALTER FUNCTION roghyar.directory_alias_current(uuid,integer,text) OWNER TO roghyar_directory_projection;
        REVOKE ALL ON FUNCTION roghyar.directory_alias_current(uuid,integer,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION roghyar.directory_alias_current(uuid,integer,text) TO roghyar_runtime;
    """)


def downgrade():
    op.execute("SET LOCAL row_security = off")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM roghyar.directory_revisions)
                OR EXISTS (SELECT 1 FROM roghyar.directory_revocations) THEN
                RAISE EXCEPTION 'Directory history exists; complete audited archival before rollback';
            END IF;
        END $$
    """)
    op.execute("DROP FUNCTION roghyar.directory_alias_current(uuid,integer,text)")
    op.execute("DROP TRIGGER directory_revoke ON roghyar.directory_revocations")
    op.execute("DROP FUNCTION roghyar.directory_apply_revocation()")
    op.execute("DROP TRIGGER directory_withdrawal_audit ON roghyar.directory_current")
    op.execute("DROP FUNCTION roghyar.directory_audit_withdrawal()")
    op.execute("DROP POLICY directory_projection ON roghyar.hospitals")
    op.execute("DROP TRIGGER directory_publication_guard ON roghyar.directory_current")
    op.execute("DROP FUNCTION roghyar.check_directory_publication()")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE roghyar.{table}")
    op.execute("GRANT INSERT, UPDATE, DELETE ON roghyar.hospitals TO roghyar_maintenance")
    for role in ROLES:
        op.execute(f"DROP OWNED BY roghyar_directory_{role}")
        op.execute(f"DROP ROLE roghyar_directory_{role}")
