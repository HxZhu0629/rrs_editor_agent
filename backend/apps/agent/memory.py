import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from django.conf import settings


MEMORY_LEVELS: Tuple[str, ...] = ('managed', 'user', 'project', 'local', 'auto', 'team')
MAX_INCLUDE_DEPTH = 5


@dataclass(frozen=True)
class MemoryDocument:
    level: str
    path: str
    content: str
    paths: Tuple[str, ...] = ()


def _parse_frontmatter(raw: str) -> Tuple[Dict[str, str], str]:
    if not raw.startswith('---'):
        return {}, raw
    lines = raw.splitlines()
    try:
        end = lines.index('---', 1)
    except ValueError:
        return {}, raw
    values: Dict[str, str] = {}
    for line in lines[1:end]:
        if ':' in line:
            key, value = line.split(':', 1)
            values[key.strip().lower()] = value.strip().strip('"\'')
    return values, '\n'.join(lines[end + 1:])


def _path_patterns(value: str) -> Tuple[str, ...]:
    return tuple(item.strip() for item in value.replace(',', ' ').split() if item.strip())


def _matches_target(patterns: Sequence[str], target_paths: Sequence[str]) -> bool:
    if not patterns or not target_paths:
        return not patterns
    return any(
        fnmatch.fnmatch(path.replace('\\', '/'), pattern.replace('\\', '/'))
        for pattern in patterns
        for path in target_paths
    )


def _configured_path(name: str) -> Optional[Path]:
    value = str(getattr(settings, name, '') or '').strip()
    return Path(value).expanduser() if value else None


def _project_root() -> Path:
    configured = _configured_path('AGENT_PROJECT_ROOT')
    if configured:
        return configured
    # settings.BASE_DIR points to <repo>/backend in this project.
    return Path(getattr(settings, 'BASE_DIR', Path.cwd())).resolve().parent


def _candidate_paths(level: str) -> List[Path]:
    root = _project_root()
    if level == 'managed':
        path = _configured_path('AGENT_MANAGED_MEMORY_FILE')
        return [path] if path else []
    if level == 'user':
        configured = _configured_path('AGENT_USER_MEMORY_FILE')
        return [configured] if configured else [
            Path.home() / '.rrs' / 'AGENT.md',
            Path.home() / '.config' / 'rrs' / 'AGENT.md',
        ]
    if level == 'project':
        configured = _configured_path('AGENT_PROJECT_MEMORY_FILE')
        return [configured] if configured else [
            root / 'AGENT.md', root / '.agent' / 'AGENT.md', root / '.claude' / 'CLAUDE.md',
        ]
    if level == 'local':
        configured = _configured_path('AGENT_LOCAL_MEMORY_FILE')
        return [configured] if configured else [
            Path.cwd() / 'AGENT.local.md', Path.cwd() / 'CLAUDE.local.md',
        ]
    if level == 'team':
        path = _configured_path('AGENT_TEAM_MEMORY_FILE')
        return [path] if path else []
    return []


class LayeredMemoryLoader:
    def __init__(
        self,
        *,
        auto_memory: Optional[Iterable[str]] = None,
        target_paths: Optional[Sequence[str]] = None,
    ):
        self.auto_memory = tuple(str(item) for item in (auto_memory or ()) if str(item).strip())
        self.target_paths = tuple(target_paths or ())
        self.documents: List[MemoryDocument] = []

    def _read_file(self, path: Path, level: str, depth: int, seen: set) -> List[MemoryDocument]:
        resolved = path.expanduser().resolve()
        if depth > MAX_INCLUDE_DEPTH or resolved in seen or not resolved.is_file():
            return []
        seen.add(resolved)
        try:
            raw = resolved.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            return []
        frontmatter, content = _parse_frontmatter(raw)
        patterns = _path_patterns(frontmatter.get('paths', ''))
        if not _matches_target(patterns, self.target_paths):
            return []
        documents = [MemoryDocument(level, str(resolved), content, patterns)]
        lines: List[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith('@') and len(stripped) > 1:
                include = Path(stripped[1:])
                if not include.is_absolute():
                    include = resolved.parent / include
                documents.extend(self._read_file(include, level, depth + 1, seen))
            else:
                lines.append(line)
        documents[0] = MemoryDocument(level, str(resolved), '\n'.join(lines).strip(), patterns)
        return [document for document in documents if document.content]

    def load(self) -> List[MemoryDocument]:
        documents: List[MemoryDocument] = []
        seen = set()
        for level in MEMORY_LEVELS:
            if level == 'auto':
                if self.auto_memory:
                    documents.append(MemoryDocument('auto', 'conversation:auto', '\n'.join(self.auto_memory)))
                continue
            for path in _candidate_paths(level):
                if path:
                    documents.extend(self._read_file(path, level, 0, seen))
        self.documents = documents
        return list(documents)

    def render(self, max_chars: Optional[int] = None) -> str:
        if not self.documents:
            self.load()
        limit = max_chars or getattr(settings, 'AGENT_MEMORY_MAX_CHARS', 12000)
        sections = []
        used = 0
        for document in self.documents:
            section = f'## {document.level.title()} memory ({document.path})\n{document.content}'
            if used and used + len(section) > limit:
                break
            sections.append(section)
            used += len(section)
        return '\n\n'.join(sections)
