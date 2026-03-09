# OSS Hybrid Architecture - Implementation Guide

This document describes the implementation of the OSS hybrid search architecture for Sirchmunk on Alibaba Cloud EAS.

## Overview

The OSS hybrid architecture enables efficient search over OSS-stored documents by:
- Caching hot files to local SSD based on LRU eviction
- Using DuckDB for fast metadata filtering
- Supporting both local cache and ossfs mounted paths
- Linking cache size to configured disk capacity

## Components

### 1. OSSCacheManager (`src/sirchmunk/storage/oss_cache_manager.py`)

Core caching logic with:
- `CacheConfig`: Configuration for disk size, cache ratios, TTL
- `SearchContext`: Tracks search state and downloaded files
- `OSSCacheManager`: Main caching engine

### 2. DuckDBMetadataStore (`src/sirchmunk/storage/duckdb_metadata.py`)

Metadata storage for OSS objects:
- Schema: `oss_objects` table with indexes
- Incremental sync support
- SQL-based candidate filtering

### 3. CLI Commands (`src/sirchmunk/cli/cli.py`)

New commands for OSS operations:
```
sirchmunk oss init          - Initialize cache configuration
sirchmunk oss sync          - Sync OSS metadata
sirchmunk oss search        - Search with hybrid cache
sirchmunk cache stats       - Show cache statistics
sirchmunk cache clear       - Clear cached files
sirchmunk cache evict       - Trigger LRU eviction
```

## Quick Start

### 1. Install Dependencies

```bash
pip install oss2
```

### 2. Configure Environment

Add to `~/.sirchmunk/.env`:
```bash
# OSS Configuration
OSS_BUCKET=sirchmunk-data
OSS_ENDPOINT=oss-cn-hangzhou-internal.aliyuncs.com
OSS_ACCESS_KEY_ID=your_access_key_id
OSS_ACCESS_KEY_SECRET=your_access_key_secret
```

### 3. Initialize Cache

```bash
# Initialize with default settings (200GB disk)
sirchmunk oss init

# Or customize:
sirchmunk oss init --disk-size 100 --cache-ratio 0.7
```

### 4. Sync Metadata

```bash
# Full sync
sirchmunk oss sync

# Sync specific prefix
sirchmunk oss sync --prefix docs/
```

### 5. Search

```bash
# Search OSS with hybrid cache
sirchmunk oss search "authentication flow"

# With filters
sirchmunk oss search "config" --prefix docs/ --file-types md py
```

### 6. Monitor Cache

```bash
# View statistics
sirchmunk cache stats

# Clear cache
sirchmunk cache clear

# Evict to target ratio
sirchmunk cache evict --target-ratio 0.5
```

## Configuration

### Cache Parameters

| Parameter | Default | Description | Recommended |
|-----------|---------|-------------|-------------|
| `local_disk_size_gb` | 100.0 | Local disk total size (GB) | Match EAS disk |
| `cache_ratio` | 0.8 | Cache / disk ratio | 0.7-0.9 |
| `hot_file_ratio` | 0.7 | Hot files / cache ratio | 0.6-0.8 |
| `max_single_file_mb` | 50.0 | Max single file to cache (MB) | 20-100 |
| `cache_ttl_hours` | 24.0 | Cache TTL (hours) | 12-48 |

### Recommended Configurations by Disk Size

| Disk Size | cache_ratio | hot_file_ratio | max_single_file_mb | Use Case |
|-----------|-------------|----------------|-------------------|----------|
| 50GB | 0.7 | 0.8 | 20 | Small files, testing |
| 100GB | 0.8 | 0.7 | 50 | Mixed, development |
| 200GB | 0.8 | 0.7 | 50 | Mixed, production |
| 500GB | 0.85 | 0.6 | 100 | Large files |
| 1TB+ | 0.9 | 0.5 | 200 | Large-scale deployment |

## Testing

Run the test script:

```bash
python scripts/test_oss_hybrid_search.py
```

This tests:
1. OSSCacheManager initialization
2. Metadata sync
3. Search context preparation
4. Cache statistics
5. rga integration
6. Cache eviction

## Architecture Flow

```
User Query
    ↓
OSSCacheManager.prepare_search_context()
    ↓
DuckDBMetadataStore.find_candidates()  ← Metadata filtering
    ↓
Score and sort candidates
    ↓
_download_if_beneficial()  ← Decision: download or use ossfs
    ↓
Local SSD cache (hot files) or ossfs (cold files)
    ↓
rga search on cached files
    ↓
Results + Cache stats
```

## Files Modified/Created

```
src/sirchmunk/
├── storage/
│   ├── __init__.py              # Updated exports
│   ├── oss_cache_manager.py     # NEW: OSS caching logic
│   └── duckdb_metadata.py       # NEW: Metadata store
├── config/
│   └── cache_config.yaml        # NEW: Configuration template
└── cli/
    └── cli.py                   # Updated: Added OSS/cache commands

scripts/
└── test_oss_hybrid_search.py    # NEW: Test script

requirements/
└── core.txt                     # Updated: Added oss2

docs/
└── oss-hybrid-architecture.md   # Source design document
```

## Deployment on Alibaba Cloud EAS

### 1. Create EAS Instance

- Select instance type with sufficient local SSD (e.g., `ecs.g6.xlarge` with 200GB)
- Ensure same VPC as OSS bucket (Hangzhou region)
- Configure security group for OSS access

### 2. Mount OSS (Optional)

For ossfs fallback:
```bash
# Install ossfs
apt-get install ossfs

# Mount bucket
ossfs sirchmunk-data /mnt/ossfs -o allow_other,max_stat_cache_size=100000,kernel_cache
```

### 3. Deploy Sirchmunk

```bash
# Install package
pip install sirchmunk

# Initialize
sirchmunk init
sirchmunk oss init --disk-size 200

# Sync metadata
sirchmunk oss sync
```

### 4. Monitor

```bash
# Check cache status
sirchmunk cache stats

# View logs
tail -f ~/.sirchmunk/logs/*.log
```

## Troubleshooting

### Cache命中率低 (Low Cache Hit Rate)

```bash
# Check cache statistics
sirchmunk cache stats

# If usage is too low, increase cache_ratio or disk size
sirchmunk oss init --disk-size 500 --cache-ratio 0.85
```

### rga Not Found

```bash
# Install ripgrep-all
# See: https://github.com/phiresky/ripgrep-all
```

### OSS Connection Failed

```bash
# Check network connectivity
ping oss-cn-hangzhou-internal.aliyuncs.com

# Verify credentials
echo $OSS_ACCESS_KEY_ID
echo $OSS_ACCESS_KEY_SECRET
```

### Disk Space Full

```bash
# Evict cache to lower ratio
sirchmunk cache evict --target-ratio 0.5

# Or clear completely
sirchmunk cache clear
```

---

*Implementation based on: docs/oss-hybrid-architecture.md*
*Version: v1.0*
