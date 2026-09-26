
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("manuscripts", "0001_initial"),
        ("reviewers", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecommendationRun",
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
                ("top_k", models.PositiveSmallIntegerField(default=20)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("processing", "Processing"),
                            ("success", "Success"),
                            ("failed", "Failed"),
                        ],
                        default="processing",
                        max_length=16,
                    ),
                ),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                (
                    "manuscript",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recommendation_runs",
                        to="manuscripts.manuscript",
                    ),
                ),
            ],
            options={
                "db_table": "recommendation_runs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="RecommendationItem",
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
                ("rank", models.PositiveSmallIntegerField()),
                ("score", models.FloatField(default=0.0)),
                ("match_score", models.FloatField(default=0.0)),
                ("activity_score", models.FloatField(default=0.0)),
                ("workload_penalty", models.FloatField(default=0.0)),
                ("rationale", models.JSONField(blank=True, default=dict)),
                (
                    "reviewer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="reviewers.reviewerprofile",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="recommendations.recommendationrun",
                    ),
                ),
            ],
            options={
                "db_table": "recommendation_items",
                "ordering": ["rank"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("run", "reviewer"), name="uq_run_reviewer"
                    ),
                    models.UniqueConstraint(fields=("run", "rank"), name="uq_run_rank"),
                ],
            },
        ),
    ]
