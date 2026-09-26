from django.db import models
from pgvector.django import VectorField

from apps.common.models import UUIDTimestampModel
from apps.manuscripts.models import Affiliation


class ReviewerProfile(UUIDTimestampModel):
    name = models.CharField(max_length=150)
    email = models.EmailField()
    affiliation = models.ForeignKey(
        Affiliation,
        on_delete=models.SET_NULL,
        related_name='reviewers',
        blank=True,
        null=True,
        default=None,
    )
    expertise_keywords = models.JSONField(default=list, blank=True)
    expertise_text = models.TextField(blank=True, default='')
    embedding_vector = models.JSONField(default=list, blank=True)
    embedding_model = models.CharField(max_length=128, blank=True, default='')
    embedding_updated_at = models.DateTimeField(blank=True, null=True, default=None)
    workload_score = models.FloatField(default=0.0)
    recent_publication_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'reviewer_profiles'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['name', 'email'], name='uq_reviewer_name_email')
        ]
        indexes = [
            models.Index(fields=['is_active']),
        ]

    def __str__(self) -> str:
        return f'{self.name} <{self.email}>'


class ReviewerPaper(UUIDTimestampModel):
    reviewer = models.ForeignKey(ReviewerProfile, on_delete=models.CASCADE, related_name='papers')
    title = models.CharField(max_length=512)
    abstract = models.TextField(blank=True, default='')
    keywords = models.JSONField(default=list, blank=True)
    publication_year = models.PositiveSmallIntegerField(blank=True, null=True, default=None)
    doi = models.CharField(max_length=255, blank=True, default='')
    source_id = models.CharField(max_length=255, blank=True, default='')
    venue = models.CharField(max_length=255, blank=True, default='')
    publication_date = models.DateField(blank=True, null=True, default=None)
    embedding_vector = models.JSONField(default=list, blank=True)
    embedding = VectorField(dimensions=768, blank=True, null=True, default=None)
    embedding_model = models.CharField(max_length=128, blank=True, default='')
    embedding_updated_at = models.DateTimeField(blank=True, null=True, default=None)

    class Meta:
        db_table = 'reviewer_papers'
        ordering = ['-publication_year', '-created_at']
        indexes = [
            models.Index(fields=['publication_year'], name='reviewer_pa_publica_9de806_idx'),
            models.Index(fields=['source_id'], name='reviewer_paper_source_idx'),
        ]


class ReviewerCoauthorEdge(UUIDTimestampModel):
    reviewer = models.ForeignKey(ReviewerProfile, on_delete=models.CASCADE, related_name='coauthor_edges')
    author_name = models.CharField(max_length=150)
    normalized_author_name = models.CharField(max_length=150)
    last_coauthor_year = models.PositiveSmallIntegerField()

    class Meta:
        db_table = 'reviewer_coauthor_edges'
        constraints = [
            models.UniqueConstraint(fields=['reviewer', 'normalized_author_name'], name='uq_reviewer_coauthor_name')
        ]
        indexes = [
            models.Index(fields=['last_coauthor_year']),
        ]
