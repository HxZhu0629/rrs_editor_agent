import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from django.conf import settings


class MCPToolError(RuntimeError):
    def __init__(self, message, code='mcp_error'):
        super().__init__(message)
        self.code = code


def _decode_call_result(result) -> Dict[str, Any]:
    if getattr(result, 'isError', False):
        message = 'MCP tool execution failed.'
        for item in getattr(result, 'content', []):
            if getattr(item, 'type', '') == 'text' and getattr(item, 'text', ''):
                message = item.text
                break
        raise MCPToolError(message)

    structured = getattr(result, 'structuredContent', None)
    if isinstance(structured, dict):
        if set(structured) == {'result'} and isinstance(structured['result'], dict):
            return structured['result']
        return structured

    text_parts = [
        item.text
        for item in getattr(result, 'content', [])
        if getattr(item, 'type', '') == 'text' and getattr(item, 'text', '')
    ]
    if not text_parts:
        return {}
    text = '\n'.join(text_parts)
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        return {'content': text}
    return decoded if isinstance(decoded, dict) else {'content': decoded}


class AcademicMCPClient:
    server_name = 'academic-evidence'
    allowed_tools = {
        'search_openalex_works',
        'resolve_openalex_access',
        'verify_crossref_metadata',
        'lookup_orcid_author',
        'check_retraction_status',
    }

    def __init__(self, server_script: str = '', timeout: int = 30):
        default_script = Path(settings.BASE_DIR) / 'mcp_servers' / 'academic_server.py'
        self.server_script = str(server_script or default_script)
        self.timeout = timeout

    async def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise MCPToolError('The MCP Python SDK is not installed.') from exc

        if not Path(self.server_script).is_file():
            raise MCPToolError(f'MCP server script not found: {self.server_script}')

        environment = {
            **os.environ,
            'OPENALEX_BASE_URL': settings.OPENALEX_BASE_URL,
            'OPENALEX_MAILTO': settings.OPENALEX_MAILTO,
            'OPENALEX_TIMEOUT': str(settings.OPENALEX_TIMEOUT),
            'CROSSREF_BASE_URL': settings.CROSSREF_BASE_URL,
            'CROSSREF_MAILTO': settings.CROSSREF_MAILTO,
            'CROSSREF_TIMEOUT': str(settings.CROSSREF_TIMEOUT),
            'ORCID_BASE_URL': settings.ORCID_BASE_URL,
            'ORCID_TIMEOUT': str(settings.ORCID_TIMEOUT),
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[self.server_script],
            env=environment,
        )
        try:
            async with asyncio.timeout(self.timeout):
                async with stdio_client(parameters, errlog=subprocess.DEVNULL) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        names = {tool.name for tool in tools.tools}
                        if tool_name not in self.allowed_tools or tool_name not in names:
                            raise MCPToolError(
                                f'MCP server {self.server_name} does not expose allowed tool {tool_name}.'
                            )
                        result = await session.call_tool(tool_name, arguments=arguments)
        except TimeoutError as exc:
            raise MCPToolError(f'MCP call timed out after {self.timeout} seconds.', code='timeout') from exc
        return _decode_call_result(result)

    def _invoke(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not getattr(settings, 'ACADEMIC_MCP_ENABLED', True):
            return {
                'status': 'disabled',
                'source_protocol': 'mcp',
                'server': self.server_name,
                'results': [],
            }
        result = asyncio.run(self._call_tool(tool_name, arguments))
        return {
            'status': 'success',
            **result,
            'source_protocol': 'mcp',
            'server': self.server_name,
            'content_trust': 'untrusted_external',
        }

    def search_papers(self, **arguments) -> Dict[str, Any]:
        return self._invoke('search_openalex_works', arguments)

    def resolve_access(self, **arguments) -> Dict[str, Any]:
        return self._invoke('resolve_openalex_access', arguments)

    def verify_metadata(self, **arguments) -> Dict[str, Any]:
        return self._invoke('verify_crossref_metadata', arguments)

    def lookup_author(self, **arguments) -> Dict[str, Any]:
        return self._invoke('lookup_orcid_author', arguments)

    def check_retraction(self, **arguments) -> Dict[str, Any]:
        return self._invoke('check_retraction_status', arguments)
