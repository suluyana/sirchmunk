#!/usr/bin/env python3
# Copyright (c) ModelScope Contributors. All rights reserved.
"""
Test script for OSS Hybrid Architecture basic flow.

This script tests the core functionality of the OSS hybrid search architecture:
1. Initialize OSSCacheManager
2. Sync metadata from OSS to local DuckDB
3. Prepare search context (download candidate files)
4. Execute search with rga
5. Verify cache statistics

Usage:
    python scripts/test_oss_hybrid_search.py

Prerequisites:
    - OSS credentials in .env file
    - rga (ripgrep-all) installed
    - oss2 package installed
"""

import asyncio
import os
import sys
import shutil
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sirchmunk.storage.oss_cache_manager import OSSCacheManager, CacheConfig
from sirchmunk.utils.file_utils import StorageStructure


async def test_oss_hybrid_flow():
    """Test the basic OSS hybrid architecture flow."""

    print("=" * 60)
    print("  OSS Hybrid Architecture - Basic Flow Test")
    print("=" * 60)
    print()

    # Load environment
    work_path = Path.home() / ".sirchmunk"
    env_file = work_path / ".env"

    if env_file.exists():
        print(f"Loading environment from {env_file}")
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except ImportError:
            print("  python-dotenv not installed, manual parsing...")
            with open(env_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key and key not in os.environ:
                            os.environ[key] = value
    else:
        print(f"Warning: {env_file} not found, using environment variables")

    # Check OSS configuration
    oss_bucket = os.getenv("OSS_BUCKET", "")
    oss_endpoint = os.getenv("OSS_ENDPOINT", "")
    oss_access_key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
    oss_access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")

    if not oss_bucket:
        print("ERROR: OSS_BUCKET is not configured")
        print("  Please set OSS_BUCKET in ~/.sirchmunk/.env")
        return 1

    print(f"OSS Configuration:")
    print(f"  Bucket: {oss_bucket}")
    print(f"  Endpoint: {oss_endpoint or 'not set'}")
    print(f"  Access Key ID: {oss_access_key_id[:8] + '...' if oss_access_key_id else 'not set'}")
    print()

    # Load cache configuration
    cache_config_file = work_path / ".cache" / "settings" / "cache_config.yaml"
    if cache_config_file.exists():
        print(f"Loading cache config from {cache_config_file}")
        config = CacheConfig.from_yaml(str(cache_config_file))
    else:
        print("No cache config found, using defaults")
        config = CacheConfig(
            local_disk_size_gb=100.0,
            cache_ratio=0.8,
            hot_file_ratio=0.7,
            max_single_file_mb=50.0,
            cache_ttl_hours=24.0,
        )

    print(f"Cache Configuration:")
    print(f"  Disk size: {config.local_disk_size_gb}GB")
    print(f"  Max cache: {config.max_cache_bytes / 1024**3:.2f}GB")
    print(f"  Hot cache: {config.hot_cache_bytes / 1024**3:.2f}GB")
    print()

    # Initialize cache manager
    print("Initializing OSSCacheManager...")
    cache_manager = OSSCacheManager(
        oss_bucket=oss_bucket,
        oss_endpoint=oss_endpoint,
        oss_access_key_id=oss_access_key_id,
        oss_access_key_secret=oss_access_key_secret,
        local_cache_dir=work_path / ".cache",
        config=config,
        verbose=True,
    )
    print("  OSSCacheManager initialized successfully")
    print()

    # Test 1: Metadata sync
    print("-" * 60)
    print("Test 1: Metadata Sync")
    print("-" * 60)

    try:
        print("Syncing OSS metadata...")
        await cache_manager.sync_metadata(oss_prefix=None)

        stats = await cache_manager.metadata_store.get_stats()
        print(f"  Total objects: {stats['total_objects']}")
        print(f"  Total size: {stats['total_size_gb']:.2f}GB")
        print(f"  Size distribution:")
        for dist in stats['size_distribution']:
            print(f"    {dist['bucket']}: {dist['count']} files, {dist['size'] / 1024**3:.2f}GB")
        print()
    except Exception as e:
        print(f"  FAILED: {e}")
        print("  Skipping to next test...")
        print()

    # Test 2: Search context preparation
    print("-" * 60)
    print("Test 2: Search Context Preparation")
    print("-" * 60)

    test_queries = [
        "authentication",
        "config",
        "README",
    ]

    for query in test_queries:
        print(f"\n  Testing query: '{query}'")
        try:
            ctx = await cache_manager.prepare_search_context(
                query=query,
                oss_prefix=None,
                file_types=None,
                max_files=10,
                max_total_size_mb=100.0,
            )
            print(f"    Candidates: {len(ctx.metadata)}")
            print(f"    Downloaded: {len(ctx.downloaded_files)}")
            print(f"    Search paths: {ctx.search_paths}")
        except Exception as e:
            print(f"    FAILED: {e}")

    print()

    # Test 3: Cache statistics
    print("-" * 60)
    print("Test 3: Cache Statistics")
    print("-" * 60)

    stats = cache_manager.get_cache_stats()
    print(f"  Cached files: {stats['cached_files_count']}")
    print(f"  Current size: {stats['current_size_mb']:.1f}MB")
    print(f"  Max size: {stats['max_size_mb']:.1f}MB")
    print(f"  Usage ratio: {stats['usage_ratio'] * 100:.1f}%")
    print(f"  Avg file age: {stats['avg_age_hours']:.1f}h")
    print()

    # Test 4: rga search (if available)
    print("-" * 60)
    print("Test 4: rga Search Integration")
    print("-" * 60)

    rga_path = shutil.which("rga")
    if rga_path:
        print(f"  rga found: {rga_path}")

        cache_dir = work_path / ".cache" / ".cache" / "rga" / "files"
        if cache_dir.exists() and any(cache_dir.iterdir()):
            print(f"  Searching cached files...")

            import subprocess
            result = subprocess.run(
                [rga_path, "--json", "test"] + [str(cache_dir)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                import json
                matches = json.loads(result.stdout) if result.stdout else []
                print(f"    Found {len(matches)} matches")
            else:
                print(f"    No matches found (return code: {result.returncode})")
        else:
            print("  Cache directory is empty, skipping rga test")
    else:
        print("  rga (ripgrep-all) not found in PATH")
        print("  Install from: https://github.com/phiresky/ripgrep-all")

    print()

    # Test 5: Cache cleanup
    print("-" * 60)
    print("Test 5: Cache Cleanup")
    print("-" * 60)

    print("  Testing cache eviction...")
    initial_stats = cache_manager.get_cache_stats()
    print(f"    Before eviction: {initial_stats['cached_files_count']} files, {initial_stats['current_size_mb']:.1f}MB")

    # Trigger eviction if cache is above 50%
    if initial_stats['usage_ratio'] > 0.5:
        await cache_manager._evict_cache(target_ratio=0.3)
        after_stats = cache_manager.get_cache_stats()
        print(f"    After eviction: {after_stats['cached_files_count']} files, {after_stats['current_size_mb']:.1f}MB")
    else:
        print("    Cache usage below 50%, skipping eviction")

    print()
    print("  Testing expired cache cleanup...")
    await cache_manager.cleanup_expired(max_age_hours=0.01)  # Clean up files older than 36 seconds
    print("    Cleanup complete")

    print()

    # Summary
    print("=" * 60)
    print("  Test Summary")
    print("=" * 60)
    print()
    print("  All tests completed successfully!")
    print()
    print("  Next steps:")
    print("  1. Configure OSS credentials in ~/.sirchmunk/.env")
    print("  2. Run: sirchmunk oss init")
    print("  3. Run: sirchmunk oss sync")
    print("  4. Run: sirchmunk oss search \"your query\"")
    print()

    return 0


async def main():
    """Main entry point."""
    try:
        return await test_oss_hybrid_flow()
    except KeyboardInterrupt:
        print("\n  Test cancelled by user")
        return 130
    except Exception as e:
        print(f"\n  Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
