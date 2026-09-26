from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from django.conf import settings
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from apps.extraction.models import ExtractionRun
from apps.extraction.vector_search import advanced_search_document_chunks
from apps.manuscripts.models import Manuscript
from apps.recommendations.retrieval import build_multiroute_recommendations

from .policy import RiskLevel
from .skills import EditorSkillRegistry, build_editor_skill_registry
from .mcp_client import AcademicMCPClient


class ToolInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class LoadEditorSkillInput(ToolInput):
    skill_name: str = Field(description='Exact skill name from the skill catalog in the system message')


class SearchEvidenceInput(ToolInput):
    query: str = Field(min_length=1, description='Question or evidence request about the manuscript')
    top_k: int = Field(default=5, ge=1, le=10)
    rewrite: bool = Field(default=False, description='Rewrite a conversational query before retrieval')
    hyde: bool = Field(default=False, description='Use hypothetical document embedding for difficult semantic queries')


class SearchExternalPapersInput(ToolInput):
    query: str = Field(min_length=1, description='Academic topic, method, author, or paper title to search')
    top_k: int = Field(default=10, ge=1, le=25)
    from_year: int = Field(default=0, ge=0, le=3000)
    to_year: int = Field(default=0, ge=0, le=3000)
    open_access_only: bool = False


class VerifyPaperMetadataInput(ToolInput):
    doi: str = Field(min_length=1, description='DOI to resolve and verify in Crossref')
    expected_title: str = Field(default='', description='Optional manuscript reference title to compare')
    expected_year: int = Field(default=0, ge=0, le=3000)
    expected_authors: List[str] = Field(default_factory=list)


class LookupAuthorIdentityInput(ToolInput):
    name: str = Field(min_length=1, description='Full author name to find in ORCID')
    affiliation: str = Field(default='', description='Optional institution used to narrow candidates')
    top_k: int = Field(default=5, ge=1, le=10)


class CheckRetractionStatusInput(ToolInput):
    doi: str = Field(min_length=1, description='DOI to check for retraction, concern, or correction notices')


class CheckSubmissionInput(ToolInput):
    include_document_quality: bool = True


class RecommendReviewersInput(ToolInput):
    top_k: int = Field(default=20, ge=1, le=50)
    query_keywords: List[str] = Field(default_factory=list)
    min_publication_year: int = Field(default=0, ge=0)
    exclude_affiliations: List[str] = Field(default_factory=list)
    diversify_affiliations: bool = False


class ExplainRecommendationInput(ToolInput):
    rank: Optional[int] = Field(default=None, ge=1)


class DraftReviewerEmailsInput(ToolInput):
    count: int = Field(default=5, ge=1, le=10)


@dataclass(frozen=True)
class EditorTool:
    tool: BaseTool
    risk_level: RiskLevel = RiskLevel.ACTION
    max_result_chars: int = 8000
    concurrency_safe: bool = False
    version: str = '1.0'
    source_protocol: str = 'native'


class EditorToolRegistry:
    def __init__(self, tools: List[EditorTool], skills: Optional[EditorSkillRegistry] = None):
        self._tools = {item.tool.name: item for item in tools}
        if len(self._tools) != len(tools):
            raise ValueError('Tool names must be unique.')
        self.skills = skills or build_editor_skill_registry()

    def get(self, name: str) -> Optional[EditorTool]:
        return self._tools.get(name)

    def langchain_tools(self) -> List[BaseTool]:
        return [item.tool for item in self._tools.values()]

    def schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                'name': item.tool.name,
                'description': item.tool.description,
                'parameters': item.tool.args_schema.model_json_schema() if item.tool.args_schema else {},
                'risk_level': item.risk_level.value,
                'version': item.version,
                'source_protocol': item.source_protocol,
                'requires_approval': item.risk_level == RiskLevel.ACTION,
            }
            for item in self._tools.values()
        ]


def _recommend_reviewers(manuscript: Manuscript, values: RecommendReviewersInput) -> Dict[str, Any]:
    rows, candidate_count = build_multiroute_recommendations(
        manuscript,
        top_k=values.top_k,
        query_keywords=values.query_keywords,
        min_publication_year=values.min_publication_year,
        exclude_affiliations=values.exclude_affiliations,
        diversify_affiliations=values.diversify_affiliations,
    )
    recommendations = []
    for rank, row in enumerate(rows, start=1):
        reviewer = row['reviewer']
        recommendations.append({
            'rank': rank,
            'reviewer_id': str(reviewer.id),
            'reviewer_name': reviewer.name,
            'reviewer_email': reviewer.email,
            'reviewer_affiliation': reviewer.affiliation.name if reviewer.affiliation else '',
            'score': round(row['score'], 6),
            'rerank_score': round(row['rerank_score'], 6),
            'rationale': row['rationale'],
        })
    return {
        'candidate_count': candidate_count,
        'returned_count': len(recommendations),
        'recommendations': recommendations,
    }


