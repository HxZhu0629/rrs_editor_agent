import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from django.db import transaction

from .models import DocumentChunk, ExtractionRun


DEFAULT_CHILD_SIZE = 480
DEFAULT_CHILD_OVERLAP = 64
DEFAULT_PARENT_SIZE = 1600
TOKENIZER_NAME = 'heuristic_cjk_word_v1'

HEADING_TYPES = {
    'title',
    'header',
    'heading',
    'heading1',
    'heading2',
    'heading3',
    'heading4',
    'heading5',
    'heading6',
}
TABLE_TYPES = {'table'}
SEPARATORS = ('\n\n', '\n', '。', '！', '？', '. ', '! ', '? ', '；', '; ', '，', ', ', ' ')
TOKEN_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
    r"|[A-Za-z0-9]+(?:[._'’/-][A-Za-z0-9]+)*"
    r"|[^\s]",
    flags=re.UNICODE,
)


@dataclass
class ChunkDraft:
    kind: str
    chunk_index: int
    section_title: str
    content: str
    token_count: int
    page_start: Optional[int]
    page_end: Optional[int]
    source_block_indexes: List[int]
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent_index: Optional[int] = None


@dataclass
class _ParentDraft:
    section_title: str
    blocks: List[Dict[str, Any]]


@dataclass
class _TextUnit:
    content: str
    source_block_index: int


def estimate_tokens(text: str) -> int:
    """Estimate tokens deterministically until a model tokenizer is configured."""
    return sum(1 for _ in TOKEN_PATTERN.finditer(text or ''))


def _token_tail(text: str, token_count: int) -> str:
    spans = list(TOKEN_PATTERN.finditer(text or ''))
    if not spans or token_count <= 0:
        return ''
    if len(spans) <= token_count:
        return text.strip()
    return text[spans[-token_count].start():].strip()


def _hard_split(text: str, max_tokens: int) -> List[str]:
    spans = list(TOKEN_PATTERN.finditer(text))
    if not spans:
        return []
    chunks = []
    for start in range(0, len(spans), max_tokens):
        end = min(start + max_tokens, len(spans))
        start_char = 0 if start == 0 else spans[start].start()
        end_char = len(text) if end == len(spans) else spans[end - 1].end()
        chunk = text[start_char:end_char].strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def _split_preserving_separator(text: str, separator: str) -> List[str]:
    parts = text.split(separator)
    pieces = []
    for index, part in enumerate(parts):
        suffix = separator if index < len(parts) - 1 else ''
        piece = f'{part}{suffix}'
        if piece.strip():
            pieces.append(piece)
    return pieces


def _recursive_units(text: str, max_tokens: int, separators: Sequence[str] = SEPARATORS) -> List[str]:
    if not text or not text.strip():
        return []
    if estimate_tokens(text) <= max_tokens:
        return [text]
    if not separators:
        return _hard_split(text, max_tokens)

    separator = separators[0]
    if separator not in text:
        return _recursive_units(text, max_tokens, separators[1:])

    units = []
    for piece in _split_preserving_separator(text, separator):
        if estimate_tokens(piece) <= max_tokens:
            units.append(piece)
        else:
            units.extend(_recursive_units(piece, max_tokens, separators[1:]))
    return units


def _block_content(block: Dict[str, Any]) -> str:
    return str(block.get('markdown') or block.get('text') or '').strip()


def _block_type(block: Dict[str, Any]) -> str:
    return str(block.get('type') or '').lower().replace(' ', '')


def _block_index(block: Dict[str, Any], fallback: int) -> int:
    try:
        return int(block.get('index', fallback))
    except (TypeError, ValueError):
        return fallback


def _page_range(blocks: Sequence[Dict[str, Any]]) -> tuple[Optional[int], Optional[int]]:
    pages = []
    for block in blocks:
        try:
            page = int(block.get('page'))
        except (TypeError, ValueError):
            continue
        pages.append(page)
    return (min(pages), max(pages)) if pages else (None, None)


