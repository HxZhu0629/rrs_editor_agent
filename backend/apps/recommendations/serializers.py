from rest_framework import serializers

from .models import RecommendationEvidence, RecommendationItem, RecommendationRun


class RecommendationEvidenceSerializer(serializers.ModelSerializer):
    paper_id = serializers.UUIDField(source='paper.id', read_only=True)
    paper_title = serializers.CharField(source='paper.title', read_only=True)
    publication_year = serializers.IntegerField(source='paper.publication_year', read_only=True)
    doi = serializers.CharField(source='paper.doi', read_only=True)

    class Meta:
        model = RecommendationEvidence
        fields = [
            'id',
            'paper_id',
            'paper_title',
            'publication_year',
            'doi',
            'recall_route',
            'retrieval_score',
            'rerank_score',
            'evidence_text',
        ]


class RecommendationItemSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.CharField(source='reviewer.name')
    reviewer_email = serializers.CharField(source='reviewer.email')
    reviewer_affiliation = serializers.SerializerMethodField()
    evidence = RecommendationEvidenceSerializer(many=True, read_only=True)

    class Meta:
        model = RecommendationItem
        fields = [
            'id',
            'rank',
            'score',
            'match_score',
            'activity_score',
            'workload_penalty',
            'rerank_score',
            'rationale',
            'evidence',
            'reviewer_name',
            'reviewer_email',
            'reviewer_affiliation',
        ]

    def get_reviewer_affiliation(self, obj: RecommendationItem):
        return obj.reviewer.affiliation.name if obj.reviewer.affiliation else ''


class RecommendationRunSerializer(serializers.ModelSerializer):
    items = RecommendationItemSerializer(many=True, read_only=True)

    class Meta:
        model = RecommendationRun
        fields = [
            'id',
            'manuscript',
            'top_k',
            'status',
            'summary',
            'error_message',
            'items',
            'created_at',
            'updated_at',
        ]
