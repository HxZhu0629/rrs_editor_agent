import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from mcp.server.fastmcp import FastMCP


mcp = FastMCP('academic-evidence')


def _first(values: Optional[List[Any]], default: Any = '') -> Any:
    return values[0] if values else default


def _normalize_doi(value: str) -> str:
    doi = str(value or '').strip().lower()
    for prefix in ('https://doi.org/', 'http://doi.org/', 'doi:'):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi


def _year(item: Dict[str, Any]) -> Optional[int]:
    for key in ('published', 'published-print', 'published-online', 'issued'):
        parts = ((item.get(key) or {}).get('date-parts') or [[]])[0]
        if parts:
            return int(parts[0])
    return None


def _crossref_headers() -> Dict[str, str]:
    mailto = os.getenv('CROSSREF_MAILTO', '').strip()
    contact = f'; mailto:{mailto}' if mailto else ''
    return {'User-Agent': f'RRS-Editor-Agent/1.0 ({contact or "no-contact"})'}


def _openalex_parameters(**values) -> Dict[str, Any]:
    mailto = os.getenv('OPENALEX_MAILTO', '').strip()
    return {**values, **({'mailto': mailto} if mailto else {})}


def _abstract_text(inverted_index: Optional[Dict[str, List[int]]], max_chars: int = 1600) -> str:
    if not inverted_index:
        return ''
    positions = [position for values in inverted_index.values() for position in values]
    if not positions:
        return ''
    words = [''] * (max(positions) + 1)
    for word, values in inverted_index.items():
        for position in values:
            if 0 <= position < len(words):
                words[position] = word
    return ' '.join(words).strip()[:max_chars]


