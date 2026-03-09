# Sirchmunk Project Documentation

## Overview

Sirchmunk is an intelligent search system designed for enterprise raw data intelligence. It leverages agentic retrieval to find and synthesize information from diverse data sources.

## Architecture

### Core Components

1. **AgenticSearch** - Main search orchestrator using ReAct pattern
2. **KnowledgeStorage** - DuckDB-based knowledge clustering and storage
3. **TextRetriever** - Hybrid search combining keyword and semantic retrieval
4. **OpenAIChat** - LLM interface for reasoning and response generation

### Search Pipeline

```
User Query → Keyword Extraction → File Retrieval → Content Extraction →
Knowledge Clustering → LLM Synthesis → Answer
```

## Key Features

- **Multi-format Support**: PDF, DOCX, PPTX, XLSX, images, code files
- **Agentic Retrieval**: Autonomous tool selection and iteration
- **Knowledge Clustering**: Groups related evidence for coherent answers
- **Hybrid Search**: Combines full-text search with semantic similarity

## Installation

```bash
pip install sirchmunk
```

## Usage

```python
from sirchmunk import AgenticSearch
from sirchmunk.llm import OpenAIChat

llm = OpenAIChat(model="gpt-4")
searcher = AgenticSearch(llm=llm)

result = await searcher.search(
    query="How does authentication work?",
    paths=["./docs", "./src"],
    mode="FAST"
)
print(result)
```

## Configuration

See `docs/configuration.md` for detailed settings.

## License

MIT License
