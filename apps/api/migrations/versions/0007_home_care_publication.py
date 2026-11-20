"""Install durable home-care revisions and append-only publication audit."""

from alembic import op

revision = "0007_home_care_publication"
down_revision = "0006_formulary_attestation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE roghyar.home_care_revisions (
            content_id text NOT NULL CHECK (content_id ~ '^[a-z][a-z0-9_]{0,47}$'),
            version integer NOT NULL CHECK (version > 0),
            condition text NOT NULL CHECK (condition ~ '^[a-z][a-z0-9_]{0,47}$'),
            applicability text NOT NULL CHECK (applicability ~ '^[a-z][a-z0-9_]{0,47}$'),
            digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
            artifact jsonb NOT NULL CHECK (jsonb_typeof(artifact) = 'object' AND octet_length(artifact::text) <= 40000),
            translations jsonb NOT NULL CHECK (jsonb_typeof(translations) = 'array' AND octet_length(translations::text) <= 12000),
            state text NOT NULL DEFAULT 'draft' CHECK (state IN
                ('draft','submitted','reviewed','ready','active','inactive','superseded','rejected','revoked')),
            generation integer NOT NULL DEFAULT 0 CHECK (generation >= 0),
            author name NOT NULL DEFAULT session_user,
            PRIMARY KEY (content_id, version)
        );
        CREATE UNIQUE INDEX home_care_active_scope
          ON roghyar.home_care_revisions (condition, applicability) WHERE state = 'active';
        CREATE TABLE roghyar.home_care_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            content_id text NOT NULL,
            version integer NOT NULL,
            digest text NOT NULL,
            previous_state text,
            state text NOT NULL,
            actor name NOT NULL,
            actor_role name NOT NULL,
            at timestamptz NOT NULL DEFAULT clock_timestamp(),
            transaction_id bigint NOT NULL DEFAULT txid_current(),
            FOREIGN KEY (content_id, version) REFERENCES roghyar.home_care_revisions
        );

        ALTER TABLE roghyar.home_care_revisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.home_care_revisions FORCE ROW LEVEL SECURITY;
        CREATE POLICY owner_archive_read ON roghyar.home_care_revisions FOR SELECT
            USING (current_user = pg_get_userbyid(
                (SELECT relowner FROM pg_class WHERE oid = 'roghyar.home_care_revisions'::regclass)));
        ALTER TABLE roghyar.home_care_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE roghyar.home_care_events FORCE ROW LEVEL SECURITY;
        CREATE POLICY owner_archive_read ON roghyar.home_care_events FOR SELECT
            USING (current_user = pg_get_userbyid(
                (SELECT relowner FROM pg_class WHERE oid = 'roghyar.home_care_events'::regclass)));
        REVOKE ALL ON roghyar.home_care_revisions, roghyar.home_care_events FROM PUBLIC;
        GRANT SELECT ON roghyar.home_care_revisions TO roghyar_runtime, roghyar_publisher;
        CREATE POLICY runtime_active ON roghyar.home_care_revisions FOR SELECT TO roghyar_runtime USING (state = 'active');
        CREATE POLICY publisher_read ON roghyar.home_care_revisions FOR SELECT TO roghyar_publisher USING (true);
        GRANT INSERT (content_id,version,condition,applicability,digest,artifact,translations),
              UPDATE (state,generation) ON roghyar.home_care_revisions TO roghyar_publisher;
        CREATE POLICY publisher_stage ON roghyar.home_care_revisions FOR INSERT TO roghyar_publisher
            WITH CHECK (state = 'draft' AND generation = 0 AND author = session_user);
        CREATE POLICY publisher_state ON roghyar.home_care_revisions FOR UPDATE TO roghyar_publisher
            USING (true) WITH CHECK (true);

        CREATE POLICY audit_owner ON roghyar.home_care_events FOR INSERT
            WITH CHECK (current_user = pg_get_userbyid(
                (SELECT relowner FROM pg_class WHERE oid = 'roghyar.home_care_events'::regclass)));
        CREATE FUNCTION roghyar.audit_home_care() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE prior text;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                prior := OLD.state;
                IF NEW.generation <> OLD.generation + 1 OR NOT (
                    (prior = 'draft' AND NEW.state IN ('submitted','rejected')) OR
                    (prior = 'submitted' AND NEW.state IN ('reviewed','rejected')) OR
                    (prior = 'reviewed' AND NEW.state IN ('ready','rejected')) OR
                    (prior IN ('ready','inactive','superseded') AND NEW.state = 'active') OR
                    (prior = 'active' AND NEW.state IN ('inactive','superseded')) OR
                    (prior NOT IN ('revoked','rejected') AND NEW.state = 'revoked')
                ) THEN
                    RAISE EXCEPTION 'Invalid publication transition';
                END IF;
            END IF;
            INSERT INTO roghyar.home_care_events
                (content_id,version,digest,previous_state,state,actor,actor_role)
            VALUES (NEW.content_id,NEW.version,NEW.digest,prior,NEW.state,session_user,'roghyar_publisher');
            RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION roghyar.audit_home_care() FROM PUBLIC;
        CREATE TRIGGER home_care_audit AFTER INSERT OR UPDATE ON roghyar.home_care_revisions
            FOR EACH ROW EXECUTE FUNCTION roghyar.audit_home_care();
    """)


def downgrade():
    op.execute("""
        LOCK TABLE roghyar.home_care_revisions, roghyar.home_care_events IN ACCESS EXCLUSIVE MODE;
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM roghyar.home_care_revisions)
                OR EXISTS (SELECT 1 FROM roghyar.home_care_events) THEN
                RAISE EXCEPTION 'Archive publication history before downgrade';
            END IF;
        END $$;
        DROP TABLE roghyar.home_care_events, roghyar.home_care_revisions;
        DROP FUNCTION roghyar.audit_home_care();
    """)
