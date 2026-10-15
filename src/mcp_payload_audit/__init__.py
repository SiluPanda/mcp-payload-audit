"""Offline inspection of saved MCP tool-result payloads."""

from .core import AuditError, analyze_result, loads_document

__all__ = ["AuditError", "analyze_result", "loads_document"]
__version__ = "0.1.0"
