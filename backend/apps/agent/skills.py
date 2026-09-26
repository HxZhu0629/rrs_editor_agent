from dataclasses import dataclass
import hashlib
import json
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class EditorSkill:
    name: str
    description: str
    instructions: Tuple[str, ...]
    recommended_tools: Tuple[str, ...]
    completion_checks: Tuple[str, ...]
    version: str = '1.0'

    def as_tool_result(self) -> Dict:
        return {
            'name': self.name,
            'version': self.version,
            'instructions_sha256': hashlib.sha256(json.dumps({
                'instructions': self.instructions, 'recommended_tools': self.recommended_tools,
                'completion_checks': self.completion_checks,
            }, sort_keys=True).encode('utf-8')).hexdigest(),
            'description': self.description,
            'instructions': list(self.instructions),
            'recommended_tools': list(self.recommended_tools),
            'completion_checks': list(self.completion_checks),
        }


class EditorSkillRegistry:
    def __init__(self, skills: List[EditorSkill]):
        self._skills = {skill.name: skill for skill in skills}
        if len(self._skills) != len(skills):
            raise ValueError('Skill names must be unique.')

    def get(self, name: str) -> Optional[EditorSkill]:
        return self._skills.get(name)

    def names(self) -> List[str]:
        return list(self._skills)

    def catalog(self) -> str:
        return '\n'.join(
            f'- {skill.name} (v{skill.version}): {skill.description}'
            for skill in self._skills.values()
        )


def build_editor_skill_registry() -> EditorSkillRegistry:
    return EditorSkillRegistry([
        EditorSkill(
            name='submission_preflight',
            description='Check whether a submission is ready for editorial assessment.',
            instructions=(
                'Check structured metadata and document parsing quality.',
                'Report missing or review-required items separately from optional improvements.',
                'Do not infer that a missing field exists from general manuscript prose.',
            ),
            recommended_tools=('check_submission_completeness', 'search_manuscript_evidence'),
            completion_checks=(
                'Every blocking issue is named.',
                'Document-quality warnings include evidence when available.',
                'The final response states whether manual review is required.',
            ),
        ),
        EditorSkill(
            name='manuscript_analysis',
            description='Answer editorial questions using verifiable manuscript evidence.',
            instructions=(
                'Turn the editorial question into one or more focused evidence searches.',
                'Prefer retrieved passages over unsupported model knowledge.',
                'Separate manuscript claims from your editorial interpretation.',
            ),
            recommended_tools=(
                'search_manuscript_evidence',
                'search_external_papers',
                'verify_paper_metadata',
                'check_retraction_status',
            ),
            completion_checks=(
                'Material factual claims cite a section or page when available.',
                'Unanswered parts are identified instead of invented.',
                'External literature claims identify their provider and a DOI, ORCID, or OpenAlex ID.',
                'Retraction checks disclose provider errors and the limits of a no-notice result.',
                'The response is concise enough for an editor to act on.',
            ),
        ),
        EditorSkill(
            name='reviewer_selection',
            description='Build and explain a conflict-aware reviewer shortlist.',
            instructions=(
                'Use manuscript evidence when the requested specialty is narrower than the stored metadata.',
                'Apply the editor constraints explicitly when recommending reviewers.',
                'Treat conflict filtering as mandatory and relevance ranking as a separate step.',
                'Explain each recommendation with stored publication evidence, not model memory.',
            ),
            recommended_tools=(
                'search_manuscript_evidence',
                'search_external_papers',
                'lookup_author_identity',
                'recommend_reviewers',
                'explain_recommendation',
            ),
            completion_checks=(
                'The requested number and constraints are reflected in the recommendation call.',
                'Every selected reviewer has a reason and supporting publication evidence.',
                'Known conflict checks and remaining uncertainty are disclosed.',
                'ORCID candidates are not treated as confirmed identities without editor verification.',
            ),
        ),
        EditorSkill(
            name='invitation_preparation',
            description='Prepare evidence-based reviewer invitation drafts for editor approval.',
            instructions=(
                'Use the current reviewer shortlist; recommend reviewers first only when none exists.',
                'Personalize drafts only with verified reviewer and publication data.',
                'Never claim that an email was sent.',
            ),
            recommended_tools=('recommend_reviewers', 'draft_reviewer_emails'),
            completion_checks=(
                'Draft count matches the editor request and available shortlist.',
                'Every draft is marked as requiring human approval.',
                'No external communication is performed.',
            ),
        ),
    ])
