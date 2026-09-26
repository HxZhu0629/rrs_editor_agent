import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recommendations', '0001_initial'),
        ('reviewers', '0003_reviewerpaper_evidence_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='recommendationitem',
            name='rerank_score',
            field=models.FloatField(default=0.0),
        ),
        migrations.CreateModel(
            name='RecommendationEvidence',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('recall_route', models.CharField(default='sparse', max_length=32)),
                ('retrieval_score', models.FloatField(default=0.0)),
                ('rerank_score', models.FloatField(default=0.0)),
                ('evidence_text', models.TextField(blank=True, default='')),
                (
                    'item',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='evidence',
                        to='recommendations.recommendationitem',
                    ),
                ),
                (
                    'paper',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='reviewers.reviewerpaper',
                    ),
                ),
            ],
            options={
                'db_table': 'recommendation_evidence',
                'ordering': ['-rerank_score', '-retrieval_score'],
            },
        ),
    ]
