
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reviewers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="reviewerprofile",
            name="embedding_model",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="reviewerprofile",
            name="embedding_updated_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="reviewerprofile",
            name="embedding_vector",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
