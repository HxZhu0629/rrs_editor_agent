from django.db import models
from django.db.models import Q

from apps.common.models import UUIDTimestampModel


class Affiliation(UUIDTimestampModel):
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, unique=True)
    country = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        db_table = 'affiliations'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Author(UUIDTimestampModel):
    name = models.CharField(max_length=150)
    normalized_name = models.CharField(max_length=150)
    email = models.EmailField(blank=True, null=True, default=None)
    orcid = models.CharField(max_length=32, blank=True, null=True, default=None)

    class Meta:
        db_table = 'authors'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['orcid'],
                condition=Q(orcid__isnull=False),
                name='uq_author_orcid_notnull',
            ),
            models.UniqueConstraint(fields=['normalized_name', 'email'], name='uq_author_name_email'),
        ]

    def __str__(self) -> str:
        return self.name


class Manuscript(UUIDTimestampModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        EXTRACTING = 'extracting', 'Extracting'
        REVIEW_PENDING = 'review_pending', 'Review Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        RECOMMENDED = 'recommended', 'Recommended'
        FAILED = 'failed', 'Failed'

    domain = models.CharField(max_length=128, default='')
    title = models.CharField(max_length=512, default='')
    abstract = models.TextField(blank=True, default='')
    keywords = models.JSONField(default=list, blank=True)
    source_pdf = models.FileField(upload_to='manuscripts/%Y/%m/%d/', blank=True, null=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    extracted_at = models.DateTimeField(blank=True, null=True, default=None)
    confirmed_at = models.DateTimeField(blank=True, null=True, default=None)

    authors = models.ManyToManyField(
        to=Author,
        through='ManuscriptAuthor',
        through_fields=('manuscript', 'author'),
        related_name='manuscripts',
    )

    class Meta:
        db_table = 'manuscripts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['domain']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self) -> str:
        return self.title or str(self.id)


class ManuscriptAuthor(UUIDTimestampModel):
    manuscript = models.ForeignKey(Manuscript, on_delete=models.CASCADE, related_name='author_links')
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name='manuscript_links')
    affiliation = models.ForeignKey(
        Affiliation,
        on_delete=models.SET_NULL,
        related_name='manuscript_author_links',
        blank=True,
        null=True,
        default=None,
    )
    author_order = models.PositiveSmallIntegerField(default=1)
    is_corresponding = models.BooleanField(default=False)
    is_co_first = models.BooleanField(default=False)
    contact_email = models.EmailField(blank=True, null=True, default=None)

    class Meta:
        db_table = 'manuscript_authorships'
        ordering = ['author_order']
        constraints = [
            models.UniqueConstraint(fields=['manuscript', 'author'], name='uq_manuscript_author_pair'),
            models.UniqueConstraint(fields=['manuscript', 'author_order'], name='uq_manuscript_author_order'),
        ]
        indexes = [
            models.Index(fields=['manuscript', 'author_order']),
            models.Index(fields=['is_corresponding']),
        ]

    def __str__(self) -> str:
        return f'{self.manuscript_id}:{self.author_id}:{self.author_order}'