def _source_indexes(blocks: Sequence[Dict[str, Any]]) -> List[int]:
    return [_block_index(block, index) for index, block in enumerate(blocks)]


def _build_parents(blocks: Sequence[Dict[str, Any]], parent_size: int) -> List[_ParentDraft]:
    parents = []
    current_blocks: List[Dict[str, Any]] = []
    current_title = ''
    current_tokens = 0

    def flush() -> None:
        nonlocal current_blocks, current_tokens
        if current_blocks:
            parents.append(_ParentDraft(section_title=current_title, blocks=current_blocks))
        current_blocks = []
        current_tokens = 0

    for block in blocks:
        content = _block_content(block)
        if not content:
            continue
        block_tokens = estimate_tokens(content)
        block_type = _block_type(block)

        if block_type in HEADING_TYPES:
            flush()
            current_title = str(block.get('text') or content.lstrip('#').strip())
        elif (
            current_blocks
            and current_tokens + block_tokens > parent_size
            and not (len(current_blocks) == 1 and _block_type(current_blocks[0]) in HEADING_TYPES)
        ):
            flush()

        current_blocks.append(block)
        current_tokens += block_tokens

    flush()
    return parents


def _text_units(blocks: Sequence[Dict[str, Any]], max_tokens: int) -> List[_TextUnit]:
    units = []
    for position, block in enumerate(blocks):
        content = _block_content(block)
        if not content:
            continue
        block_index = _block_index(block, position)
        pieces = _recursive_units(content, max_tokens)
        for piece_index, piece in enumerate(pieces):
            prefix = '\n\n' if position > 0 and piece_index == 0 else ''
            units.append(_TextUnit(content=f'{prefix}{piece}', source_block_index=block_index))
    return units


def _build_text_children(
    blocks: Sequence[Dict[str, Any]],
    section_title: str,
    parent_index: int,
    child_index: int,
    child_size: int,
    child_overlap: int,
) -> tuple[List[ChunkDraft], int]:
    max_unit_tokens = max(1, child_size - child_overlap)
    units = _text_units(blocks, max_unit_tokens)
    children = []
    current = ''
    current_sources: List[int] = []

    def append_child() -> None:
        nonlocal child_index
        content = current.strip()
        if not content:
            return
        page_start, page_end = _page_range(
            [block for position, block in enumerate(blocks) if _block_index(block, position) in current_sources]
        )
        children.append(ChunkDraft(
            kind=DocumentChunk.Kind.CHILD,
            chunk_index=child_index,
            section_title=section_title,
            content=content,
            token_count=estimate_tokens(content),
            page_start=page_start,
            page_end=page_end,
            source_block_indexes=list(dict.fromkeys(current_sources)),
            metadata={
                'chunk_size': child_size,
                'chunk_overlap': child_overlap,
                'tokenizer': TOKENIZER_NAME,
            },
            parent_index=parent_index,
        ))
        child_index += 1

    for unit in units:
        candidate = f'{current}{unit.content}'
        if current and estimate_tokens(candidate) > child_size:
            previous_content = current
            previous_sources = current_sources
            append_child()
            current = _token_tail(previous_content, child_overlap)
            current_sources = list(previous_sources)
            candidate = f'{current}{unit.content}'

        current = candidate
        if unit.source_block_index not in current_sources:
            current_sources.append(unit.source_block_index)

    append_child()
    return children, child_index


