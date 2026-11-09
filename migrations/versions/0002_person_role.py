"""Store a bot role for each person."""

from alembic import op

revision = "0002_person_role"
down_revision = "0001_people"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE people ADD COLUMN role TEXT NOT NULL DEFAULT 'user'
        CONSTRAINT ck_people_role CHECK (role IN ('user', 'moderator', 'admin'))
        """
    )


def downgrade() -> None:
    op.drop_column("people", "role")
