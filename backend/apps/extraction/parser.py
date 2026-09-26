from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ParsedDocument:
    markdown: str
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    quality: Dict[str, Any] = field(default_factory=dict)


class _TableMarkdownParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self.current_row: List[str] = []
        self.current_cell: List[str] = []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'tr':
            self.current_row = []
        elif tag.lower() in {'td', 'th'}:
            self.current_cell = []
            self.in_cell = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {'td', 'th'} and self.in_cell:
            self.current_row.append(' '.join(''.join(self.current_cell).split()))
            self.in_cell = False
        elif tag == 'tr' and self.current_row:
            self.rows.append(self.current_row)

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)


def _table_to_markdown(html: str, fallback: str) -> str:
    parser = _TableMarkdownParser()
    try:
        parser.feed(html or '')
    except Exception:
        parser.rows = []
    rows = [row for row in parser.rows if any(cell for cell in row)]
    if not rows:
        return fallback.strip()
    width = max(len(row) for row in rows)
    normalized = [row + [''] * (width - len(row)) for row in rows]
    lines = [f"| {' | '.join(normalized[0])} |", f"| {' | '.join('---' for _ in range(width))} |"]
    lines.extend(f"| {' | '.join(row)} |" for row in normalized[1:])
    return '\n'.join(lines)


def _element_text(element: Any) -> str:
    return ' '.join(str(getattr(element, 'text', '') or '').split())


def _element_page(element: Any) -> Optional[int]:
    metadata = getattr(element, 'metadata', None)
    value = getattr(metadata, 'page_number', None) if metadata else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _element_metadata(element: Any) -> Dict[str, Any]:
    metadata = getattr(element, 'metadata', None)
    if metadata is None:
        return {}
    try:
        values = metadata.to_dict()
    except AttributeError:
        return {}
    if not isinstance(values, dict):
        return {}
    def make_json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [make_json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): make_json_safe(item) for key, item in value.items()}
        return str(value)

    return {str(key): make_json_safe(value) for key, value in values.items()}


def _block_markdown(category: str, text: str, metadata: Dict[str, Any]) -> str:
    normalized = category.lower().replace(' ', '')
    if normalized in {'title', 'header', 'heading', 'heading1', 'heading2', 'heading3', 'heading4', 'heading5', 'heading6'}:
        level = 1 if normalized in {'title', 'header', 'heading', 'heading1'} else min(int(normalized[-1]), 6) if normalized[-1].isdigit() else 2
        return f"{'#' * level} {text}".strip()
    if normalized in {'listitem', 'bulletedlistitem', 'numberedlistitem'}:
        return f'- {text}'.strip()
    if normalized == 'table':
        return _table_to_markdown(metadata.get('text_as_html', ''), text)
    if normalized in {'image', 'figure', 'picture'}:
        return f'[Figure: {text}]' if text else '[Figure]'
    if normalized == 'equation':
        return f'$$\n{text}\n$$'
    return text


def _quality(blocks: List[Dict[str, Any]], markdown: str) -> Dict[str, Any]:
    text_chars = sum(len(block['text']) for block in blocks if block['type'] not in {'image', 'figure'})
    pages = sorted({block['page'] for block in blocks if block.get('page') is not None})
    category_counts: Dict[str, int] = {}
    for block in blocks:
        category_counts[block['type']] = category_counts.get(block['type'], 0) + 1
    replacement_chars = markdown.count('\ufffd')
    warnings = []
    if text_chars < 200:
        warnings.append('low_text_content')
    if replacement_chars / max(1, len(markdown)) > 0.01:
        warnings.append('high_replacement_character_ratio')
    if category_counts.get('table', 0) and not any('|' in block['markdown'] for block in blocks if block['type'] == 'table'):
        warnings.append('table_structure_unresolved')
    return {
        'parser': 'unstructured',
        'block_count': len(blocks),
        'text_char_count': text_chars,
        'page_count': len(pages),
        'table_count': category_counts.get('table', 0),
        'figure_count': category_counts.get('figure', 0) + category_counts.get('image', 0),
        'category_counts': category_counts,
        'warnings': warnings,
        'needs_review': bool(warnings),
        'scan_suspected': text_chars < 200,
    }


def parse_document(path: Path) -> ParsedDocument:
    if not path.exists():
        raise FileNotFoundError(str(path))
    try:
        from unstructured.partition.auto import partition
    except ImportError as exc:
        raise RuntimeError('Unstructured is not installed; install the document parser dependencies.') from exc

    elements = partition(filename=str(path), include_page_breaks=False)
    blocks = []
    markdown_parts = []
    for index, element in enumerate(elements):
        text = _element_text(element)
        metadata = _element_metadata(element)
        category = str(getattr(element, 'category', None) or element.__class__.__name__)
        block_type = category.lower().replace(' ', '')
        if not text and block_type not in {'image', 'figure', 'table'}:
            continue
        block_markdown = _block_markdown(category, text, metadata)
        block = {
            'index': index,
            'type': block_type,
            'text': text,
            'markdown': block_markdown,
            'page': _element_page(element),
            'metadata': {key: value for key, value in metadata.items()
                         if key in {'page_number', 'filename', 'text_as_html', 'image_path', 'parent_id'}},
        }
        blocks.append(block)
        if block_markdown:
            markdown_parts.append(block_markdown)

    markdown = '\n\n'.join(markdown_parts).strip()
    return ParsedDocument(markdown=markdown, blocks=blocks, quality=_quality(blocks, markdown))
