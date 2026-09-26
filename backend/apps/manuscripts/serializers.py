from rest_framework import serializers

from .models import Affiliation, Author, Manuscript, ManuscriptAuthor


class ManuscriptUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class AuthorInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    affiliation = serializers.CharField(max_length=255, required=False, allow_blank=True)
    author_order = serializers.IntegerField(min_value=1)
    is_corresponding = serializers.BooleanField(default=False)
    is_co_first = serializers.BooleanField(default=False)


class ManuscriptConfirmSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=512)
    abstract = serializers.CharField(allow_blank=True)
    keywords = serializers.ListField(child=serializers.CharField(max_length=64), allow_empty=True)
    domain = serializers.CharField(max_length=128, allow_blank=True)
    authors = AuthorInputSerializer(many=True)


class ManuscriptAuthorSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='author.name')
    email = serializers.EmailField(source='author.email', allow_null=True)
    affiliation = serializers.CharField(source='affiliation.name', allow_null=True)

    class Meta:
        model = ManuscriptAuthor
        fields = [
            'author_order',
            'name',
            'email',
            'affiliation',
            'is_corresponding',
            'is_co_first',
            'contact_email',
        ]


class ManuscriptDetailSerializer(serializers.ModelSerializer):
    authors = serializers.SerializerMethodField()

    class Meta:
        model = Manuscript
        fields = [
            'id',
            'domain',
            'title',
            'abstract',
            'keywords',
            'status',
            'source_pdf',
            'extracted_at',
            'confirmed_at',
            'authors',
            'created_at',
            'updated_at',
        ]

    def get_authors(self, obj: Manuscript):
        links = obj.author_links.select_related('author', 'affiliation').all().order_by('author_order')
        return ManuscriptAuthorSerializer(links, many=True).data


class ManuscriptListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Manuscript
        fields = ['id', 'title', 'domain', 'status', 'created_at', 'updated_at']
