from django.db import models

from apps.common.models import UUIDTimestampModel
from apps.manuscripts.models import Manuscript
from apps.reviewers.models import ReviewerProfile


class RecommendationRun(UUIDTimestampModel):
    class Status(models.TextChoices):
        PROCESSING = 'processing', 'Processing'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    manuscript = models.ForeignKey(Manuscript, on_delete=models.CASCADE, related_name='recommendation_runs')
    top_k = models.PositiveSmallIntegerField(default=20)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROCESSING)
    summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'recommendation_runs'
        ordering = ['-created_at']


class RecommendationItem(UUIDTimestampModel):
    run = models.ForeignKey(RecommendationRun, on_delete=models.CASCADE, related_name='items')
    reviewer = models.ForeignKey(ReviewerProfile, on_delete=models.CASCADE, related_name='+')
    rank = models.PositiveSmallIntegerField()
    score = models.FloatField(default=0.0)
    match_score = models.FloatField(default=0.0)
    activity_score = models.FloatField(default=0.0)
    workload_penalty = models.FloatField(default=0.0)
    rerank_score = models.FloatField(default=0.0)
    rationale = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'recommendation_items'
        ordering = ['rank']
        constraints = [
            models.UniqueConstraint(fields=['run', 'reviewer'], name='uq_run_reviewer'),
            models.UniqueConstraint(fields=['run', 'rank'], name='uq_run_rank'),
        ]


class RecommendationEvidence(UUIDTimestampModel):
    item = models.ForeignKey(RecommendationItem, on_delete=models.CASCADE, related_name='evidence')
    paper = models.ForeignKey('reviewers.ReviewerPaper', on_delete=models.CASCADE, related_name='+')
    recall_route = models.CharField(max_length=32, default='sparse')
    retrieval_score = models.FloatField(default=0.0)
    rerank_score = models.FloatField(default=0.0)
    evidence_text = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'recommendation_evidence'
        ordering = ['-rerank_score', '-retrieval_score']
