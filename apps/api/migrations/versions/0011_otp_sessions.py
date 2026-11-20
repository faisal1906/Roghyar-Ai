"""Private BK-01 identity, challenge and session custody."""
from alembic import op

revision = "0011_otp_sessions"
down_revision = "0010_doctor_governance"
branch_labels = None
depends_on = None

TABLES = ("otp_challenges", "otp_identities", "otp_sessions")


def upgrade():
    op.execute("CREATE ROLE roghyar_otp_writer LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS")
    op.execute("GRANT USAGE ON SCHEMA roghyar TO roghyar_otp_writer")
    op.execute("""
        CREATE TABLE roghyar.otp_challenges (
            id uuid PRIMARY KEY, phone_lookup bytea NOT NULL CHECK(octet_length(phone_lookup)=32),
            purpose text NOT NULL CHECK(purpose IN ('booking','appointments')),
            verifier bytea NOT NULL CHECK(octet_length(verifier)=32),
            state text NOT NULL CHECK(state IN ('pending','active','invalidated','superseded','locked','expired','consumed')),
            attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
            created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
            CHECK(expires_at>created_at AND expires_at<=created_at+interval '5 minutes'),
            CHECK((state='locked')=(attempts=5) OR (state IN ('expired','superseded') AND attempts=5)),
            UNIQUE(id,phone_lookup,purpose)
        );
        CREATE UNIQUE INDEX ux_otp_current ON roghyar.otp_challenges(phone_lookup,purpose)
            WHERE state IN ('pending','active');
        CREATE TABLE roghyar.otp_identities (
            subject uuid PRIMARY KEY, phone_lookup bytea NOT NULL UNIQUE CHECK(octet_length(phone_lookup)=32),
            created_at timestamptz NOT NULL, revoked_at timestamptz,
            UNIQUE(subject, phone_lookup)
        );
        CREATE TABLE roghyar.otp_sessions (
            id uuid PRIMARY KEY, subject uuid NOT NULL, phone_lookup bytea NOT NULL,
            verification uuid NOT NULL UNIQUE, purpose text NOT NULL,
            issued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
            signing_key text NOT NULL, revoked_at timestamptz,
            CHECK(expires_at>issued_at AND expires_at<=issued_at+interval '900 seconds'),
            FOREIGN KEY(subject,phone_lookup) REFERENCES roghyar.otp_identities(subject,phone_lookup),
            FOREIGN KEY(verification,phone_lookup,purpose) REFERENCES roghyar.otp_challenges(id,phone_lookup,purpose)
        )
    """)
    for table in TABLES:
        op.execute(f"ALTER TABLE roghyar.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE roghyar.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY otp_writer ON roghyar.{table} TO roghyar_otp_writer USING(true) WITH CHECK(true)")
        op.execute(f"GRANT SELECT, INSERT ON roghyar.{table} TO roghyar_otp_writer")
    op.execute("""
        GRANT UPDATE(state,attempts) ON roghyar.otp_challenges TO roghyar_otp_writer;
        GRANT UPDATE(revoked_at) ON roghyar.otp_sessions,roghyar.otp_identities TO roghyar_otp_writer;
        CREATE FUNCTION roghyar.check_otp_transition() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF NOT (
                (OLD.state='pending' AND NEW.state IN ('active','invalidated','superseded','expired') AND NEW.attempts=0)
                OR (OLD.state='active' AND NEW.state IN ('active','locked') AND NEW.attempts=OLD.attempts+1)
                OR (OLD.state='active' AND NEW.state IN ('consumed','superseded','expired') AND NEW.attempts=OLD.attempts)
            ) THEN RAISE EXCEPTION 'Invalid OTP transition' USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER otp_state_guard BEFORE UPDATE ON roghyar.otp_challenges
            FOR EACH ROW EXECUTE FUNCTION roghyar.check_otp_transition();
        CREATE FUNCTION roghyar.check_otp_session() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF NOT EXISTS(SELECT 1 FROM roghyar.otp_challenges c
              WHERE c.id=NEW.verification AND c.state='consumed') THEN
                RAISE EXCEPTION 'OTP verification required' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER otp_session_guard BEFORE INSERT ON roghyar.otp_sessions
            FOR EACH ROW EXECUTE FUNCTION roghyar.check_otp_session()
    """)


def downgrade():
    op.execute("SET LOCAL row_security=off")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM roghyar.otp_challenges) OR EXISTS(SELECT 1 FROM roghyar.otp_identities)
                OR EXISTS(SELECT 1 FROM roghyar.otp_sessions) THEN
                RAISE EXCEPTION 'OTP custody history requires approved archival';
            END IF;
        END $$
    """)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE roghyar.{table}")
    op.execute("DROP FUNCTION roghyar.check_otp_session()")
    op.execute("DROP FUNCTION roghyar.check_otp_transition()")
    op.execute("DROP OWNED BY roghyar_otp_writer")
    op.execute("DROP ROLE roghyar_otp_writer")
