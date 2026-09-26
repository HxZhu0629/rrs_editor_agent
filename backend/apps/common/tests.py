from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from .llm import build_chat_model, generate_text


class DeepSeekClientTests(SimpleTestCase):
    @override_settings(DEEPSEEK_API_KEY='')
    def test_api_key_is_required(self):
        with self.assertRaises(ImproperlyConfigured):
            build_chat_model()

    @override_settings(
        DEEPSEEK_API_KEY='test-key',
        DEEPSEEK_BASE_URL='https://api.deepseek.com',
        DEEPSEEK_CHAT_MODEL='deepseek-chat',
        DEEPSEEK_CHAT_TIMEOUT=120,
        DEEPSEEK_MAX_RETRIES=2,
    )
    @patch('langchain_openai.ChatOpenAI')
    def test_chat_model_uses_deepseek_openai_compatible_api(self, chat_model_class):
        build_chat_model()

        chat_model_class.assert_called_once_with(
            model='deepseek-chat',
            api_key='test-key',
            base_url='https://api.deepseek.com',
            timeout=120,
            max_retries=2,
            temperature=0,
        )

    @patch('apps.common.llm.build_chat_model')
    def test_json_mode_uses_provider_response_format(self, build_model):
        model = Mock()
        model.bind.return_value = model
        model.invoke.return_value = SimpleNamespace(content='{"ok": true}')
        build_model.return_value = model

        result = generate_text('Return JSON.', json_mode=True)

        self.assertEqual(result, '{"ok": true}')
        model.bind.assert_called_once_with(response_format={'type': 'json_object'})
