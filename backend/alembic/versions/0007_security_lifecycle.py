"""Add privacy revisions and make new completion email subscriptions opt-in."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("memory_revision", "telegram_link_revision"):
        op.add_column("users", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("users", "completion_email_opt_in", server_default=sa.false())
    # Prior true values could come from the old default. Collect consent again.
    op.execute("UPDATE users SET completion_email_opt_in = false WHERE completion_email_opt_in")
    # Telegram group and channel IDs are negative. Require a new private link.
    op.execute("UPDATE users SET telegram_chat_id = NULL WHERE telegram_chat_id < 0")


def downgrade() -> None:
    op.alter_column("users", "completion_email_opt_in", server_default=sa.true())
    op.drop_column("users", "telegram_link_revision")
    op.drop_column("users", "memory_revision")
