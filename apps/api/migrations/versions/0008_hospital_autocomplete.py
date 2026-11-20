"""Install private directory name matching and protect reviewed identity fields."""

from alembic import op

revision = "0008_hospital_autocomplete"
down_revision = "0007_home_care_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
    op.execute(r"""
        CREATE FUNCTION roghyar.hospital_search_key(value text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path = pg_catalog
        AS $$ SELECT lower(btrim(regexp_replace(
            normalize(value, NFC), U&'[[:space:]\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+', ' ', 'g'
        ))) $$
    """)
    op.execute("REVOKE ALL ON FUNCTION roghyar.hospital_search_key(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION roghyar.hospital_search_key(text) TO roghyar_runtime")
    op.execute("""
        CREATE INDEX ix_hospitals_search_prefix
        ON roghyar.hospitals (roghyar.hospital_search_key(name) text_pattern_ops)
        WHERE verified AND published AND verified_on IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX ix_hospitals_search_trgm
        ON roghyar.hospitals USING gin (roghyar.hospital_search_key(name) public.gin_trgm_ops)
        WHERE verified AND published AND verified_on IS NOT NULL
    """)
    op.execute("""
        CREATE FUNCTION roghyar.require_hospital_identity_review() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF OLD.verified AND NEW.verified AND
               ROW(NEW.name, NEW.aliases, NEW.address, NEW.city, NEW.district, NEW.province,
                   NEW.location, NEW.source, NEW.phone, NEW.departments, NEW.is_emergency_24_7)
               IS DISTINCT FROM
               ROW(OLD.name, OLD.aliases, OLD.address, OLD.city, OLD.district, OLD.province,
                   OLD.location, OLD.source, OLD.phone, OLD.departments, OLD.is_emergency_24_7)
            THEN
                RAISE EXCEPTION 'Withdraw verification before changing hospital directory data'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER hospital_identity_review BEFORE UPDATE ON roghyar.hospitals
        FOR EACH ROW EXECUTE FUNCTION roghyar.require_hospital_identity_review()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER hospital_identity_review ON roghyar.hospitals")
    op.execute("DROP FUNCTION roghyar.require_hospital_identity_review()")
    op.execute("DROP INDEX roghyar.ix_hospitals_search_trgm")
    op.execute("DROP INDEX roghyar.ix_hospitals_search_prefix")
    op.execute("DROP FUNCTION roghyar.hospital_search_key(text)")
    # The extension may have been preinstalled or be shared by other consumers.