def _latest_extraction(manuscript: Manuscript) -> Optional[ExtractionRun]:
    return manuscript.extraction_runs.filter(status=ExtractionRun.Status.SUCCESS).order_by('-created_at').first()


def _draft_email(manuscript: Manuscript, recommendation: Dict[str, Any]) -> Dict[str, Any]:
    papers = recommendation.get('rationale', {}).get('representative_papers', [])
    if papers:
        evidence = f"We noticed your relevant work, including \"{papers[0].get('title', '')}\"."
    else:
        evidence = 'Your research profile appears relevant to the manuscript topic.'
    name = recommendation.get('reviewer_name') or 'Reviewer'
    title = manuscript.title or 'the submitted manuscript'
    return {
        'reviewer_id': recommendation.get('reviewer_id'),
        'reviewer_name': name,
        'reviewer_email': recommendation.get('reviewer_email'),
        'subject': f'Review invitation: {title}',
        'body': (
            f'Dear {name},\n\n'
            f'We would like to invite you to review "{title}". {evidence}\n\n'
            'Please let us know whether you are available and whether any conflict of interest exists. '
            'This message is a draft and must be reviewed by the editor before sending.\n\n'
            'Best regards,\nEditorial Office\n'
        ),
        'requires_human_approval': True,
    }


def _structured_tool(name: str, description: str, schema, handler: Callable[..., Dict[str, Any]]) -> BaseTool:
    return StructuredTool.from_function(
        func=handler,
        name=name,
        description=description,
        args_schema=schema,
    )


