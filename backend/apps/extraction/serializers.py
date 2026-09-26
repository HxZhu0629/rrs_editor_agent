from rest_framework import serializers

from .models import DocumentChunk, ExtractionRun


class DocumentChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentChunk
        fields = [
            'id',
            'extraction_run',
            'parent',
            'kind',
            'chunk_index',
            'section_title',
            'content',
            'token_count',
            'page_start',
            'page_end',
            'source_block_indexes',
            'metadata',
            'embedding_model',
            'embedding_updated_at',
            'created_at',
            'updated_at',
        ]


class ExtractionRunSerializer(serializers.ModelSerializer):
    parent_chunk_count = serializers.SerializerMethodField()
    child_chunk_count = serializers.SerializerMethodField()

    def get_parent_chunk_count(self, obj):
        return obj.document_chunks.filter(kind=DocumentChunk.Kind.PARENT).count()

    def get_child_chunk_count(self, obj):
        return obj.document_chunks.filter(kind=DocumentChunk.Kind.CHILD).count()

    class Meta:
        model = ExtractionRun
        fields = [
            'id',
            'manuscript',
            'model_name',
            'status',
            'raw_response',
            'parsed_json',
            'parser_name',
            'document_markdown',
            'document_blocks',
            'document_quality',
            'parent_chunk_count',
            'child_chunk_count',
            'confidence',
            'error_message',
            'created_at',
            'updated_at',
        ]
