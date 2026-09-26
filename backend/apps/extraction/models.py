from django.db import models
from pgvector.django import VectorField

from apps.common.models import UUIDTimestampModel
from apps.manuscripts.models import Manuscript


class ExtractionRun(UUIDTimestampModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    manuscript = models.ForeignKey(Manuscript, on_delete=models.CASCADE, related_name='extraction_runs')
    model_name = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    raw_response = models.JSONField(default=dict, blank=True)
    parsed_json = models.JSONField(default=dict, blank=True)
    parser_name = models.CharField(max_length=64, blank=True, default='')
    document_markdown = models.TextField(blank=True, default='')
    document_blocks = models.JSONField(default=list, blank=True)
    document_quality = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(default=0.0)
    error_message = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'extraction_runs'
        ordering = ['-created_at']


class DocumentChunk(UUIDTimestampModel):
    class Kind(models.TextChoices):
        PARENT = 'parent', 'Parent'
        CHILD = 'child', 'Child'

    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.CASCADE,
        related_name='document_chunks',
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        related_name='children',
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    chunk_index = models.PositiveIntegerField()
    section_title = models.CharField(max_length=512, blank=True, default='')
    content = models.TextField()
    token_count = models.PositiveIntegerField(default=0)
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    source_block_indexes = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    embedding_vector = models.JSONField(default=list, blank=True)
    embedding = VectorField(dimensions=768, blank=True, null=True, default=None)
    embedding_model = models.CharField(max_length=128, blank=True, default='')
    embedding_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'document_chunks'
        ordering = ['kind', 'chunk_index']
        constraints = [
            models.UniqueConstraint(
                fields=['extraction_run', 'kind', 'chunk_index'],
                name='uq_extraction_chunk_kind_index',
            ),
        ]
        indexes = [
            models.Index(fields=['extraction_run', 'kind'], name='idx_chunk_run_kind'),
            models.Index(fields=['parent', 'chunk_index'], name='idx_chunk_parent_index'),
        ]


class ExtractionReview(UUIDTimestampModel):
    extraction_run = models.ForeignKey(ExtractionRun, on_delete=models.CASCADE, related_name='reviews')
    reviewed_json = models.JSONField(default=dict, blank=True)
    reviewer_name = models.CharField(max_length=128, blank=True, default='')
    is_confirmed = models.BooleanField(default=False)

    class Meta:
        db_table = 'extraction_reviews'
        ordering = ['-created_at']
