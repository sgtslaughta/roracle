"""document_coauthors: co-author read grant on published papers (+ backfill, is_latest repair)

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "document_coauthors",
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), primary_key=True),
    )
    op.create_index("ix_document_coauthors_user_id", "document_coauthors", ["user_id"])
    # Backfill: before this, publish wrote co-authors as shares (inert unless `shared`). Re-grant from the
    # drafts of published papers, then queue an acl_sync per affected document so ES gets coauthor_users.
    op.execute(
        """
        WITH ins AS (
            INSERT INTO document_coauthors (document_id, user_id)
            SELECT DISTINCT d.document_id, u.id
            FROM drafts d
            JOIN documents doc ON doc.id = d.document_id AND doc.deleted_at IS NULL
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(d.coauthors->'users') = 'array' THEN d.coauthors->'users' ELSE '[]'::jsonb END
            ) AS c(val)
            JOIN users u ON u.id::text = lower(c.val) AND u.id <> doc.owner_id
            WHERE (d.submitted_version IS NOT NULL OR EXISTS (
                SELECT 1 FROM document_versions v WHERE v.document_id = d.document_id AND v.stage = 'searchable'))
            ON CONFLICT DO NOTHING
            RETURNING document_id
        )
        INSERT INTO jobs (kind, payload)
        SELECT 'acl_sync', jsonb_build_object('doc_id', document_id::text) FROM (SELECT DISTINCT document_id FROM ins) x
        """
    )
    # is_latest: exactly one per document = its highest searchable version (documents with none keep theirs).
    op.execute(
        """
        UPDATE document_versions v SET is_latest = (v.stage = 'searchable' AND v.version = m.top)
        FROM (SELECT document_id, max(version) AS top FROM document_versions
              WHERE stage = 'searchable' GROUP BY document_id) m
        WHERE v.document_id = m.document_id AND v.is_latest IS DISTINCT FROM (v.stage = 'searchable' AND v.version = m.top)
        """
    )
    op.alter_column("document_versions", "is_latest", server_default=sa.false())


def downgrade() -> None:
    op.alter_column("document_versions", "is_latest", server_default=sa.true())
    op.drop_table("document_coauthors")
