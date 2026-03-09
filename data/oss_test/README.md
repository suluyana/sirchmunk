# OSS Hybrid Architecture Test Data

This directory contains test data for the Sirchmunk OSS hybrid search architecture.

## Directory Structure

```
oss_test/
├── archives/                    # Historical documents (5 levels deep)
│   ├── 2024/
│   │   ├── financial_report_2024.txt
│   │   ├── product_roadmap_review.md
│   │   ├── chinese_classic_sample.pdf    # 红楼梦 sample (10 pages)
│   │   └── newsletters/
│   │       └── december_2024.html
│   └── 2025/
│       ├── governance/
│       │   └── board_minutes_jan_2025.txt
│       └── strategy/
│           └── strategic_plan_2025.md
│
├── configs/                     # Configuration files
│   ├── package.json             # npm package configuration
│   ├── settings.json            # Application settings
│   ├── sirchmunk.yaml           # Sirchmunk YAML config
│   └── openapi-spec.json        # OpenAPI 3.0 specification
│
├── docs/                        # Documentation (5 levels deep)
│   ├── README.md
│   ├── authentication.md
│   ├── api-reference.md
│   ├── architecture/
│   │   └── project-structure.md
│   └── policies/
│       └── code-of-conduct.md
│
├── reports/                     # Business reports
│   ├── quarterly_report_q4_2024.txt
│   ├── employee_directory.csv
│   ├── system.log
│   └── annual_report_2024.pdf    # 50-page annual report
│
├── sources/                     # Source code (6 levels deep)
│   └── code/
│       └── sirchmunk/
│           ├── __init__.py
│           ├── auth/
│           │   └── token_manager.py
│           ├── config/
│           │   └── settings.py
│           ├── search/
│           │   └── engine.py
│           └── api/
│               └── middleware/
│                   └── auth.py
│
└── web/                         # Web assets (5 levels deep)
    ├── robots.txt
    ├── sitemap.xml
    └── html/
        ├── index.html
        ├── auth/
        │   └── login.html
        └── guides/
            └── user-guide-zh.html
```

## File Formats

| Format | Count | Extensions |
|--------|-------|------------|
| Markdown | 8 | .md |
| Python | 5 | .py |
| Text | 4 | .txt |
| JSON | 4 | .json |
| HTML | 4 | .html |
| PDF | 2 | .pdf |
| YAML | 1 | .yaml |
| XML | 1 | .xml |
| TypeScript/TSX | 1 | .tsx |
| Log | 1 | .log |
| CSV | 1 | .csv |
| CSS | 1 | .css |
| **Total** | **33** | |

## Maximum Directory Depth

**6 levels** (oss_test → sources → code → sirchmunk → api → middleware → auth.py)

## Usage

### Testing OSS Hybrid Search

```bash
# Sync test data to OSS (after configuring OSS credentials)
sirchmunk oss init --disk-size 100
sirchmunk oss sync --prefix oss_test/

# Search test data
sirchmunk oss search "authentication" --prefix oss_test/docs/
sirchmunk oss search "token" --file-types py
sirchmunk oss search "Q4 report" --prefix oss_test/reports/
```

### Testing with Local Files

```bash
# Test local search on test data
sirchmunk search "code of conduct" data/oss_test/docs/
sirchmunk search "financial report" data/oss_test/reports/
```

## Test Scenarios

### 1. Multi-format Search
Test searching across different file formats:
```bash
# Search for "authentication" across all formats
sirchmunk oss search "authentication" --prefix oss_test/
```

### 2. Deep Nesting
Test search performance with deeply nested files:
```bash
# Search in 6-level deep directory
sirchmunk oss search "middleware" --prefix oss_test/sources/code/
```

### 3. File Type Filtering
Test file type filtering:
```bash
# Only Python files
sirchmunk oss search "token" --file-types py

# Only documentation
sirchmunk oss search "API" --file-types md
```

### 4. Cache Behavior
Test cache filling and eviction:
```bash
# First search (cold cache)
sirchmunk oss search "authentication"

# Second search (warm cache)
sirchmunk oss search "authentication"

# Check cache stats
sirchmunk cache stats
```

## Content Summary

### Archives
- Financial reports with tables and metrics
- Product roadmap with checkboxes and timelines
- Board meeting minutes with formal structure
- Strategic plans with OKRs and budgets
- Company newsletters in HTML format
- **PDF: chinese_classic_sample.pdf** - 红楼梦 sample (10 pages, tests PDF extraction)

### Documentation
- README with architecture overview
- Authentication flow documentation
- API reference with examples
- Project structure documentation
- Code of conduct policy

### Source Code
- Python modules with docstrings
- TypeScript/React components
- CSS stylesheets with variables
- Configuration files (JSON, YAML)
- Middleware implementations

### Reports
- Quarterly business reports
- Employee directory (CSV)
- System logs with timestamps
- **PDF: annual_report_2024.pdf** - 50-page annual report (tests large file handling)

---

*Test data updated: 2026-03-09*
*Total files: 33*
*Total size: ~208KB*

## PDF Testing

### Testing PDF Extraction

```bash
# Search within PDF files
sirchmunk oss search "Dream of the Red Chamber" --file-types pdf
sirchmunk oss search "Chapter 1" --prefix oss_test/archives/2024/

# Search annual report content
sirchmunk oss search "Annual Report" --prefix oss_test/reports/
```

### Large File Cache Behavior

PDF files are subject to the cache eviction policy:
- Files under `max_single_file_mb` (default 50MB) are cached based on available space
- The `annual_report_2024.pdf` (39KB) tests multi-page PDF extraction
- The `chinese_classic_sample.pdf` (7KB) tests classic literature content

When cache fills up, larger files are evicted first to maximize cache hit ratio.
