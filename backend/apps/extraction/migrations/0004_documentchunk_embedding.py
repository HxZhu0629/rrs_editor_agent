import pgvector.django.vector
from django.db import migrations, models


def enable_vector_extension(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('CREATE EXTENSION IF NOT EXISTS vector')


def create_chunk_vector_index(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            'CREATE INDEX IF NOT EXISTS document_chunk_embedding_hnsw '
            'ON document_chunks USING hnsw (embedding vector_cosine_ops) '
            'WITH (m = 16, ef_construction = 64)'
        )


def drop_chunk_vector_index(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP INDEX IF EXISTS document_chunk_embedding_hnsw')


class Migration(migrations.Migration):
    dependencies = [
        ('extraction', '0003_documentchunk'),
    ]

    operations = [
        migrations.RunPython(enable_vector_extension, migrations.RunPython.noop),
        migrations.AddField(
            model_name='documentchunk',
            name='embedding',
            field=pgvector.django.vector.VectorField(blank=True, default=None, dimensions=768, null=True),
        ),
        migrations.RunPython(create_chunk_vector_index, drop_chunk_vector_index),
    ]
