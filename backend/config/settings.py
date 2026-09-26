import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'dev-secret-key-change-me')
DEBUG = os.getenv('DJANGO_DEBUG', '1') == '1'
ALLOWED_HOSTS = [h.strip() for h in os.getenv('DJANGO_ALLOWED_HOSTS', '*').split(',') if h.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'corsheaders',
    'rest_framework',
    'apps.common',
    'apps.manuscripts',
    'apps.extraction',
    'apps.reviewers',
    'apps.recommendations',
    'apps.agent',
]

MIDDLEWARE = [
    'apps.common.telemetry.RequestTelemetryMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DB_ENGINE = os.getenv('DB_ENGINE', 'postgresql').lower()
if DB_ENGINE == 'sqlite':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('POSTGRES_DB', 'rrs'),
            'USER': os.getenv('POSTGRES_USER', 'rrs'),
            'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'rrs_password'),
            'HOST': os.getenv('POSTGRES_HOST', 'db'),
            'PORT': os.getenv('POSTGRES_PORT', '5432'),
            'OPTIONS': {'connect_timeout': 3, 'options': '-c statement_timeout=30000'},
        }
    }

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = os.getenv('TIME_ZONE', 'Asia/Shanghai')
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
}

CORS_ALLOW_ALL_ORIGINS = True

OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://host.docker.internal:11434')
OLLAMA_EMBED_MODEL = os.getenv('OLLAMA_EMBED_MODEL', 'nomic-embed-text:latest')
OLLAMA_EMBED_TIMEOUT = int(os.getenv('OLLAMA_EMBED_TIMEOUT', '120'))
OLLAMA_EMBED_DIMENSION = int(os.getenv('OLLAMA_EMBED_DIMENSION', '768'))
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
DEEPSEEK_CHAT_MODEL = os.getenv('DEEPSEEK_CHAT_MODEL', 'deepseek-chat')
LANGFUSE_TRACING_ENABLED = os.getenv('LANGFUSE_TRACING_ENABLED', '0') == '1'
DEEPSEEK_CHAT_TIMEOUT = int(os.getenv('DEEPSEEK_CHAT_TIMEOUT', '120'))
DEEPSEEK_MAX_RETRIES = int(os.getenv('DEEPSEEK_MAX_RETRIES', '2'))
RERANKER_MODEL = os.getenv('RERANKER_MODEL', '')
RERANKER_MAX_LENGTH = int(os.getenv('RERANKER_MAX_LENGTH', '512'))
RAG_QUERY_REWRITE_ENABLED = os.getenv('RAG_QUERY_REWRITE_ENABLED', '0') == '1'
RAG_HYDE_ENABLED = os.getenv('RAG_HYDE_ENABLED', '0') == '1'
RAG_RERANK_ENABLED = os.getenv('RAG_RERANK_ENABLED', '1') == '1'
RAG_RERANK_CANDIDATES = int(os.getenv('RAG_RERANK_CANDIDATES', '20'))
RAG_RRF_K = int(os.getenv('RAG_RRF_K', '60'))
RAG_CRAG_ENABLED = os.getenv('RAG_CRAG_ENABLED', '0') == '1'
RAG_WEB_SEARCH_URL = os.getenv('RAG_WEB_SEARCH_URL', '')
RAG_SELF_REFLECTION_ENABLED = os.getenv('RAG_SELF_REFLECTION_ENABLED', '0') == '1'
AGENT_MAX_TURNS = int(os.getenv('AGENT_MAX_TURNS', '6'))
AGENT_MAX_TOOL_CALLS = int(os.getenv('AGENT_MAX_TOOL_CALLS', '12'))
AGENT_MAX_TOOL_RESULT_CHARS = int(os.getenv('AGENT_MAX_TOOL_RESULT_CHARS', '8000'))
AGENT_MAX_CONTEXT_CHARS = int(os.getenv('AGENT_MAX_CONTEXT_CHARS', '40000'))
AGENT_MAX_CONTEXT_TOKENS = int(os.getenv('AGENT_MAX_CONTEXT_TOKENS', '12000'))
AGENT_CONTEXT_SNIP_RATIO = float(os.getenv('AGENT_CONTEXT_SNIP_RATIO', '0.55'))
AGENT_CONTEXT_MICRO_RATIO = float(os.getenv('AGENT_CONTEXT_MICRO_RATIO', '0.70'))
AGENT_CONTEXT_COLLAPSE_RATIO = float(os.getenv('AGENT_CONTEXT_COLLAPSE_RATIO', '0.82'))
AGENT_CONTEXT_AUTO_RATIO = float(os.getenv('AGENT_CONTEXT_AUTO_RATIO', '0.90'))
AGENT_CONTEXT_RECENT_BLOCKS = int(os.getenv('AGENT_CONTEXT_RECENT_BLOCKS', '6'))
AGENT_COMPACTION_MAX_FAILURES = int(os.getenv('AGENT_COMPACTION_MAX_FAILURES', '3'))
AGENT_COMPACTION_SUMMARY_CHARS = int(os.getenv('AGENT_COMPACTION_SUMMARY_CHARS', '4000'))
AGENT_MEMORY_MAX_CHARS = int(os.getenv('AGENT_MEMORY_MAX_CHARS', '12000'))
AGENT_MANAGED_MEMORY_FILE = os.getenv('AGENT_MANAGED_MEMORY_FILE', '')
AGENT_USER_MEMORY_FILE = os.getenv('AGENT_USER_MEMORY_FILE', '')
AGENT_PROJECT_ROOT = os.getenv('AGENT_PROJECT_ROOT', '')
AGENT_PROJECT_MEMORY_FILE = os.getenv('AGENT_PROJECT_MEMORY_FILE', '')
AGENT_LOCAL_MEMORY_FILE = os.getenv('AGENT_LOCAL_MEMORY_FILE', '')
AGENT_TEAM_MEMORY_FILE = os.getenv('AGENT_TEAM_MEMORY_FILE', '')
ACADEMIC_MCP_ENABLED = os.getenv('ACADEMIC_MCP_ENABLED', '1') == '1'
AGENT_DISABLED_TOOLS = tuple(name.strip() for name in os.getenv('AGENT_DISABLED_TOOLS', '').split(',') if name.strip())
ACADEMIC_MCP_TIMEOUT = int(os.getenv('ACADEMIC_MCP_TIMEOUT', '30'))
OPENALEX_BASE_URL = os.getenv('OPENALEX_BASE_URL', 'https://api.openalex.org')
OPENALEX_MAILTO = os.getenv('OPENALEX_MAILTO', '')
OPENALEX_TIMEOUT = int(os.getenv('OPENALEX_TIMEOUT', '20'))
CROSSREF_BASE_URL = os.getenv('CROSSREF_BASE_URL', 'https://api.crossref.org')
CROSSREF_MAILTO = os.getenv('CROSSREF_MAILTO', OPENALEX_MAILTO)
CROSSREF_TIMEOUT = int(os.getenv('CROSSREF_TIMEOUT', '20'))
ORCID_BASE_URL = os.getenv('ORCID_BASE_URL', 'https://pub.orcid.org/v3.0')
ORCID_TIMEOUT = int(os.getenv('ORCID_TIMEOUT', '20'))
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/1')
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = int(os.getenv('CELERY_TASK_SOFT_TIME_LIMIT', '840'))
CELERY_TASK_TIME_LIMIT = int(os.getenv('CELERY_TASK_TIME_LIMIT', '900'))
CELERY_TASK_PUBLISH_RETRY = False
CELERY_BROKER_CONNECTION_TIMEOUT = 3
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'socket_connect_timeout': 3,
    'socket_timeout': 3,
    'visibility_timeout': max(3600, CELERY_TASK_TIME_LIMIT + 120),
}
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_BEAT_SCHEDULE = {
    'reconcile-agent-runs': {'task': 'apps.agent.tasks.reconcile_agent_runs', 'schedule': 60.0},
}
AGENT_MAX_PENDING_RUNS = int(os.getenv('AGENT_MAX_PENDING_RUNS', '20'))
AGENT_QUEUE_TIMEOUT = int(os.getenv('AGENT_QUEUE_TIMEOUT', '300'))
METRICS_TOKEN = os.getenv('METRICS_TOKEN', '')
WORKER_METRICS_PORT = int(os.getenv('WORKER_METRICS_PORT', '0'))
RAG_EMBED_CIRCUIT_FAILURES = int(os.getenv('RAG_EMBED_CIRCUIT_FAILURES', '3'))
RAG_EMBED_CIRCUIT_COOLDOWN = int(os.getenv('RAG_EMBED_CIRCUIT_COOLDOWN', '30'))
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'json': {'()': 'apps.common.telemetry.JsonFormatter'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'json'}},
    'loggers': {'rrs.events': {'handlers': ['console'], 'level': 'INFO', 'propagate': False}},
}
