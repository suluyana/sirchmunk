# Sirchmunk Project Structure

## Directory Layout

```
sirchmunk/
├── src/
│   ├── api/                    # FastAPI backend
│   │   ├── routes/
│   │   │   ├── search.py       # Search endpoints
│   │   │   ├── knowledge.py    # Knowledge management
│   │   │   └── health.py       # Health check
│   │   ├── middleware/
│   │   │   ├── auth.py         # Authentication middleware
│   │   │   └── cors.py         # CORS configuration
│   │   └── main.py             # Application entry
│   │
│   ├── agentic/                # Agentic search components
│   │   ├── react_agent.py      # ReAct pattern implementation
│   │   ├── tools.py            # Tool definitions
│   │   └── prompts.py          # Agent prompts
│   │
│   ├── cli/                    # Command-line interface
│   │   ├── cli.py              # Main CLI entry
│   │   └── web_launcher.py     # Web UI launcher
│   │
│   ├── insight/                # Text analysis
│   │   └── text_insights.py    # Insight generation
│   │
│   ├── learnings/              # Knowledge learning
│   │   ├── evidence_processor.py
│   │   └── knowledge_base.py
│   │
│   ├── llm/                    # LLM integrations
│   │   ├── openai_chat.py      # OpenAI client
│   │   └── prompts.py          # LLM prompts
│   │
│   ├── retrieve/               # Retrieval systems
│   │   ├── base.py             # Base retriever
│   │   └── text_retriever.py   # Text retrieval
│   │
│   ├── scan/                   # File scanning
│   │   ├── base.py             # Base scanner
│   │   ├── file_scanner.py     # File scanner
│   │   └── dir_scanner.py      # Directory scanner
│   │
│   ├── schema/                 # Data models
│   │   ├── request.py          # Request models
│   │   ├── response.py         # Response models
│   │   └── knowledge.py        # Knowledge models
│   │
│   ├── storage/                # Storage systems
│   │   ├── duckdb.py           # DuckDB manager
│   │   └── knowledge_storage.py
│   │
│   └── utils/                  # Utilities
│       ├── file_utils.py       # File operations
│       ├── log_utils.py        # Logging setup
│       └── utils.py            # General utilities
│
├── web/                        # Frontend (Next.js)
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   └── utils/
│   └── public/
│
├── tests/                      # Test suites
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── docs/                       # Documentation
│   ├── en/
│   └── zh/
│
├── scripts/                    # Utility scripts
├── configs/                    # Configuration files
└── data/                       # Test data
```

## Module Dependencies

```
                    ┌─────────────┐
                    │   CLI/Web   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │  Agentic │ │   API    │ │  Scanner │
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │            │            │
             │      ┌─────┴─────┐      │
             │      │           │      │
             ▼      ▼           ▼      ▼
        ┌─────────────────────────────────┐
        │         Search Engine           │
        └─────────────────────────────────┘
             │            │            │
             ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │   LLM    │ │ Retrieve │ │ Storage  │
        └──────────┘ └──────────┘ └──────────┘
```

## Key Design Patterns

1. **Repository Pattern**: Data access abstracted behind interfaces
2. **Strategy Pattern**: Interchangeable retrieval strategies
3. **Observer Pattern**: Event-driven architecture for updates
4. **Factory Pattern**: LLM client creation based on configuration

## Technology Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.10+, FastAPI |
| Frontend | TypeScript, React, Next.js 14 |
| Database | DuckDB |
| Search | ripgrep, ripgrep-all |
| LLM | OpenAI, Anthropic |
| Embeddings | Sentence Transformers |
| Cache | LRU with disk persistence |

---

Last Updated: 2024-12-15
