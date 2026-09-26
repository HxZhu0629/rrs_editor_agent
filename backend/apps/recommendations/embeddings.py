import math
from typing import Iterable, List

import requests
from django.conf import settings

from apps.manuscripts.models import Manuscript
from apps.reviewers.models import ReviewerProfile


def _normalize_vector(vector: Iterable[float]) -> List[float]:
    values = [float(item) for item in vector]
    norm = math.sqrt(sum(item * item for item in values))
    if norm <= 0:
        return []
    return [item / norm for item in values]


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = list(left or [])
    right_values = list(right or [])
    if not left_values or not right_values or len(left_values) != len(right_values):
        return 0.0
    score = sum(float(a) * float(b) for a, b in zip(left_values, right_values))
    return max(-1.0, min(1.0, score))


def compose_manuscript_embedding_text(manuscript: Manuscript) -> str:
    keywords = ", ".join(str(item).strip() for item in (manuscript.keywords or []) if str(item).strip())
    author_names = ", ".join(
        link.author.name
        for link in manuscript.author_links.select_related("author").all()
        if getattr(link, "author", None)
    )
    parts = [
        f"Title: {manuscript.title}",
        f"Domain: {manuscript.domain}",
        f"Keywords: {keywords}",
        f"Authors: {author_names}",
        f"Abstract: {manuscript.abstract}",
    ]
    return "\n".join(part.strip() for part in parts if part.strip())


def compose_reviewer_embedding_text(reviewer: ReviewerProfile) -> str:
    keywords = ", ".join(str(item).strip() for item in (reviewer.expertise_keywords or []) if str(item).strip())
    affiliation = reviewer.affiliation.name if reviewer.affiliation else ""
    parts = [
        f"Reviewer: {reviewer.name}",
        f"Affiliation: {affiliation}",
        f"Keywords: {keywords}",
        f"Profile: {reviewer.expertise_text}",
    ]
    return "\n".join(part.strip() for part in parts if part.strip())


def compose_reviewer_paper_embedding_text(paper) -> str:
    keywords = ", ".join(str(item).strip() for item in (paper.keywords or []) if str(item).strip())
    parts = [
        f"Title: {paper.title}",
        f"Venue: {paper.venue}",
        f"Keywords: {keywords}",
        f"Abstract: {paper.abstract}",
    ]
    return "\n".join(part.strip() for part in parts if part.strip())


def embed_texts(inputs: List[str]) -> List[List[float]]:
    cleaned_inputs = [item.strip() for item in inputs if item and item.strip()]
    if not cleaned_inputs:
        return []

    payload = {
        "model": settings.OLLAMA_EMBED_MODEL,
        "input": cleaned_inputs if len(cleaned_inputs) > 1 else cleaned_inputs[0],
    }
    timeout = settings.OLLAMA_EMBED_TIMEOUT
    base_url = settings.OLLAMA_BASE_URL.rstrip("/")

    try:
        response = requests.post(f"{base_url}/api/embed", json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        vectors = data.get("embeddings")
        if vectors is None and data.get("embedding") is not None:
            vectors = [data["embedding"]]
        if not vectors:
            raise ValueError("empty embedding payload from /api/embed")
        return [_normalize_vector(vector) for vector in vectors]
    except Exception:
        vectors = []
        for text in cleaned_inputs:
            response = requests.post(
                f"{base_url}/api/embeddings",
                json={"model": settings.OLLAMA_EMBED_MODEL, "prompt": text},
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            vector = data.get("embedding") or []
            vectors.append(_normalize_vector(vector))
        return vectors


def embed_text(text: str) -> List[float]:
    vectors = embed_texts([text])
    return vectors[0] if vectors else []
