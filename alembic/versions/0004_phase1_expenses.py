"""Phase 1: categories, subcategories, expenses, audit_log, plus the category seed.

The seed is copied into this file on purpose: a migration must keep working even if the app's own
lists change later. Urdu labels need a native-speaker review before release.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# (slug, name_en, name_ur, is_charity)  -- sort_order is the position in this list, starting at 1
CATEGORIES = [
    ("food",          "Food",              "خوراک",           False),
    ("transport",     "Transport",         "آمد و رفت",       False),
    ("bills",         "Bills & utilities", "بل",              False),
    ("subscriptions", "Subscriptions",     "سبسکرپشنز",       False),
    ("education",     "Education",         "تعلیم",           False),
    ("health",        "Health",            "صحت",             False),
    ("charity",       "Charity",           "صدقہ و خیرات",    True),
    ("household",     "Household",         "گھریلو اخراجات",  False),
    ("clothing",      "Clothing",          "لباس",            False),
    ("family",        "Family",            "خاندان",          False),
    ("entertainment", "Entertainment",     "تفریح",           False),
    ("financial",     "Banking & finance", "مالی و بینک",     False),
    ("other",         "Other",             "دیگر",            False),
]

# (category slug, slug, name_en, name_ur, charity_type, default_flag)  -- sort_order restarts at 1 per category
SUBCATEGORIES = [
    ("food", "food.groceries", "Groceries", "راشن", None, None),
    ("food", "food.dining_out", "Dining out", "باہر کھانا", None, None),
    ("food", "food.tea_snacks", "Tea & snacks", "چائے اور ناشتہ", None, None),
    ("transport", "transport.fuel", "Fuel", "ایندھن", None, None),
    ("transport", "transport.ride_hailing", "Cab / ride-hailing", "کیب", None, None),
    ("transport", "transport.public", "Public transport", "پبلک ٹرانسپورٹ", None, None),
    ("transport", "transport.vehicle_maintenance", "Vehicle maintenance", "گاڑی کی مرمت", None, None),
    ("bills", "bills.electricity", "Electricity", "بجلی", None, None),
    ("bills", "bills.gas", "Gas", "گیس", None, None),
    ("bills", "bills.water", "Water", "پانی", None, None),
    ("bills", "bills.internet", "Internet", "انٹرنیٹ", None, None),
    ("bills", "bills.mobile", "Mobile", "موبائل", None, None),
    ("bills", "bills.rent", "Rent", "کرایہ", None, None),
    ("subscriptions", "subscriptions.streaming", "Streaming", "سٹریمنگ", None, None),
    ("subscriptions", "subscriptions.software", "Software & apps", "سافٹ ویئر", None, None),
    ("subscriptions", "subscriptions.memberships", "Memberships", "ممبرشپ", None, None),
    ("education", "education.fees", "Fees", "فیس", None, None),
    ("education", "education.books", "Books & supplies", "کتابیں", None, None),
    ("education", "education.courses", "Courses", "کورسز", None, None),
    ("health", "health.doctor", "Doctor", "ڈاکٹر", None, None),
    ("health", "health.medicine", "Medicine", "دوائیں", None, None),
    ("health", "health.tests", "Tests", "ٹیسٹ", None, None),
    ("charity", "charity.zakat", "Zakat", "زکوٰۃ", "zakat", None),
    ("charity", "charity.sadaqah", "Sadaqah", "صدقہ", "sadaqah", None),
    ("charity", "charity.fitrana", "Fitrana", "فطرانہ", "fitrana", None),
    ("charity", "charity.other", "Other donations", "دیگر عطیات", "other", None),
    ("household", "household.repairs", "Repairs", "مرمت", None, None),
    ("household", "household.supplies", "Supplies", "گھریلو سامان", None, None),
    ("clothing", "clothing.clothes", "Clothes", "کپڑے", None, None),
    ("clothing", "clothing.tailoring", "Tailoring", "سلائی", None, None),
    ("family", "family.children", "Children", "بچے", None, None),
    ("family", "family.gifts", "Gifts", "تحائف", None, None),
    ("entertainment", "entertainment.outings", "Outings", "سیر و تفریح", None, None),
    ("financial", "financial.bank_fees", "Bank fees", "بینک چارجز", None, None),
    ("financial", "financial.loan_installment", "Loan installment", "قرض کی قسط", None, "doubtful"),
    ("financial", "financial.markup_interest", "Markup / interest", "سود / مارک اپ", None, "riba"),
    ("financial", "financial.late_charge", "Late-payment charge", "تاخیری جرمانہ", None, "riba"),
    ("financial", "financial.insurance", "Insurance (conventional)", "انشورنس", None, "doubtful"),
    ("financial", "financial.takaful", "Takaful contribution", "تکافل", None, None),
    ("other", "other.other", "Other", "دیگر", None, None),
]

_ACTIVE = sa.text("deleted_at IS NULL")


def upgrade() -> None:
    small = sa.SmallInteger().with_variant(sa.Integer(), "sqlite")
    big = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

    op.create_table(
        "categories",
        sa.Column("id", small, primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("name_en", sa.String(), nullable=False),
        sa.Column("name_ur", sa.String(), nullable=False),
        sa.Column("is_charity", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "subcategories",
        sa.Column("id", small, primary_key=True, autoincrement=True),
        sa.Column("category_id", sa.SmallInteger(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("name_en", sa.String(), nullable=False),
        sa.Column("name_ur", sa.String(), nullable=False),
        sa.Column("charity_type", sa.String()),
        sa.Column("default_flag", sa.String()),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("id", "category_id", name="uq_subcategories_id_category"),
        sa.CheckConstraint("charity_type IN ('zakat','sadaqah','fitrana','other')", name="ck_subcategories_charity_type"),
        sa.CheckConstraint("default_flag IN ('riba','doubtful')", name="ck_subcategories_default_flag"),
    )
    op.create_table(
        "expenses",
        sa.Column("id", big, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("amount_paisa", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("category_id", sa.SmallInteger(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("subcategory_id", sa.SmallInteger()),
        sa.Column("note", sa.Text()),
        sa.Column("payment_method", sa.String(), nullable=False, server_default="cash"),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("shariah_flag", sa.String()),
        sa.Column("shariah_flag_dismissed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["subcategory_id", "category_id"], ["subcategories.id", "subcategories.category_id"],
                                name="fk_expenses_subcategory_category"),
        sa.UniqueConstraint("user_id", "client_id", name="uq_expenses_user_client"),
        sa.CheckConstraint("amount_paisa BETWEEN 1 AND 1000000000000", name="ck_expenses_amount"),
        sa.CheckConstraint("currency = 'PKR'", name="ck_expenses_currency"),
        sa.CheckConstraint("occurred_on >= '2000-01-01'", name="ck_expenses_date_floor"),
        sa.CheckConstraint("length(note) <= 500", name="ck_expenses_note_len"),
        sa.CheckConstraint("payment_method IN ('cash','bank_transfer','card','wallet','other')", name="ck_expenses_payment_method"),
        sa.CheckConstraint("source IN ('manual','ai_chat','whatsapp','pwa_sync')", name="ck_expenses_source"),
        sa.CheckConstraint("shariah_flag IN ('riba','doubtful')", name="ck_expenses_shariah_flag"),
    )
    where = {"postgresql_where": _ACTIVE, "sqlite_where": _ACTIVE}
    op.create_index("ix_exp_user_date", "expenses", ["user_id", sa.text("occurred_on DESC"), sa.text("id DESC")], **where)
    op.create_index("ix_exp_user_cat_date", "expenses", ["user_id", "category_id", sa.text("occurred_on DESC")], **where)
    sub_where = sa.text("deleted_at IS NULL AND subcategory_id IS NOT NULL")
    op.create_index("ix_exp_user_sub_date", "expenses", ["user_id", "subcategory_id", "occurred_on"],
                    postgresql_where=sub_where, sqlite_where=sub_where)
    purge_where = sa.text("deleted_at IS NOT NULL")
    op.create_index("ix_exp_purge", "expenses", ["deleted_at"], postgresql_where=purge_where, sqlite_where=purge_where)

    op.create_table(
        "audit_log",
        sa.Column("id", big, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String()),
        sa.Column("entity_id", sa.String()),
        sa.Column("detail", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("actor IN ('user','ai','whatsapp','admin','system')", name="ck_audit_actor"),
    )
    op.create_index("ix_audit_user_time", "audit_log", ["user_id", sa.text("created_at DESC")])

    # ---- seed (no explicit ids, so the Postgres id counters stay correct) ----
    categories = sa.table("categories", sa.column("slug"), sa.column("name_en"), sa.column("name_ur"),
                          sa.column("is_charity"), sa.column("sort_order"))
    op.bulk_insert(categories, [
        {"slug": s, "name_en": en, "name_ur": ur, "is_charity": ch, "sort_order": i}
        for i, (s, en, ur, ch) in enumerate(CATEGORIES, start=1)
    ])
    ids = dict(op.get_bind().execute(sa.text("SELECT slug, id FROM categories")).all())

    subcategories = sa.table("subcategories", sa.column("category_id"), sa.column("slug"), sa.column("name_en"),
                             sa.column("name_ur"), sa.column("charity_type"), sa.column("default_flag"),
                             sa.column("sort_order"))
    position: dict[str, int] = {}
    rows = []
    for cat, slug, en, ur, charity, flag in SUBCATEGORIES:
        position[cat] = position.get(cat, 0) + 1
        rows.append({"category_id": ids[cat], "slug": slug, "name_en": en, "name_ur": ur,
                     "charity_type": charity, "default_flag": flag, "sort_order": position[cat]})
    op.bulk_insert(subcategories, rows)


def downgrade() -> None:
    op.drop_index("ix_audit_user_time", "audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_exp_purge", "expenses")
    op.drop_index("ix_exp_user_sub_date", "expenses")
    op.drop_index("ix_exp_user_cat_date", "expenses")
    op.drop_index("ix_exp_user_date", "expenses")
    op.drop_table("expenses")
    op.drop_table("subcategories")
    op.drop_table("categories")
