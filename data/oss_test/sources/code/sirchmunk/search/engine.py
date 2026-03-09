# Copyright (c) ModelScope Contributors. All rights reserved.
"""
Search Engine Core Module

Provides the main search functionality including:
- Full-text search with ripgrep
- Semantic search with embeddings
- Hybrid ranking combining both approaches
"""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger

from sirchmunk.llm import OpenAIChat
from sirchmunk.storage import KnowledgeStorage
from sirchmunk.retrieve import TextRetriever


@dataclass
class SearchResult:
    """Individual search result item."""
    file_path: str
    content: str
    relevance_score: float
    match_type: str  # "keyword", "semantic", or "hybrid"
    line_numbers: List[int] = field(default_factory=list)
    metadata: Dict[str, any] = field(default_factory=dict)


@dataclass
class SearchResponse:
    """Complete search response."""
    query: str
    results: List[SearchResult]
    total_time_ms: float
    files_searched: int
    tokens_used: int
    answer: Optional[str] = None


class AgenticSearch:
    """
    Agentic search engine for Sirchmunk.

    Combines multiple retrieval strategies with LLM reasoning
    to provide comprehensive search results.
    """

    def __init__(
        self,
        llm: OpenAIChat,
        work_path: Optional[str] = None,
        verbose: bool = False
    ):
        """
        Initialize AgenticSearch.

        Args:
            llm: LLM client for reasoning
            work_path: Working directory for cache
            verbose: Enable verbose logging
        """
        self.llm = llm
        self.work_path = Path(work_path) if work_path else Path.home() / ".sirchmunk"
        self.verbose = verbose

        # Initialize components
        self.knowledge_storage = KnowledgeStorage(
            work_path=str(self.work_path)
        )
        self.text_retriever = TextRetriever()

        logger.info(f"AgenticSearch initialized at {self.work_path}")

    async def search(
        self,
        query: str,
        paths: List[str],
        mode: str = "FAST",
        return_context: bool = False
    ) -> str:
        """
        Execute a search query.

        Args:
            query: Search query string
            paths: Paths to search
            mode: Search mode (FAST, DEEP, FILENAME_ONLY)
            return_context: Return full context instead of answer

        Returns:
            Search result summary
        """
        import time
        start_time = time.time()

        logger.info(f"Starting search: {query} (mode={mode})")

        # Step 1: Extract keywords using LLM
        keywords = await self._extract_keywords(query)

        # Step 2: Perform file search
        matched_files = await self._find_files(keywords, paths, mode)

        # Step 3: Extract content from matched files
        contents = await self._extract_content(matched_files)

        # Step 4: Rank and cluster results
        ranked_results = await self._rank_results(contents, query)

        # Step 5: Generate answer using LLM
        answer = await self._generate_answer(query, ranked_results)

        elapsed_ms = (time.time() - start_time) * 1000

        logger.info(f"Search completed in {elapsed_ms:.0f}ms")

        return answer

    async def _extract_keywords(self, query: str) -> List[str]:
        """Extract search keywords from query using LLM."""
        prompt = f"""Extract 3-5 key search terms from this query:

Query: {query}

Return only the keywords, one per line."""

        response = await self.llm.generate(prompt)
        keywords = [k.strip() for k in response.strip().split('\n') if k.strip()]
        logger.debug(f"Extracted keywords: {keywords}")
        return keywords

    async def _find_files(
        self,
        keywords: List[str],
        paths: List[str],
        mode: str
    ) -> List[Path]:
        """Find files matching keywords."""
        matched = []

        for path in paths:
            root = Path(path)
            if not root.exists():
                logger.warning(f"Path not found: {path}")
                continue

            # Use ripgrep for fast file search
            for keyword in keywords:
                files = await self._search_with_ripgrep(keyword, root, mode)
                matched.extend(files)

        # Deduplicate
        unique_files = list(set(matched))
        logger.info(f"Found {len(unique_files)} matching files")
        return unique_files

    async def _search_with_ripgrep(
        self,
        keyword: str,
        root: Path,
        mode: str
    ) -> List[Path]:
        """Search files using ripgrep."""
        import subprocess

        matched_files = []

        try:
            if mode == "FILENAME_ONLY":
                # Search only filenames
                result = subprocess.run(
                    ["rg", "--files", "-g", f"*{keyword}*"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            else:
                # Search file contents
                result = subprocess.run(
                    ["rg", "-l", keyword],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=30
                )

            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line:
                        matched_files.append(root / line)

        except subprocess.TimeoutExpired:
            logger.warning(f"Ripgrep search timed out for: {keyword}")
        except FileNotFoundError:
            logger.warning("ripgrep (rg) not found in PATH")

        return matched_files

    async def _extract_content(self, files: List[Path]) -> Dict[str, str]:
        """Extract text content from files."""
        contents = {}

        for file_path in files:
            try:
                # Use kreuzberg for multi-format extraction
                from kreuzberg import extract_file
                result = await extract_file(str(file_path))
                contents[str(file_path)] = result.text_content
            except Exception as e:
                logger.warning(f"Failed to extract {file_path}: {e}")

        return contents

    async def _rank_results(
        self,
        contents: Dict[str, str],
        query: str
    ) -> List[SearchResult]:
        """Rank search results by relevance."""
        results = []

        for file_path, content in contents.items():
            # Calculate relevance based on keyword frequency and content length
            words = content.lower().split()
            query_words = query.lower().split()

            matches = sum(1 for w in query_words if w in words)
            score = matches / len(query_words) if query_words else 0

            results.append(SearchResult(
                file_path=file_path,
                content=content[:500],  # Truncate for response
                relevance_score=score,
                match_type="keyword"
            ))

        # Sort by relevance
        results.sort(key=lambda x: x.relevance_score, reverse=True)
        return results[:10]  # Top 10 results

    async def _generate_answer(
        self,
        query: str,
        results: List[SearchResult]
    ) -> str:
        """Generate answer from search results using LLM."""
        context = "\n\n".join([
            f"=== {r.file_path} (relevance: {r.relevance_score:.2f}) ===\n{r.content}"
            for r in results[:5]  # Use top 5 results
        ])

        prompt = f"""Based on the following search results, answer the query.

Query: {query}

Search Results:
{context}

Provide a clear, concise answer. If the information is not found, say so."""

        response = await self.llm.generate(prompt)
        return response
