"""Allow narrowly scoped, encrypted assessment inserts."""

from alembic import op

revision = "0004_assessments"
down_revision = "0003_rls"
branch_labels = None
depends_on = None

LEGACY = """(anonymized_at IS NULL AND (session_id IS NOT NULL OR patient_id IS NOT NULL)
    AND text_ciphertext IS NOT NULL AND age IS NOT NULL AND severity IS NOT NULL
    AND ((duration_hours IS NULL AND more_than_one_year)
         OR (duration_hours BETWEEN 1 AND 8760 AND duration_hours IS NOT NULL AND NOT more_than_one_year)))"""
SCRUBBED = """(anonymized_at IS NOT NULL AND session_id IS NULL AND patient_id IS NULL
    AND text_ciphertext IS NULL AND age IS NULL AND severity IS NULL AND duration_hours IS NULL
    AND NOT more_than_one_year AND language IS NULL AND answers_ciphertext IS NULL
    AND safety_details_ciphertext IS NULL AND level IS NULL AND condition_code IS NULL
    AND specialty IS NULL AND red_flags IS NULL)"""
ENCRYPTED = """(anonymized_at IS NULL AND num_nonnulls(session_id, patient_id) = 1
    AND text_ciphertext IS NOT NULL AND answers_ciphertext IS NOT NULL
    AND safety_details_ciphertext IS NOT NULL AND age IS NULL AND severity IS NULL
    AND duration_hours IS NULL AND NOT more_than_one_year AND language IS NULL)"""
OWNER = """(
    (current_setting('roghyar.kind',true) = 'anonymous' AND patient_id IS NULL
        AND session_id = nullif(current_setting('roghyar.subject',true),'')::uuid)
    OR
    (current_setting('roghyar.kind',true) = 'patient' AND session_id IS NULL
        AND patient_id IN (SELECT id FROM roghyar.patients))
)"""
CONSTRAINT = "ck_assessments_live_or_anonymized"


def replace_constraint(expression):
    op.drop_constraint(CONSTRAINT, "assessments", schema="roghyar", type_="check")
    op.create_check_constraint(CONSTRAINT, "assessments", expression, schema="roghyar")


def upgrade():
    op.execute("""
        DO $role$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'roghyar_assessment_writer') THEN
            CREATE ROLE roghyar_assessment_writer LOGIN NOINHERIT NOBYPASSRLS;
          END IF;
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'roghyar_assessment_writer'
            AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb
                 OR rolreplication OR rolinherit OR NOT rolcanlogin))
            OR EXISTS (SELECT FROM pg_auth_members WHERE member = 'roghyar_assessment_writer'::regrole)
          THEN RAISE EXCEPTION 'Unexpected assessment writer role attributes or memberships';
          END IF;
        END $role$
    """)
    replace_constraint(f"{LEGACY} OR {SCRUBBED} OR {ENCRYPTED}")
    op.execute("GRANT USAGE ON SCHEMA roghyar TO roghyar_assessment_writer")
    op.execute("GRANT SELECT (id,auth_subject,phone_verified_at) ON roghyar.patients TO roghyar_assessment_writer")
    op.execute("""
        CREATE POLICY read_assessment_writer ON roghyar.patients FOR SELECT TO roghyar_assessment_writer
        USING (current_setting('roghyar.kind',true) = 'patient' AND phone_verified_at IS NOT NULL
               AND auth_subject = nullif(current_setting('roghyar.subject',true),'')::uuid)
    """)
    op.execute("""
        GRANT SELECT (id,patient_id,session_id,created_at,text_ciphertext,answers_ciphertext,
            safety_details_ciphertext,anonymized_at,age,severity,duration_hours,more_than_one_year,language),
            INSERT (id,patient_id,session_id,text_ciphertext,answers_ciphertext,safety_details_ciphertext)
        ON roghyar.assessments TO roghyar_assessment_writer
    """)
    op.execute(f"""
        CREATE POLICY read_assessment_writer ON roghyar.assessments FOR SELECT TO roghyar_assessment_writer
        USING ({OWNER})
    """)
    op.execute(f"""
        CREATE POLICY insert_assessment_writer ON roghyar.assessments FOR INSERT TO roghyar_assessment_writer
        WITH CHECK ({OWNER} AND {ENCRYPTED})
    """)


def downgrade():
    # Constraint validation deliberately refuses rollback with live encrypted drafts.
    replace_constraint(f"{LEGACY} OR {SCRUBBED}")
    op.execute("DROP POLICY insert_assessment_writer ON roghyar.assessments")
    op.execute("DROP POLICY read_assessment_writer ON roghyar.assessments")
    op.execute("DROP POLICY read_assessment_writer ON roghyar.patients")
    op.execute("""
        REVOKE SELECT (id,patient_id,session_id,created_at,text_ciphertext,answers_ciphertext,
            safety_details_ciphertext,anonymized_at,age,severity,duration_hours,more_than_one_year,language),
            INSERT (id,patient_id,session_id,text_ciphertext,answers_ciphertext,safety_details_ciphertext)
        ON roghyar.assessments FROM roghyar_assessment_writer
    """)
    op.execute("REVOKE SELECT (id,auth_subject,phone_verified_at) ON roghyar.patients FROM roghyar_assessment_writer")
    op.execute("REVOKE USAGE ON SCHEMA roghyar FROM roghyar_assessment_writer")
