from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def build_chat_model():
    if not settings.DEEPSEEK_API_KEY:
        raise ImproperlyConfigured('DEEPSEEK_API_KEY is not configured.')

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.DEEPSEEK_CHAT_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
        timeout=settings.DEEPSEEK_CHAT_TIMEOUT,
        max_retries=settings.DEEPSEEK_MAX_RETRIES,
        temperature=0,
    )


def generate_text(prompt: str, *, json_mode: bool = False) -> str:
    model = build_chat_model()
    if json_mode:
        model = model.bind(response_format={'type': 'json_object'})
    response = model.invoke(prompt)
    return str(response.content or '').strip()
