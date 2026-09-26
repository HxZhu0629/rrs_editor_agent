import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('extraction', '0002_extractionrun_document_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentChunk',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('parent', 'Parent'), ('child', 'Child')], max_length=16)),
                ('chunk_index', models.PositiveIntegerField()),
                ('section_title', models.CharField(blank=True, default='', max_length=512)),
                ('content', models.TextField()),
                ('token_count', models.PositiveIntegerField(default=0)),
                ('page_start', models.PositiveIntegerField(blank=True, null=True)),
                ('page_end', models.PositiveIntegerField(blank=True, null=True)),
                ('source_block_indexes', models.JSONField(blank=True, default=list)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('embedding_vector', models.JSONField(blank=True, default=list)),
                ('embedding_model', models.CharField(blank=True, default='', max_length=128)),
                ('embedding_updated_at', models.DateTimeField(blank=True, null=True)),
                ('extraction_run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='document_chunks', to='extraction.extractionrun')),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='extraction.documentchunk')),
            ],
            options={
                'db_table': 'document_chunks',
                'ordering': ['kind', 'chunk_index'],
                'indexes': [
                    models.Index(fields=['extraction_run', 'kind'], name='idx_chunk_run_kind'),
                    models.Index(fields=['parent', 'chunk_index'], name='idx_chunk_parent_index'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('extraction_run', 'kind', 'chunk_index'), name='uq_extraction_chunk_kind_index'),
                ],
            },
        ),
    ]
