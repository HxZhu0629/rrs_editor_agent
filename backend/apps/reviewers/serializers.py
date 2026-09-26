import re

from rest_framework import serializers

from apps.manuscripts.models import Affiliation
from .models import ReviewerCoauthorEdge, ReviewerPaper, ReviewerProfile


def normalize_name(value: str) -> str:
    value = value.strip().lower()
    return re.sub(r'\s+', ' ', value)


class ReviewerPaperSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewerPaper
        fields = ['id', 'title', 'abstract', 'keywords', 'publication_year']


class ReviewerCoauthorEdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewerCoauthorEdge
        fields = ['id', 'author_name', 'last_coauthor_year']


class ReviewerProfileSerializer(serializers.ModelSerializer):
    affiliation_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    affiliation = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReviewerProfile
        fields = [
            'id',
            'name',
            'email',
            'affiliation',
            'affiliation_name',
            'expertise_keywords',
            'expertise_text',
            'workload_score',
            'recent_publication_count',
            'is_active',
            'created_at',
            'updated_at',
        ]

    def get_affiliation(self, obj: ReviewerProfile):
        return obj.affiliation.name if obj.affiliation else ''

    def _resolve_affiliation(self, affiliation_name: str):
        if not affiliation_name:
            return None
        obj, _ = Affiliation.objects.get_or_create(
            normalized_name=normalize_name(affiliation_name),
            defaults={'name': affiliation_name},
        )
        return obj

    def create(self, validated_data):
        affiliation_name = validated_data.pop('affiliation_name', '').strip()
        validated_data['affiliation'] = self._resolve_affiliation(affiliation_name)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        affiliation_name = validated_data.pop('affiliation_name', None)
        if affiliation_name is not None:
            instance.affiliation = self._resolve_affiliation(affiliation_name.strip())
        return super().update(instance, validated_data)
