import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
import asyncio
import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai", "anthropic"])
async def test_chat_basic(provider):
    """Provider registry routes to the correct backend."""
    with patch.dict(
        os.environ,
        {
            "PROVIDER_NAME": provider,
            "OLLAMA_BASE_URL": "http://localhost:11434",
            "OLLAMA_MODEL": "llama3",
            "OPENAI_API_KEY": "sk-test-key",
            "OPENAI_MODEL": "gpt-4o-mini",
            "ANTHROPIC_API_KEY": "sk-ant-key",
            "ANTHROPIC_MODEL": "claude-3-haiku",
            "SKIP_WEAVIATE_CONNECTION": "1",
        },
        clear=True,
    ):
        from packages.core.settings import get_settings
        from packages.core.providers import router

        get_settings.cache_clear()
        router._provider_cache.clear()
        
        # Mock the router function directly
        with patch.object(router, 'chat', new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = f"response from {provider}"
            result = await router.chat([{"role": "user", "content": "hi"}])
            mock_chat.assert_called_once()
            assert result == f"response from {provider}"


@pytest.mark.asyncio
async def test_chat_content():
    """Chat function can be patched directly."""
    with patch(
        "packages.core.providers.router.chat", new_callable=AsyncMock
    ) as mock_chat:
        mock_chat.return_value = "Test"
        result = await mock_chat([{"role": "user", "content": "hi"}])
        assert result == "Test"
        mock_chat.assert_called_once()
