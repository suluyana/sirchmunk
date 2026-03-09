# Copyright (c) ModelScope Contributors. All rights reserved.
"""
Sirchmunk - Enterprise AI Search Engine

A powerful search system that combines agentic retrieval
with knowledge clustering for intelligent data discovery.
"""

import asyncio
import logging
from typing import Optional

from .search import AgenticSearch
from .llm import OpenAIChat
from .storage import KnowledgeStorage

__version__ = '1.0.0'
__author__ = 'Sirchmunk Team'
__email__ = 'team@sirchmunk.com'

# Configure default logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def create_searcher(
    api_key: Optional[str] = None,
    model: str = "gpt-4-turbo-preview",
    base_url: Optional[str] = None,
    **kwargs
) -> AgenticSearch:
    """
    Create a configured AgenticSearch instance.

    Args:
        api_key: LLM API key (falls back to env var LLM_API_KEY)
        model: Model name to use
        base_url: Custom API base URL
        **kwargs: Additional arguments for AgenticSearch

    Returns:
        Configured AgenticSearch instance
    """
    llm = OpenAIChat(
        model=model,
        api_key=api_key,
        base_url=base_url
    )
    return AgenticSearch(llm=llm, **kwargs)


def search(
    query: str,
    paths: list[str],
    api_key: Optional[str] = None,
    mode: str = "FAST",
    **kwargs
) -> str:
    """
    Perform a synchronous search.

    Args:
        query: Search query string
        paths: List of paths to search
        api_key: LLM API key
        mode: Search mode (FAST, DEEP, FILENAME_ONLY)
        **kwargs: Additional search parameters

    Returns:
        Search result summary
    """
    searcher = create_searcher(api_key=api_key)
    return asyncio.run(searcher.search(query=query, paths=paths, mode=mode))


__all__ = [
    'AgenticSearch',
    'OpenAIChat',
    'KnowledgeStorage',
    'create_searcher',
    'search',
    '__version__',
]
