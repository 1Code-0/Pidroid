"""Add message_id column to Reminders

Revision ID: 92b55b3bbe0d
Revises: 08d143919da6
Create Date: 2023-08-24 17:58:46.359057

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '92b55b3bbe0d'
down_revision = '08d143919da6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('Reminders', sa.Column('message_id', sa.BigInteger(), nullable=False))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('Reminders', 'message_id')
    # ### end Alembic commands ###