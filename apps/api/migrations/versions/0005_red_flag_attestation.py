"""Require a versioned attestation envelope for active red-flag candidates."""

from alembic import op

revision = "0005_red_flag_attestation"
down_revision = "0004_assessments"
branch_labels = None
depends_on = None

NAME = "ck_red_flag_active_attestation"
CHECK = """NOT active OR (
    pattern->'schema_version' = '1'::jsonb
    AND jsonb_typeof(pattern->'attestation') = 'object'
    AND jsonb_typeof(pattern->'attestation'->'key_id') = 'string'
    AND jsonb_typeof(pattern->'attestation'->'signature') = 'string'
    AND pattern->'attestation'->>'signature' ~ '^[0-9a-f]{128}$'
) IS TRUE"""


def upgrade():
    op.create_check_constraint(NAME, "red_flag_rules", CHECK, schema="roghyar")


def downgrade():
    op.drop_constraint(NAME, "red_flag_rules", schema="roghyar", type_="check")
