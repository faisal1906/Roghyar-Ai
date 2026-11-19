"""Enable PostGIS and establish the foundation revision."""

from alembic import op

revision = "0001_postgis"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    # The database image or managed service may already own this extension.
    # Leave it installed so a downgrade cannot affect unrelated objects.
    pass