def _openalex_authors(authorships: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for authorship in authorships[:12]:
        author = authorship.get('author') or {}
        institutions = authorship.get('institutions') or []
        rows.append({
            'openalex_id': str(author.get('id') or '').rsplit('/', 1)[-1],
            'name': author.get('display_name') or '',
            'orcid': author.get('orcid') or '',
            'institutions': [item.get('display_name') for item in institutions if item.get('display_name')],
        })
    return rows


def _openalex_work(work: Dict[str, Any]) -> Dict[str, Any]:
    location = work.get('primary_location') or {}
    best_oa_location = work.get('best_oa_location') or {}
    source = location.get('source') or {}
    open_access = work.get('open_access') or {}
    topic = work.get('primary_topic') or {}
    return {
        'openalex_id': str(work.get('id') or '').rsplit('/', 1)[-1],
        'doi': work.get('doi') or '',
        'title': work.get('display_name') or work.get('title') or '',
        'publication_year': work.get('publication_year'),
        'publication_date': work.get('publication_date'),
        'type': work.get('type') or '',
        'authors': _openalex_authors(work.get('authorships') or []),
        'venue': source.get('display_name') or '',
        'landing_page_url': location.get('landing_page_url') or best_oa_location.get('landing_page_url') or '',
        'open_access_url': open_access.get('oa_url') or best_oa_location.get('landing_page_url') or '',
        'pdf_url': best_oa_location.get('pdf_url') or location.get('pdf_url') or '',
        'open_access_status': open_access.get('oa_status') or '',
        'open_access_license': best_oa_location.get('license') or location.get('license') or '',
        'is_open_access': bool(open_access.get('is_oa')),
        'cited_by_count': int(work.get('cited_by_count') or 0),
        'topic': topic.get('display_name') or '',
        'abstract': _abstract_text(work.get('abstract_inverted_index')),
    }


@mcp.tool()
def resolve_openalex_access(
    doi: str = '',
    openalex_id: str = '',
    title: str = '',
) -> Dict[str, Any]:
    """Resolve a paper to its OpenAlex landing page and legal open-access PDF location."""
    normalized_doi = _normalize_doi(doi)
    normalized_openalex_id = str(openalex_id or '').strip().rsplit('/', 1)[-1]
    base_url = os.getenv('OPENALEX_BASE_URL', 'https://api.openalex.org').rstrip('/')
    if normalized_openalex_id:
        url = f'{base_url}/works/{quote(normalized_openalex_id, safe="")}'
        params = _openalex_parameters()
    elif normalized_doi:
        url = f'{base_url}/works'
        params = _openalex_parameters(filter=f'doi:{normalized_doi}', **{'per-page': 1})
    elif title.strip():
        url = f'{base_url}/works'
        params = _openalex_parameters(search=title.strip(), **{'per-page': 1})
    else:
        raise ValueError('doi, openalex_id, or title is required')

    response = requests.get(
        url,
        params=params,
        headers={'User-Agent': 'RRS-Editor-Agent/1.0'},
        timeout=int(os.getenv('OPENALEX_TIMEOUT', '20')),
    )
    response.raise_for_status()
    payload = response.json()
    work = (payload.get('results') or [None])[0] if 'results' in payload else payload
    if not work:
        return {'provider': 'OpenAlex', 'status': 'not_found', 'paper': {}}
    return {'provider': 'OpenAlex', 'status': 'resolved', 'paper': _openalex_work(work)}


def _crossref_author(author: Dict[str, Any]) -> Dict[str, Any]:
    name = ' '.join(part for part in (author.get('given'), author.get('family')) if part).strip()
    return {
        'name': name,
        'orcid': author.get('ORCID') or '',
        'affiliations': [
            affiliation.get('name')
            for affiliation in author.get('affiliation') or []
            if affiliation.get('name')
        ],
    }


def _normalized_text(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


@mcp.tool()
def search_openalex_works(
    query: str,
    top_k: int = 10,
    from_year: int = 0,
    to_year: int = 0,
    open_access_only: bool = False,
) -> Dict[str, Any]:
    """Search external academic papers in OpenAlex and return normalized bibliographic evidence."""
    query = query.strip()
    if not query:
        raise ValueError('query must not be empty')
    top_k = max(1, min(int(top_k), 25))
    filters = ['is_retracted:false']
    if from_year:
        filters.append(f'from_publication_date:{int(from_year)}-01-01')
    if to_year:
        filters.append(f'to_publication_date:{int(to_year)}-12-31')
    if open_access_only:
        filters.append('is_oa:true')

    response = requests.get(
        f"{os.getenv('OPENALEX_BASE_URL', 'https://api.openalex.org').rstrip('/')}/works",
        params=_openalex_parameters(
            search=query,
            **{'per-page': top_k, 'filter': ','.join(filters)},
        ),
        headers={'User-Agent': 'RRS-Editor-Agent/1.0'},
        timeout=int(os.getenv('OPENALEX_TIMEOUT', '20')),
    )
    response.raise_for_status()
    payload = response.json()
    results = [_openalex_work(work) for work in payload.get('results', [])[:top_k]]
    return {
        'query': query,
        'provider': 'OpenAlex',
        'result_count': len(results),
        'total_count': int((payload.get('meta') or {}).get('count') or 0),
        'results': results,
    }


@mcp.tool()
def verify_crossref_metadata(
    doi: str,
    expected_title: str = '',
    expected_year: int = 0,
    expected_authors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Resolve a DOI in Crossref and compare its authoritative metadata with optional expected values."""
    normalized_doi = _normalize_doi(doi)
    if not normalized_doi:
        raise ValueError('doi must not be empty')
    response = requests.get(
        f"{os.getenv('CROSSREF_BASE_URL', 'https://api.crossref.org').rstrip('/')}/works/{quote(normalized_doi, safe='')}",
        params={'mailto': os.getenv('CROSSREF_MAILTO', '').strip()} if os.getenv('CROSSREF_MAILTO', '').strip() else None,
        headers=_crossref_headers(),
        timeout=int(os.getenv('CROSSREF_TIMEOUT', '20')),
    )
    response.raise_for_status()
    item = response.json().get('message') or {}
    title = _first(item.get('title'))
    authors = [_crossref_author(author) for author in item.get('author') or []]
    publication_year = _year(item)

    supplied_checks = []
    checks: Dict[str, Any] = {'doi_resolves': True}
    if expected_title:
        title_score = SequenceMatcher(None, _normalized_text(expected_title), _normalized_text(title)).ratio()
        checks['title_similarity'] = round(title_score, 4)
        checks['title_match'] = title_score >= 0.9
        supplied_checks.append(checks['title_match'])
    if expected_year:
        checks['year_match'] = publication_year == int(expected_year)
        supplied_checks.append(checks['year_match'])
    if expected_authors:
        actual_names = [_normalized_text(author['name']) for author in authors]
        missing = [
            name for name in expected_authors
            if not any(_normalized_text(name) in actual or actual in _normalized_text(name) for actual in actual_names)
        ]
        checks['missing_expected_authors'] = missing
        checks['authors_match'] = not missing
        supplied_checks.append(checks['authors_match'])

    return {
        'provider': 'Crossref',
        'doi': normalized_doi,
        'metadata': {
            'title': title,
            'authors': authors,
            'publication_year': publication_year,
            'container_title': _first(item.get('container-title')),
            'publisher': item.get('publisher') or '',
            'type': item.get('type') or '',
            'url': item.get('URL') or f'https://doi.org/{normalized_doi}',
            'reference_count': int(item.get('reference-count') or 0),
            'cited_by_count': int(item.get('is-referenced-by-count') or 0),
        },
        'checks': checks,
        'overall_match': all(supplied_checks) if supplied_checks else True,
    }


@mcp.tool()
def lookup_orcid_author(name: str, affiliation: str = '', top_k: int = 5) -> Dict[str, Any]:
    """Find public ORCID identity candidates by author name and optional affiliation."""
    name = name.strip()
    if not name:
        raise ValueError('name must not be empty')
    top_k = max(1, min(int(top_k), 10))
    escaped_name = name.replace('\\', '\\\\').replace('"', '\\"')
    query = f'given-and-family-names:"{escaped_name}"'
    if affiliation.strip():
        escaped_affiliation = affiliation.strip().replace('\\', '\\\\').replace('"', '\\"')
        query += f' AND affiliation-org-name:"{escaped_affiliation}"'
    response = requests.get(
        f"{os.getenv('ORCID_BASE_URL', 'https://pub.orcid.org/v3.0').rstrip('/')}/expanded-search/",
        params={'q': query, 'rows': top_k},
        headers={
            'Accept': 'application/vnd.orcid+json',
            'User-Agent': 'RRS-Editor-Agent/1.0',
        },
        timeout=int(os.getenv('ORCID_TIMEOUT', '20')),
    )
    response.raise_for_status()
    payload = response.json()
    results = []
    for item in payload.get('expanded-result') or []:
        orcid = item.get('orcid-id') or ''
        display_name = item.get('credit-name') or ' '.join(
            part for part in (item.get('given-names'), item.get('family-names')) if part
        )
        results.append({
            'orcid': orcid,
            'orcid_url': f'https://orcid.org/{orcid}' if orcid else '',
            'name': display_name,
            'other_names': item.get('other-name') or [],
            'institutions': item.get('institution-name') or [],
        })
    return {
        'provider': 'ORCID',
        'query_name': name,
        'query_affiliation': affiliation.strip(),
        'result_count': len(results),
        'total_count': int(payload.get('num-found') or 0),
        'results': results,
        'requires_editor_confirmation': True,
    }


@mcp.tool()
def check_retraction_status(doi: str) -> Dict[str, Any]:
    """Check Crossref update notices and OpenAlex retraction metadata for a DOI."""
    normalized_doi = _normalize_doi(doi)
    if not normalized_doi:
        raise ValueError('doi must not be empty')
    errors = []
    notices = []
    openalex_retracted: Optional[bool] = None

    try:
        response = requests.get(
            f"{os.getenv('CROSSREF_BASE_URL', 'https://api.crossref.org').rstrip('/')}/works",
            params={'filter': f'updates:{normalized_doi}', 'rows': 20},
            headers=_crossref_headers(),
            timeout=int(os.getenv('CROSSREF_TIMEOUT', '20')),
        )
        response.raise_for_status()
        for item in (response.json().get('message') or {}).get('items') or []:
            for update in item.get('update-to') or []:
                if _normalize_doi(update.get('DOI') or '') != normalized_doi:
                    continue
                notices.append({
                    'notice_doi': _normalize_doi(item.get('DOI') or ''),
                    'title': _first(item.get('title')),
                    'update_type': str(update.get('type') or '').lower(),
                    'label': update.get('label') or '',
                    'source': update.get('source') or 'Crossref',
                    'publication_year': _year(item),
                    'url': item.get('URL') or '',
                })
    except requests.RequestException as exc:
        errors.append(f'Crossref: {exc}')

    try:
        response = requests.get(
            f"{os.getenv('OPENALEX_BASE_URL', 'https://api.openalex.org').rstrip('/')}/works",
            params=_openalex_parameters(filter=f'doi:{normalized_doi}', **{'per-page': 1}),
            headers={'User-Agent': 'RRS-Editor-Agent/1.0'},
            timeout=int(os.getenv('OPENALEX_TIMEOUT', '20')),
        )
        response.raise_for_status()
        works = response.json().get('results') or []
        openalex_retracted = bool(works[0].get('is_retracted')) if works else None
    except requests.RequestException as exc:
        errors.append(f'OpenAlex: {exc}')

    if len(errors) == 2:
        raise RuntimeError('; '.join(errors))
    update_types = {notice['update_type'] for notice in notices}
    if openalex_retracted or 'retraction' in update_types:
        status = 'retracted'
    elif update_types & {'expression-of-concern', 'expression_of_concern'}:
        status = 'expression_of_concern'
    elif update_types & {'correction', 'erratum'}:
        status = 'corrected'
    else:
        status = 'no_notice_found'
    return {
        'providers': ['Crossref', 'OpenAlex'],
        'doi': normalized_doi,
        'retraction_status': status,
        'is_retracted': status == 'retracted',
        'openalex_is_retracted': openalex_retracted,
        'notices': notices,
        'provider_errors': errors,
        'caveat': 'No notice found does not prove that the work is free of integrity concerns.',
    }


if __name__ == '__main__':
    mcp.run(transport='stdio')
