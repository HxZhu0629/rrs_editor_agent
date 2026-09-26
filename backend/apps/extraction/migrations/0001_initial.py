
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
            name="ExtractionRun",
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
                ("model_name", models.CharField(max_length=128)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("success", "Success"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("raw_response", models.JSONField(blank=True, default=dict)),
                ("parsed_json", models.JSONField(blank=True, default=dict)),
                ("confidence", models.FloatField(default=0.0)),
                ("error_message", models.TextField(blank=True, default="")),
                (
                    "manuscript",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="extraction_runs",
                        to="manuscripts.manuscript",
                    ),
                ),
            ],
            options={
                "db_table": "extraction_runs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ExtractionReview",
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
                ("reviewed_json", models.JSONField(blank=True, default=dict)),
                (
                    "reviewer_name",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("is_confirmed", models.BooleanField(default=False)),
                (
                    "extraction_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reviews",
                        to="extraction.extractionrun",
                    ),
                ),
            ],
            options={
                "db_table": "extraction_reviews",
                "ordering": ["-created_at"],
            },
        ),
    ]
