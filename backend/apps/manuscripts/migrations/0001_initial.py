
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Affiliation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("normalized_name", models.CharField(max_length=255, unique=True)),
                ("country", models.CharField(blank=True, default="", max_length=64)),
            ],
            options={
                "db_table": "affiliations",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Manuscript",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("domain", models.CharField(default="", max_length=128)),
                ("title", models.CharField(default="", max_length=512)),
                ("abstract", models.TextField(blank=True, default="")),
                ("keywords", models.JSONField(blank=True, default=list)),
                (
                    "source_pdf",
                    models.FileField(
                        blank=True, null=True, upload_to="manuscripts/%Y/%m/%d/"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("extracting", "Extracting"),
                            ("review_pending", "Review Pending"),
                            ("confirmed", "Confirmed"),
                            ("recommended", "Recommended"),
                            ("failed", "Failed"),
                        ],
                        default="draft",
                        max_length=32,
                    ),
                ),
                (
                    "extracted_at",
                    models.DateTimeField(blank=True, default=None, null=True),
                ),
                (
                    "confirmed_at",
                    models.DateTimeField(blank=True, default=None, null=True),
                ),
            ],
            options={
                "db_table": "manuscripts",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="Author",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=150)),
                ("normalized_name", models.CharField(max_length=150)),
                (
                    "email",
                    models.EmailField(
                        blank=True, default=None, max_length=254, null=True
                    ),
                ),
                (
                    "orcid",
                    models.CharField(
                        blank=True, default=None, max_length=32, null=True
                    ),
                ),
            ],
            options={
                "db_table": "authors",
                "ordering": ["name"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("orcid__isnull", False)),
                        fields=("orcid",),
                        name="uq_author_orcid_notnull",
                    ),
                    models.UniqueConstraint(
                        fields=("normalized_name", "email"), name="uq_author_name_email"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ManuscriptAuthor",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("author_order", models.PositiveSmallIntegerField(default=1)),
                ("is_corresponding", models.BooleanField(default=False)),
                ("is_co_first", models.BooleanField(default=False)),
                (
                    "contact_email",
                    models.EmailField(
                        blank=True, default=None, max_length=254, null=True
                    ),
                ),
                (
                    "affiliation",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="manuscript_author_links",
                        to="manuscripts.affiliation",
                    ),
                ),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="manuscript_links",
                        to="manuscripts.author",
                    ),
                ),
                (
                    "manuscript",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="author_links",
                        to="manuscripts.manuscript",
                    ),
                ),
            ],
            options={
                "db_table": "manuscript_authorships",
                "ordering": ["author_order"],
            },
        ),
        migrations.AddField(
            model_name="manuscript",
            name="authors",
            field=models.ManyToManyField(
                related_name="manuscripts",
                through="manuscripts.ManuscriptAuthor",
                to="manuscripts.author",
            ),
        ),
        migrations.AddIndex(
            model_name="manuscriptauthor",
            index=models.Index(
                fields=["manuscript", "author_order"],
                name="manuscript__manuscr_97b0ee_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="manuscriptauthor",
            index=models.Index(
                fields=["is_corresponding"], name="manuscript__is_corr_a21ed6_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="manuscriptauthor",
            constraint=models.UniqueConstraint(
                fields=("manuscript", "author"), name="uq_manuscript_author_pair"
            ),
        ),
        migrations.AddConstraint(
            model_name="manuscriptauthor",
            constraint=models.UniqueConstraint(
                fields=("manuscript", "author_order"), name="uq_manuscript_author_order"
            ),
        ),
        migrations.AddIndex(
            model_name="manuscript",
            index=models.Index(fields=["status"], name="manuscripts_status_572e09_idx"),
        ),
        migrations.AddIndex(
            model_name="manuscript",
            index=models.Index(fields=["domain"], name="manuscripts_domain_417ce0_idx"),
        ),
        migrations.AddIndex(
            model_name="manuscript",
            index=models.Index(
                fields=["created_at"], name="manuscripts_created_81e562_idx"
            ),
        ),
    ]
