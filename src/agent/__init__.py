"""New LangGraph Agent.

This module defines a custom graph.
"""

from .graph import get_graph


async def initialize():
    return await get_graph()


__all__ = ["get_graph", "initialize"]