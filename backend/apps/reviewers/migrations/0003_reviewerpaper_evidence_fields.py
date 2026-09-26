from django.db import migrations, models
import pgvector.django.vector


def enable_vector_extension(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('CREATE EXTENSION IF NOT EXISTS vector')


def create_vector_index(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            'CREATE INDEX IF NOT EXISTS reviewer_paper_embedding_hnsw '
            'ON reviewer_papers USING hnsw (embedding vector_cosine_ops) '
            'WITH (m = 16, ef_construction = 64)'
        )


def drop_vector_index(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP INDEX IF EXISTS reviewer_paper_embedding_hnsw')


class Migration(migrations.Migration):

    dependencies = [
        ('reviewers', '0002_reviewerprofile_embedding_fields'),
    ]

    operations = [
        migrations.RunPython(enable_vector_extension, migrations.RunPython.noop),
        migrations.AddField(
            model_name='reviewerpaper',
            name='doi',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='reviewerpaper',
            name='source_id',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='reviewerpaper',
            name='venue',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='reviewerpaper',
            name='publication_date',
            field=models.DateField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='reviewerpaper',
            name='embedding_vector',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='reviewerpaper',
            name='embedding',
            field=pgvector.django.vector.VectorField(blank=True, default=None, dimensions=768, null=True),
        ),
        migrations.AddField(
            model_name='reviewerpaper',
            name='embedding_model',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='reviewerpaper',
            name='embedding_updated_at',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddIndex(
            model_name='reviewerpaper',
            index=models.Index(fields=['publication_year'], name='reviewer_pa_publica_9de806_idx'),
        ),
        migrations.AddIndex(
            model_name='reviewerpaper',
            index=models.Index(fields=['source_id'], name='reviewer_paper_source_idx'),
        ),
        migrations.RunPython(create_vector_index, drop_vector_index),
    ]
