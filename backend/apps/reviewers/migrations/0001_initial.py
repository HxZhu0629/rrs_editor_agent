
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("manuscripts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReviewerProfile",
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
                ("email", models.EmailField(max_length=254)),
                ("expertise_keywords", models.JSONField(blank=True, default=list)),
                ("expertise_text", models.TextField(blank=True, default="")),
                ("workload_score", models.FloatField(default=0.0)),
                ("recent_publication_count", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "affiliation",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewers",
                        to="manuscripts.affiliation",
                    ),
                ),
            ],
            options={
                "db_table": "reviewer_profiles",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="ReviewerPaper",
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
                ("title", models.CharField(max_length=512)),
                ("abstract", models.TextField(blank=True, default="")),
                ("keywords", models.JSONField(blank=True, default=list)),
                (
                    "publication_year",
                    models.PositiveSmallIntegerField(
                        blank=True, default=None, null=True
                    ),
                ),
                (
                    "reviewer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="papers",
                        to="reviewers.reviewerprofile",
                    ),
                ),
            ],
            options={
                "db_table": "reviewer_papers",
                "ordering": ["-publication_year", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ReviewerCoauthorEdge",
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
                ("author_name", models.CharField(max_length=150)),
                ("normalized_author_name", models.CharField(max_length=150)),
                ("last_coauthor_year", models.PositiveSmallIntegerField()),
                (
                    "reviewer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="coauthor_edges",
                        to="reviewers.reviewerprofile",
                    ),
                ),
            ],
            options={
                "db_table": "reviewer_coauthor_edges",
            },
        ),
        migrations.AddIndex(
            model_name="reviewerprofile",
            index=models.Index(
                fields=["is_active"], name="reviewer_pr_is_acti_55f421_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="reviewerprofile",
            constraint=models.UniqueConstraint(
                fields=("name", "email"), name="uq_reviewer_name_email"
            ),
        ),
        migrations.AddIndex(
            model_name="reviewercoauthoredge",
            index=models.Index(
                fields=["last_coauthor_year"], name="reviewer_co_last_co_633b51_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="reviewercoauthoredge",
            constraint=models.UniqueConstraint(
                fields=("reviewer", "normalized_author_name"),
                name="uq_reviewer_coauthor_name",
            ),
        ),
    ]
