"""version_lsh: LSH bands of every version's MinHash (near-duplicate candidates by index lookup)

Existing versions are backfilled by the one-off `lsh_backfill` job enqueued here; the ingest uses the exhaustive
check until the explicit `lsh_backfilled` marker exists (written by the last batch, never by a failed one), so recall
never drops during the upgrade.

Revision ID: 0014e
Revises: 0014d
"""

import sqlalchemy as sa
from alembic import op

revision = "0014e"
down_revision = "0014d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "version_lsh",
        sa.Column("band", sa.SmallInteger, nullable=False),
        sa.Column("hash", sa.BigInteger, nullable=False),
        sa.Column(
            "version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("band", "hash", "version_id"),
    )
    op.create_index(
        "ix_version_lsh_version_id", "version_lsh", ["version_id"]
    )  # cascade deletes, band rewrites
    op.execute(
        "INSERT INTO jobs (kind, payload) SELECT 'lsh_backfill', '{}'::jsonb"
        " WHERE EXISTS (SELECT 1 FROM document_versions WHERE minhash IS NOT NULL)"
    )
    # nothing to backfill (fresh install): the LSH path is ready at once; otherwise the LAST batch writes the marker
    op.execute(
        "INSERT INTO app_settings (key, value) SELECT 'lsh_backfilled', 'true'::jsonb"
        " WHERE NOT EXISTS (SELECT 1 FROM document_versions WHERE minhash IS NOT NULL)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'lsh_backfill'")
    op.execute("DELETE FROM app_settings WHERE key = 'lsh_backfilled'")
    op.drop_table("version_lsh")