def build_document_chunks(
    blocks: Sequence[Dict[str, Any]],
    child_size: int = DEFAULT_CHILD_SIZE,
    child_overlap: int = DEFAULT_CHILD_OVERLAP,
    parent_size: int = DEFAULT_PARENT_SIZE,
) -> List[ChunkDraft]:
    if child_size <= 0 or parent_size <= 0:
        raise ValueError('Chunk sizes must be positive.')
    if child_overlap < 0 or child_overlap >= child_size:
        raise ValueError('Child overlap must be non-negative and smaller than child size.')

    parent_groups = _build_parents(blocks, parent_size)
    drafts = []
    child_drafts = []
    child_index = 0

    for parent_index, parent in enumerate(parent_groups):
        parent_content = '\n\n'.join(_block_content(block) for block in parent.blocks if _block_content(block))
        page_start, page_end = _page_range(parent.blocks)
        drafts.append(ChunkDraft(
            kind=DocumentChunk.Kind.PARENT,
            chunk_index=parent_index,
            section_title=parent.section_title,
            content=parent_content,
            token_count=estimate_tokens(parent_content),
            page_start=page_start,
            page_end=page_end,
            source_block_indexes=_source_indexes(parent.blocks),
            metadata={
                'target_size': parent_size,
                'tokenizer': TOKENIZER_NAME,
            },
        ))

        text_blocks = []
        for block in parent.blocks:
            if _block_type(block) in TABLE_TYPES:
                if text_blocks:
                    children, child_index = _build_text_children(
                        text_blocks, parent.section_title, parent_index, child_index, child_size, child_overlap,
                    )
                    child_drafts.extend(children)
                    text_blocks = []

                table_content = _block_content(block)
                table_tokens = estimate_tokens(table_content)
                page_start, page_end = _page_range([block])
                child_drafts.append(ChunkDraft(
                    kind=DocumentChunk.Kind.CHILD,
                    chunk_index=child_index,
                    section_title=parent.section_title,
                    content=table_content,
                    token_count=table_tokens,
                    page_start=page_start,
                    page_end=page_end,
                    source_block_indexes=[_block_index(block, 0)],
                    metadata={
                        'chunk_size': child_size,
                        'chunk_overlap': child_overlap,
                        'tokenizer': TOKENIZER_NAME,
                        'atomic': True,
                        'element_type': 'table',
                        'oversized_atomic': table_tokens > child_size,
                    },
                    parent_index=parent_index,
                ))
                child_index += 1
            else:
                text_blocks.append(block)

        if text_blocks:
            children, child_index = _build_text_children(
                text_blocks, parent.section_title, parent_index, child_index, child_size, child_overlap,
            )
            child_drafts.extend(children)

    return drafts + child_drafts


@transaction.atomic
def persist_document_chunks(
    extraction_run: ExtractionRun,
    blocks: Sequence[Dict[str, Any]],
    child_size: int = DEFAULT_CHILD_SIZE,
    child_overlap: int = DEFAULT_CHILD_OVERLAP,
    parent_size: int = DEFAULT_PARENT_SIZE,
) -> List[DocumentChunk]:
    drafts = build_document_chunks(blocks, child_size, child_overlap, parent_size)
    extraction_run.document_chunks.all().delete()

    parent_objects = {}
    for draft in drafts:
        if draft.kind != DocumentChunk.Kind.PARENT:
            continue
        parent_objects[draft.chunk_index] = DocumentChunk(
            extraction_run=extraction_run,
            kind=draft.kind,
            chunk_index=draft.chunk_index,
            section_title=draft.section_title,
            content=draft.content,
            token_count=draft.token_count,
            page_start=draft.page_start,
            page_end=draft.page_end,
            source_block_indexes=draft.source_block_indexes,
            metadata=draft.metadata,
        )
    DocumentChunk.objects.bulk_create(parent_objects.values())

    child_objects = []
    for draft in drafts:
        if draft.kind != DocumentChunk.Kind.CHILD:
            continue
        child_objects.append(DocumentChunk(
            extraction_run=extraction_run,
            parent=parent_objects[draft.parent_index],
            kind=draft.kind,
            chunk_index=draft.chunk_index,
            section_title=draft.section_title,
            content=draft.content,
            token_count=draft.token_count,
            page_start=draft.page_start,
            page_end=draft.page_end,
            source_block_indexes=draft.source_block_indexes,
            metadata=draft.metadata,
        ))
    DocumentChunk.objects.bulk_create(child_objects)
    return list(parent_objects.values()) + child_objects
