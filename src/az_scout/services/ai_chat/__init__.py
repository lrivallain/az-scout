"""AI Chat service – Azure OpenAI with tool-calling over az-scout functions.

Provides a streaming chat endpoint that uses Azure OpenAI to answer questions
about Azure infrastructure, calling az-scout tool functions when needed.

Requires environment variables:
    AZURE_OPENAI_ENDPOINT    – e.g. https://my-resource.openai.azure.com
    AZURE_OPENAI_API_KEY     – API key for the Azure OpenAI resource
    AZURE_OPENAI_DEPLOYMENT  – deployment name (e.g. gpt-4o)
"""

from __future__ import annotations

from az_scout.services.ai_chat._config import (
    AZURE_OPENAI_API_KEY as AZURE_OPENAI_API_KEY,
)
from az_scout.services.ai_chat._config import (
    AZURE_OPENAI_API_VERSION as AZURE_OPENAI_API_VERSION,
)
from az_scout.services.ai_chat._config import (
    AZURE_OPENAI_DEPLOYMENT as AZURE_OPENAI_DEPLOYMENT,
)
from az_scout.services.ai_chat._config import (
    AZURE_OPENAI_ENDPOINT as AZURE_OPENAI_ENDPOINT,
)
from az_scout.services.ai_chat._dispatch import (
    _post_process_tool_result as _post_process_tool_result,
)
from az_scout.services.ai_chat._dispatch import (
    _truncate_tool_result as _truncate_tool_result,
)
from az_scout.services.ai_chat._prompts import (
    SYSTEM_PROMPT as SYSTEM_PROMPT,
)
from az_scout.services.ai_chat._prompts import (
    _build_system_prompt as _build_system_prompt,
)
from az_scout.services.ai_chat._stream import chat_stream as chat_stream
from az_scout.services.ai_chat._tools import (
    TOOL_DEFINITIONS as TOOL_DEFINITIONS,
)
from az_scout.services.ai_chat._tools import (
    _build_openai_tools as _build_openai_tools,
)
from az_scout.services.ai_chat._tools import (
    _mcp_schema_to_openai as _mcp_schema_to_openai,
)
from az_scout.services.ai_chat._tools import (
    refresh_tool_definitions as refresh_tool_definitions,
)


def is_chat_enabled() -> bool:
    """Return True if all required Azure OpenAI env vars are set."""
    return bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT)