def build_editor_tool_registry(manuscript: Manuscript, previous_recommendations: List[Dict[str, Any]]) -> EditorToolRegistry:
    skills = build_editor_skill_registry()
    recommendation_context = list(previous_recommendations)
    academic_mcp = AcademicMCPClient(timeout=settings.ACADEMIC_MCP_TIMEOUT)

    def load_editor_skill(skill_name: str):
        skill = skills.get(skill_name)
        if skill is None:
            return {
                'status': 'not_found',
                'skill_name': skill_name,
                'available_skills': skills.names(),
            }
        available = [name for name in skill.recommended_tools if registry.get(name)]
        return {'status': 'loaded', 'skill': {
            **skill.as_tool_result(), 'available_tools': available,
            'unavailable_tools': [name for name in skill.recommended_tools if name not in available],
        }}

    def search_manuscript_evidence(query: str, top_k: int = 5, rewrite: bool = False, hyde: bool = False):
        extraction = _latest_extraction(manuscript)
        if extraction is None:
            return {
                'status': 'unavailable',
                'reason': 'No successful document extraction is available.',
                'results': [],
            }
        return advanced_search_document_chunks(
            query,
            extraction_run=extraction,
            top_k=top_k,
            query_rewrite=rewrite,
            hyde=hyde,
        )

    def search_external_papers(
        query: str,
        top_k: int = 10,
        from_year: int = 0,
        to_year: int = 0,
        open_access_only: bool = False,
    ):
        if from_year and to_year and from_year > to_year:
            raise ValueError('from_year must not be greater than to_year')
        return academic_mcp.search_papers(
            query=query,
            top_k=top_k,
            from_year=from_year,
            to_year=to_year,
            open_access_only=open_access_only,
        )

    def verify_paper_metadata(
        doi: str,
        expected_title: str = '',
        expected_year: int = 0,
        expected_authors: Optional[List[str]] = None,
    ):
        return academic_mcp.verify_metadata(
            doi=doi,
            expected_title=expected_title,
            expected_year=expected_year,
            expected_authors=expected_authors or [],
        )

    def lookup_author_identity(name: str, affiliation: str = '', top_k: int = 5):
        return academic_mcp.lookup_author(
            name=name,
            affiliation=affiliation,
            top_k=top_k,
        )

    def check_retraction_status(doi: str):
        return academic_mcp.check_retraction(doi=doi)

    def check_submission_completeness(include_document_quality: bool = True):
        extraction = _latest_extraction(manuscript)
        missing = []
        if not manuscript.title.strip():
            missing.append('title')
        if not manuscript.abstract.strip():
            missing.append('abstract')
        if not manuscript.keywords:
            missing.append('keywords')
        if not manuscript.author_links.exists():
            missing.append('authors')
        if not manuscript.source_pdf:
            missing.append('source_document')
        quality = extraction.document_quality if extraction and include_document_quality else {}
        return {
            'complete': not missing and not quality.get('needs_review', False),
            'missing_fields': missing,
            'document_quality': quality,
            'manuscript': {
                'id': str(manuscript.id),
                'title': manuscript.title,
                'domain': manuscript.domain,
                'keywords': manuscript.keywords,
                'status': manuscript.status,
            },
        }

    def recommend_reviewers(
        top_k: int = 20,
        query_keywords: Optional[List[str]] = None,
        min_publication_year: int = 0,
        exclude_affiliations: Optional[List[str]] = None,
        diversify_affiliations: bool = False,
    ):
        result = _recommend_reviewers(manuscript, RecommendReviewersInput(
            top_k=top_k,
            query_keywords=query_keywords or [],
            min_publication_year=min_publication_year,
            exclude_affiliations=exclude_affiliations or [],
            diversify_affiliations=diversify_affiliations,
        ))
        recommendation_context[:] = result.get('recommendations', [])
        return result

    def explain_recommendation(rank: Optional[int] = None):
        selected = list(recommendation_context)
        if rank is not None:
            selected = [item for item in selected if int(item.get('rank') or 0) == rank]
        return {'recommendations': selected}

    def draft_reviewer_emails(count: int = 5):
        selected = list(recommendation_context)[:count]
        return {
            'recommendations': selected,
            'email_drafts': [_draft_email(manuscript, item) for item in selected],
            'requires_human_approval': True,
        }

    definitions = [
        EditorTool(
            tool=_structured_tool(
                'load_editor_skill',
                'Load trusted workflow instructions and completion checks for a multi-step editorial task.',
                LoadEditorSkillInput,
                load_editor_skill,
            ),
            risk_level=RiskLevel.READ,
            max_result_chars=5000,
            concurrency_safe=True,
        ),
        EditorTool(
            tool=_structured_tool(
                'search_manuscript_evidence',
                'Retrieve manuscript passages with section and page evidence using hybrid Advanced RAG.',
                SearchEvidenceInput,
                search_manuscript_evidence,
            ),
            risk_level=RiskLevel.READ,
            concurrency_safe=True,
        ),
        EditorTool(
            tool=_structured_tool(
                'search_external_papers',
                'Search current external academic literature through the OpenAlex MCP server. '
                'Use for literature outside the private manuscript and local reviewer database.',
                SearchExternalPapersInput,
                search_external_papers,
            ),
            source_protocol='mcp',
            risk_level=RiskLevel.READ,
            max_result_chars=12000,
            concurrency_safe=True,
        ),
        EditorTool(
            tool=_structured_tool(
                'verify_paper_metadata',
                'Resolve a DOI through Crossref and compare authoritative metadata with optional expected values.',
                VerifyPaperMetadataInput,
                verify_paper_metadata,
            ),
            source_protocol='mcp',
            risk_level=RiskLevel.READ,
            concurrency_safe=True,
        ),
        EditorTool(
            tool=_structured_tool(
                'lookup_author_identity',
                'Find public ORCID identity candidates by author name and optional affiliation. '
                'Candidates require editor confirmation.',
                LookupAuthorIdentityInput,
                lookup_author_identity,
            ),
            source_protocol='mcp',
            risk_level=RiskLevel.READ,
            concurrency_safe=True,
        ),
        EditorTool(
            tool=_structured_tool(
                'check_retraction_status',
                'Check Crossref notices and OpenAlex metadata for retraction, expression of concern, or correction.',
                CheckRetractionStatusInput,
                check_retraction_status,
            ),
            source_protocol='mcp',
            risk_level=RiskLevel.READ,
            concurrency_safe=True,
        ),
        EditorTool(
            tool=_structured_tool(
                'check_submission_completeness',
                'Check required manuscript metadata and document parsing quality.',
                CheckSubmissionInput,
                check_submission_completeness,
            ),
            risk_level=RiskLevel.READ,
            concurrency_safe=True,
        ),
        EditorTool(
            tool=_structured_tool(
                'recommend_reviewers',
                'Find eligible peer reviewers using hybrid retrieval, conflict filtering, RRF, and reranking.',
                RecommendReviewersInput,
                recommend_reviewers,
            ),
            risk_level=RiskLevel.READ,
        ),
        EditorTool(
            tool=_structured_tool(
                'explain_recommendation',
                'Return previous reviewer recommendations and their supporting publication evidence.',
                ExplainRecommendationInput,
                explain_recommendation,
            ),
            risk_level=RiskLevel.READ,
            concurrency_safe=True,
        ),
        EditorTool(
            tool=_structured_tool(
                'draft_reviewer_emails',
                'Create reviewer invitation email drafts. This tool never sends email.',
                DraftReviewerEmailsInput,
                draft_reviewer_emails,
            ),
            risk_level=RiskLevel.DRAFT,
        ),
    ]
    disabled = set(settings.AGENT_DISABLED_TOOLS)
    known = {definition.tool.name for definition in definitions}
    if disabled - known:
        raise ValueError(f'Unknown AGENT_DISABLED_TOOLS: {sorted(disabled - known)}')
    for name in skills.names():
        if set(skills.get(name).recommended_tools) - known:
            raise ValueError(f'Skill {name} references unknown tools.')
    registry = EditorToolRegistry([definition for definition in definitions if definition.tool.name not in disabled], skills=skills)
    return registry
