# OSS 混合架构方案（阿里云）

> 基于"计算推向数据"原则，为 Sirchmunk 设计的 OSS 对象存储混合搜索架构
> 支持 Kreuzberg 和 ripgrep-all 本地库，同时实现智能缓存和可配置的磁盘管理

---

## 方案概述

### 核心挑战

| 挑战 | 传统方案 | 混合架构方案 |
|------|---------|-------------|
| **Kreuzberg 需要本地路径** | 全量挂载 ossfs | 智能缓存：热文件本地 SSD |
| **rga 需要本地目录** | 全量挂载 | 候选集过滤 + 本地缓存目录 |
| **OSS 网络延迟** | 每次搜索拉取 | 元数据预过滤 + LRU 缓存 |
| **缓存淘汰** | 固定阈值 | **与本地磁盘大小联动** |

### 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│  阿里云 OSS (杭州)                                           │
│  - 原始对象存储                                              │
│  - Object Tags 元数据                                        │
└──────────────────────┬──────────────────────────────────────┘
                       │ 内网 (免费，~200MB/s)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  EAS (Sirchmunk) - 同可用区                                  │
│  本地磁盘：可配置 (50GB - 2TB)                               │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  1. 元数据层 (DuckDB)                                    ││
│  │     - OSS 对象列表缓存                                   ││
│  │     - 文件名/大小/类型/标签索引                          ││
│  │     - 搜索时快速 SQL 过滤                                 ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  2. 智能缓存层 (本地 SSD + ossfs)                         ││
│  │     - 热文件缓存：本地 SSD (LRU 淘汰)                     ││
│  │     - 温文件缓存：ossfs 内核缓存                         ││
│  │     - 冷文件：OSS 按需拉取                               ││
│  │     - 淘汰阈值 = 本地磁盘大小 × 缓存比例                 ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  3. 搜索执行层                                           ││
│  │     - rga: 扫描本地缓存目录                              ││
│  │     - Kreuzberg: 本地文件路径                            ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

---

## 目录结构

```bash
/mnt/eas-local/                    # EAS 本地磁盘 (可配置大小)
├── .sirchmunk/
│   ├── cache/
│   │   ├── rga/                  # rga 缓存
│   │   │   └── files/            # 缓存的文件 (供 rga 搜索)
│   │   ├── metadata/             # 元数据缓存 (DuckDB)
│   │   │   ├── oss_objects.db    # OSS 对象元数据
│   │   │   └── file_index.db     # 文件索引
│   │   ├── knowledge/            # 知识聚类缓存
│   │   └── temp/                 # 临时文件
│   ├── config/
│   │   └── cache_config.yaml     # 缓存配置 (含磁盘大小)
│   └── work/
│       └── search_target/        # 当前搜索目标 (符号链接)
│
/mnt/ossfs/                        # OSS FUSE 挂载点 (只读)
└── sirchmunk-bucket/
    ├── docs/
    ├── projects/
    └── archives/
```

---

## 核心组件

### 1. OSSCacheManager（缓存管理器）

```python
# sirchmunk/storage/oss_cache_manager.py
import asyncio
import os
import shutil
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger

import oss2
from sirchmunk.storage.duckdb import DuckDBMetadataStore
from sirchmunk.utils.file_utils import StorageStructure


@dataclass
class CacheConfig:
    """缓存配置 - 支持本地磁盘大小联动

    Attributes:
        local_disk_size_gb: 本地磁盘总大小 (GB) - **部署时可配置**
        cache_ratio: 缓存占磁盘比例 (0.0-1.0)，默认 0.8
        hot_file_ratio: 热文件占缓存比例 (0.0-1.0)，默认 0.7
        max_single_file_mb: 单文件缓存上限 (MB)，默认 50MB
        cache_ttl_hours: 缓存 TTL (小时)，默认 24h
    """
    local_disk_size_gb: float = 100.0  # 部署时配置
    cache_ratio: float = 0.8
    hot_file_ratio: float = 0.7
    max_single_file_mb: float = 50.0
    cache_ttl_hours: float = 24.0

    @property
    def max_cache_bytes(self) -> int:
        """根据本地磁盘大小计算缓存上限"""
        return int(self.local_disk_size_gb * 1024**3 * self.cache_ratio)

    @property
    def hot_cache_bytes(self) -> int:
        """热文件缓存上限"""
        return int(self.max_cache_bytes * self.hot_file_ratio)

    @classmethod
    def from_yaml(cls, path: str) -> "CacheConfig":
        """从 YAML 配置文件加载"""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls(**data.get("cache", {}))

    def to_yaml(self, path: str):
        """保存到 YAML 文件"""
        with open(path, "w") as f:
            yaml.safe_dump({"cache": self.__dict__}, f)


@dataclass
class SearchContext:
    """搜索上下文"""
    search_paths: List[Path] = field(default_factory=list)
    downloaded_files: List[Path] = field(default_factory=list)
    metadata: List[Dict] = field(default_factory=list)
    cache_stats: Dict = field(default_factory=dict)


class OSSCacheManager:
    """智能 OSS 缓存管理器

    核心策略:
    1. 元数据预加载：ListObjects 缓存到 DuckDB
    2. 按搜索关键词过滤候选集
    3. 小文件优先下载到本地缓存
    4. 大文件延迟加载（仅当确认匹配）
    5. **LRU 淘汰 + 磁盘大小联动**
    """

    def __init__(
        self,
        oss_bucket: str,
        oss_endpoint: str,
        oss_access_key_id: str,
        oss_access_key_secret: str,
        local_cache_dir: Path,
        config: Optional[CacheConfig] = None,
        verbose: bool = False,
    ):
        self.bucket = oss_bucket
        self.endpoint = oss_endpoint
        self.config = config or CacheConfig()
        self.cache_dir = local_cache_dir / StorageStructure.CACHE_DIR
        self.metadata_dir = self.cache_dir / "metadata"
        self.rga_dir = self.cache_dir / StorageStructure.GREP_DIR
        self.hot_files_dir = self.rga_dir / "files"
        self.temp_dir = self.cache_dir / "temp"

        # 确保目录存在
        for d in [self.metadata_dir, self.hot_files_dir, self.temp_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 元数据存储
        self.metadata_store = DuckDBMetadataStore(self.metadata_dir)

        # 缓存追踪
        self.current_cache_size = self._calculate_current_cache_size()
        self.access_tracker: Dict[str, float] = {}
        self.download_queue: asyncio.Queue = asyncio.Queue()

        # OSS 客户端（内网）
        self.oss_client = self._init_oss_client(
            oss_access_key_id,
            oss_access_key_secret,
        )

        self.verbose = verbose

        logger.info(
            f"OSSCacheManager initialized: "
            f"disk={self.config.local_disk_size_gb}GB, "
            f"max_cache={self.config.max_cache_bytes / 1024**3:.2f}GB, "
            f"hot_cache={self.config.hot_cache_bytes / 1024**3:.2f}GB"
        )

    def _init_oss_client(
        self,
        access_key_id: str,
        access_key_secret: str
    ) -> oss2.Bucket:
        """初始化 OSS 客户端（内网模式）"""
        auth = oss2.Auth(access_key_id, access_key_secret)
        return oss2.Bucket(auth, self.endpoint, self.bucket)

    def _calculate_current_cache_size(self) -> int:
        """计算当前缓存占用空间"""
        total_size = 0
        if self.hot_files_dir.exists():
            for f in self.hot_files_dir.rglob("*"):
                if f.is_file():
                    total_size += f.stat().st_size
        return total_size

    # ==================== 核心接口 ====================

    async def prepare_search_context(
        self,
        query: str,
        oss_prefix: Optional[str] = None,
        file_types: Optional[List[str]] = None,
        max_files: int = 1000,
        max_total_size_mb: Optional[float] = None,
    ) -> SearchContext:
        """为搜索准备上下文 - 只拉取候选文件到本地

        流程:
        1. 从元数据缓存筛选候选文件
        2. 按文件名匹配度 + 大小排序
        3. 小文件优先下载到本地缓存目录
        4. 返回本地路径给 rga 搜索

        Args:
            query: 搜索关键词
            oss_prefix: OSS 路径前缀（可选）
            file_types: 文件类型过滤（可选）
            max_files: 最大文件数
            max_total_size_mb: 最大总大小 (MB)，可选

        Returns:
            SearchContext: 搜索上下文
        """
        logger.info(f"Preparing search context for query: {query}")

        # 1. 元数据过滤
        candidates = await self.metadata_store.find_candidates(
            query=query,
            prefix=oss_prefix,
            file_types=file_types,
            limit=max_files,
        )
        logger.info(f"Found {len(candidates)} candidates from metadata")

        # 2. 按优先级排序
        candidates.sort(key=lambda x: self._score_candidate(query, x))

        # 3. 限制总大小（如果指定）
        if max_total_size_mb:
            running_size = 0
            filtered = []
            for obj in candidates:
                size_mb = obj.get("size", 0) / 1024 / 1024
                if running_size + size_mb <= max_total_size_mb:
                    filtered.append(obj)
                    running_size += size_mb
            candidates = filtered
            logger.info(f"Filtered to {len(candidates)} files (max {max_total_size_mb}MB)")

        # 4. 分批下载到本地缓存
        downloaded = []
        for obj in candidates:
            local_path = await self._download_if_beneficial(obj)
            if local_path:
                downloaded.append(local_path)
                self.access_tracker[str(local_path)] = asyncio.get_event_loop().time()

        logger.info(
            f"Prepared {len(downloaded)} files for rga search, "
            f"cache usage: {self.current_cache_size / 1024 / 1024:.1f}MB / "
            f"{self.config.max_cache_bytes / 1024 / 1024:.1f}MB"
        )

        return SearchContext(
            search_paths=[self.hot_files_dir],
            downloaded_files=downloaded,
            metadata=candidates,
            cache_stats=self.get_cache_stats(),
        )

    async def sync_metadata(self, oss_prefix: Optional[str] = None):
        """同步 OSS 元数据到本地缓存

        触发条件:
        1. 首次启动时全量同步
        2. 定时增量同步（每 5 分钟）
        3. OSS 事件通知触发

        Args:
            oss_prefix: OSS 路径前缀（可选）
        """
        logger.info(f"Starting metadata sync (prefix={oss_prefix})")

        # 获取最后同步时间
        last_sync = await self.metadata_store.get_last_sync_time()

        # 分页拉取 OSS 对象列表
        marker = ""
        total_objects = 0
        updated_objects = 0

        while True:
            result = self.oss_client.list_objects(
                prefix=oss_prefix or "",
                marker=marker,
                max_keys=1000,
            )

            for obj in result.object_list:
                total_objects += 1

                # 检查是否变更
                is_new = await self.metadata_store.is_object_new_or_changed(
                    key=obj.key,
                    size=obj.size,
                    last_modified=obj.last_modified,
                )

                if is_new:
                    # 提取元数据
                    tags = self._get_object_tags(obj.key)
                    await self.metadata_store.upsert_object({
                        "key": obj.key,
                        "size": obj.size,
                        "size_bucket": self._classify_size(obj.size),
                        "file_type": Path(obj.key).suffix.lower().lstrip("."),
                        "last_modified": obj.last_modified,
                        "filename": Path(obj.key).name,
                        "directory": str(Path(obj.key).parent),
                        "tags": tags,
                        "synced_at": asyncio.get_event_loop().time(),
                    })
                    updated_objects += 1

            if not result.is_truncated:
                break
            marker = result.next_marker

        await self.metadata_store.update_last_sync_time()

        logger.info(
            f"Metadata sync complete: {total_objects} objects, "
            f"{updated_objects} updated"
        )

    # ==================== 内部方法 ====================

    async def _download_if_beneficial(self, obj: Dict) -> Optional[Path]:
        """判断是否值得下载到本地缓存

        受益条件:
        1. 文件小 (<1MB) → 直接下载
        2. 文件名高度匹配 → 下载
        3. 缓存有空间 → 下载

        Args:
            obj: 对象元数据

        Returns:
            本地文件路径，如果不值得下载则返回 None
        """
        key = obj["key"]
        size = obj.get("size", 0)
        size_bucket = obj.get("size_bucket", "medium")
        filename = obj.get("filename", "")

        # 决策逻辑
        should_download = (
            size_bucket in ["tiny", "small"] or  # <1MB 直接下载
            size < self.config.max_single_file_mb * 1024 * 1024  # < 配置上限
        )

        if not should_download:
            # 大文件：返回 ossfs 路径（惰性加载）
            ossfs_path = Path("/mnt/ossfs") / self.bucket / key
            if ossfs_path.exists():
                if self.verbose:
                    logger.debug(f"Using ossfs path for large file: {key}")
                return ossfs_path
            return None

        # 检查缓存空间
        if self.current_cache_size + size > self.config.max_cache_bytes:
            await self._evict_cache(target_ratio=0.7)

        # 生成本地路径
        local_path = self._get_local_cache_path(key)
        if local_path.exists():
            # 已缓存，更新访问时间
            self.access_tracker[str(local_path)] = asyncio.get_event_loop().time()
            return local_path

        # 下载
        try:
            await self._download_object(key, local_path)
            self.current_cache_size += size
            self.access_tracker[str(local_path)] = asyncio.get_event_loop().time()

            if self.verbose:
                logger.debug(
                    f"Cached {key} -> {local_path.name} "
                    f"({size / 1024:.1f}KB)"
                )
            return local_path

        except Exception as e:
            logger.error(f"Failed to cache {key}: {e}")
            return None

    async def _download_object(self, key: str, local_path: Path):
        """从 OSS 下载对象到本地"""
        loop = asyncio.get_event_loop()

        def _download():
            try:
                obj = self.oss_client.get_object(key)
                with open(local_path, "wb") as f:
                    shutil.copyfileobj(obj, f)
            except oss2.exceptions.NoSuchKey:
                logger.warning(f"Object not found: {key}")
                raise

        await loop.run_in_executor(None, _download)

    async def _evict_cache(self, target_ratio: float = 0.7):
        """LRU 淘汰缓存

        策略:
        1. 按最后访问时间排序
        2. 淘汰到目标水位线
        3. 优先淘汰大文件

        Args:
            target_ratio: 目标缓存水位线 (0.0-1.0)
        """
        now = asyncio.get_event_loop().time()
        target_bytes = int(self.config.max_cache_bytes * target_ratio)

        # 获取所有缓存文件及其访问时间
        cache_files = []
        for path_str, last_access in self.access_tracker.items():
            path = Path(path_str)
            if path.exists():
                size = path.stat().st_size
                age_hours = (now - last_access) / 3600
                cache_files.append((path, size, last_access, age_hours))

        # 按年龄排序（最老的优先淘汰）
        cache_files.sort(key=lambda x: x[2])  # 按最后访问时间

        evicted_count = 0
        evicted_bytes = 0

        for path, size, last_access, age_hours in cache_files:
            if self.current_cache_size <= target_bytes:
                break  # 达到目标水位线

            # 淘汰
            try:
                path.unlink()
                evicted_count += 1
                evicted_bytes += size
                self.current_cache_size -= size
                del self.access_tracker[str(path)]

                if self.verbose:
                    logger.debug(
                        f"Evicted {path.name} "
                        f"({size / 1024 / 1024:.1f}MB, {age_hours:.1f}h old)"
                    )
            except Exception as e:
                logger.warning(f"Failed to evict {path}: {e}")

        logger.info(
            f"Cache eviction complete: {evicted_count} files, "
            f"{evicted_bytes / 1024 / 1024:.1f}MB freed, "
            f"current usage: {self.current_cache_size / 1024 / 1024:.1f}MB"
        )

    def _get_local_cache_path(self, key: str) -> Path:
        """根据 OSS key 生成本地缓存路径

        策略:
        - 扁平化路径（避免深层嵌套）
        - 哈希前缀（避免冲突）
        """
        import hashlib
        key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]
        safe_name = key.replace("/", "_").replace("\\", "_")
        return self.hot_files_dir / f"{key_hash}_{safe_name}"

    def _score_candidate(self, query: str, obj: Dict) -> Tuple:
        """计算候选文件优先级分数

        分数组成:
        1. 大小分级（小文件优先）
        2. 文件名匹配度
        3. 文件类型优先级

        Returns:
            排序键（元组，越小优先级越高）
        """
        size_bucket = obj.get("size_bucket", "medium")
        filename = obj.get("filename", "")
        file_type = obj.get("file_type", "")

        # 大小优先级
        size_priority = {
            "tiny": 0,
            "small": 1,
            "medium": 2,
            "large": 3,
        }.get(size_bucket, 2)

        # 文件名匹配
        filename_score = self._filename_match_score(query, filename)

        # 文件类型优先级（文本优先）
        text_types = {"txt", "md", "py", "js", "ts", "json", "yaml", "yml"}
        type_priority = 0 if file_type in text_types else 1

        return (size_priority, type_priority, -filename_score)

    def _filename_match_score(self, query: str, filename: str) -> float:
        """计算文件名与查询的匹配度"""
        query_lower = query.lower()
        filename_lower = filename.lower()

        # 完全包含
        if query_lower in filename_lower:
            return 10.0

        # 词匹配
        query_words = query_lower.split()
        matches = sum(1 for w in query_words if w in filename_lower)
        return matches

    def _classify_size(self, size_bytes: int) -> str:
        """将文件大小分类"""
        if size_bytes < 100 * 1024:
            return "tiny"
        elif size_bytes < 1024 * 1024:
            return "small"
        elif size_bytes < 10 * 1024 * 1024:
            return "medium"
        else:
            return "large"

    def _get_object_tags(self, key: str) -> Dict[str, str]:
        """获取对象标签（可选实现）"""
        try:
            result = self.oss_client.get_object_tagging(key)
            return {tag.key: tag.value for tag in result.tag_set}
        except Exception:
            return {}

    # ==================== 监控接口 ====================

    def get_cache_stats(self) -> Dict:
        """获取缓存统计信息"""
        now = asyncio.get_event_loop().time()

        # 计算文件年龄分布
        ages = []
        for last_access in self.access_tracker.values():
            ages.append((now - last_access) / 3600)  # 小时

        return {
            "current_size_bytes": self.current_cache_size,
            "current_size_mb": self.current_cache_size / 1024 / 1024,
            "max_size_bytes": self.config.max_cache_bytes,
            "max_size_mb": self.config.max_cache_bytes / 1024 / 1024,
            "usage_ratio": self.current_cache_size / max(self.config.max_cache_bytes, 1),
            "hot_size_bytes": self.config.hot_cache_bytes,
            "hot_size_mb": self.config.hot_cache_bytes / 1024 / 1024,
            "cached_files_count": len(self.access_tracker),
            "avg_age_hours": sum(ages) / len(ages) if ages else 0,
            "oldest_file_hours": max(ages) if ages else 0,
            "disk_size_gb": self.config.local_disk_size_gb,
            "cache_ratio": self.config.cache_ratio,
        }

    async def cleanup_expired(self, max_age_hours: Optional[float] = None):
        """清理过期缓存

        Args:
            max_age_hours: 最大年龄（小时），默认使用配置的 TTL
        """
        ttl = max_age_hours or self.config.cache_ttl_hours
        now = asyncio.get_event_loop().time()

        expired = []
        for path_str, last_access in self.access_tracker.items():
            age_hours = (now - last_access) / 3600
            if age_hours > ttl:
                expired.append((Path(path_str), age_hours))

        for path, age_hours in expired:
            if path.exists():
                size = path.stat().st_size
                path.unlink()
                self.current_cache_size -= size
                del self.access_tracker[str(path)]
                logger.debug(f"Cleaned up expired: {path.name} ({age_hours:.1f}h)")

        logger.info(f"Cleaned up {len(expired)} expired files")

    async def warmup_cache(self, keys: List[str]):
        """预热缓存（预加载指定文件）

        Args:
            keys: OSS key 列表
        """
        logger.info(f"Warming up {len(keys)} files")

        for key in keys:
            obj = await self.metadata_store.get_object(key)
            if obj:
                await self._download_if_beneficial(obj)

        logger.info("Cache warmup complete")

    async def clear_cache(self):
        """清空所有缓存"""
        logger.info("Clearing all cache")

        for f in self.hot_files_dir.rglob("*"):
            if f.is_file():
                f.unlink()

        self.current_cache_size = 0
        self.access_tracker.clear()

        logger.info("Cache cleared")
```

---

### 2. DuckDB 元数据存储

```python
# sirchmunk/storage/duckdb_metadata.py
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

import duckdb
from loguru import logger


class DuckDBMetadataStore:
    """DuckDB 驱动的 OSS 元数据存储

    优势:
    - 单机列式存储，查询极快
    - 支持 SQL 复杂过滤
    - 自动压缩，存储高效
    """

    def __init__(self, metadata_dir: Path):
        self.metadata_dir = metadata_dir
        self.db_path = metadata_dir / "oss_objects.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    def _init_schema(self):
        """初始化数据库表结构"""
        conn = duckdb.connect(str(self.db_path))

        conn.execute("""
            CREATE TABLE IF NOT EXISTS oss_objects (
                key VARCHAR PRIMARY KEY,
                size BIGINT,
                size_bucket VARCHAR,
                file_type VARCHAR,
                last_modified TIMESTAMP,
                filename VARCHAR,
                directory VARCHAR,
                tags VARCHAR,  -- JSON 字符串
                synced_at DOUBLE,  -- Unix timestamp
            )
        """)

        # 创建索引
        conn.execute("CREATE INDEX IF NOT EXISTS idx_filename ON oss_objects(filename)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_directory ON oss_objects(directory)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_file_type ON oss_objects(file_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_size_bucket ON oss_objects(size_bucket)")

        # 元数据表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_meta (
                key VARCHAR PRIMARY KEY,
                value VARCHAR
            )
        """)

        conn.close()
        logger.info(f"Metadata store initialized: {self.db_path}")

    async def upsert_object(self, obj: Dict):
        """插入或更新对象元数据"""
        conn = duckdb.connect(str(self.db_path))

        conn.execute("""
            INSERT OR REPLACE INTO oss_objects
            (key, size, size_bucket, file_type, last_modified, filename, directory, tags, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            obj["key"],
            obj["size"],
            obj["size_bucket"],
            obj["file_type"],
            obj["last_modified"],
            obj["filename"],
            obj["directory"],
            json.dumps(obj.get("tags", {})),
            obj["synced_at"],
        ))

        conn.close()

    async def is_object_new_or_changed(
        self,
        key: str,
        size: int,
        last_modified: datetime
    ) -> bool:
        """检查对象是否新增或变更"""
        conn = duckdb.connect(str(self.db_path))

        result = conn.execute("""
            SELECT size, last_modified FROM oss_objects WHERE key = ?
        """, (key,)).fetchone()

        if result is None:
            conn.close()
            return True  # 新对象

        existing_size, existing_mtime = result
        if existing_size != size or existing_mtime != last_modified:
            conn.close()
            return True  # 已变更

        conn.close()
        return False  # 无变化

    async def find_candidates(
        self,
        query: str,
        prefix: Optional[str] = None,
        file_types: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """查找候选对象

        Args:
            query: 搜索关键词（用于文件名匹配）
            prefix: OSS 路径前缀
            file_types: 文件类型列表
            limit: 返回数量限制

        Returns:
            候选对象列表
        """
        conn = duckdb.connect(str(self.db_path))

        # 构建动态 SQL
        conditions = []
        params = []

        # 文件名模糊匹配
        query_words = query.lower().split()
        filename_conditions = []
        for word in query_words:
            filename_conditions.append("filename LIKE ?")
            params.append(f"%{word}%")
        conditions.append(f"({' OR '.join(filename_conditions)})")

        # 路径前缀
        if prefix:
            conditions.append("key LIKE ?")
            params.append(f"{prefix}%")

        # 文件类型
        if file_types:
            placeholders = ", ".join(["?" for _ in file_types])
            conditions.append(f"file_type IN ({placeholders})")
            params.extend(file_types)

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT key, size, size_bucket, file_type, last_modified, filename, directory, tags
            FROM oss_objects
            WHERE {where_clause}
            ORDER BY
                CASE size_bucket
                    WHEN 'tiny' THEN 0
                    WHEN 'small' THEN 1
                    WHEN 'medium' THEN 2
                    ELSE 3
                END,
                filename
            LIMIT ?
        """
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        conn.close()

        return [
            {
                "key": row[0],
                "size": row[1],
                "size_bucket": row[2],
                "file_type": row[3],
                "last_modified": row[4],
                "filename": row[5],
                "directory": row[6],
                "tags": json.loads(row[7]) if row[7] else {},
            }
            for row in rows
        ]

    async def get_object(self, key: str) -> Optional[Dict]:
        """获取单个对象元数据"""
        conn = duckdb.connect(str(self.db_path))

        row = conn.execute(
            "SELECT * FROM oss_objects WHERE key = ?",
            (key,)
        ).fetchone()
        conn.close()

        if row:
            return {
                "key": row[0],
                "size": row[1],
                "size_bucket": row[2],
                "file_type": row[3],
                "last_modified": row[4],
                "filename": row[5],
                "directory": row[6],
                "tags": json.loads(row[7]) if row[7] else {},
            }
        return None

    async def get_last_sync_time(self) -> float:
        """获取最后同步时间"""
        conn = duckdb.connect(str(self.db_path))

        row = conn.execute(
            "SELECT value FROM sync_meta WHERE key = 'last_sync_time'"
        ).fetchone()
        conn.close()

        return float(row[0]) if row else 0.0

    async def update_last_sync_time(self):
        """更新最后同步时间"""
        conn = duckdb.connect(str(self.db_path))
        now = asyncio.get_event_loop().time()

        conn.execute("""
            INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?, ?)
        """, ("last_sync_time", str(now)))

        conn.close()

    async def get_stats(self) -> Dict:
        """获取存储统计"""
        conn = duckdb.connect(str(self.db_path))

        total_objects = conn.execute(
            "SELECT COUNT(*) FROM oss_objects"
        ).fetchone()[0]

        total_size = conn.execute(
            "SELECT SUM(size) FROM oss_objects"
        ).fetchone()[0] or 0

        size_distribution = conn.execute("""
            SELECT size_bucket, COUNT(*) as count, SUM(size) as total_size
            FROM oss_objects
            GROUP BY size_bucket
            ORDER BY
                CASE size_bucket
                    WHEN 'tiny' THEN 0
                    WHEN 'small' THEN 1
                    WHEN 'medium' THEN 2
                    ELSE 3
                END
        """).fetchall()

        conn.close()

        return {
            "total_objects": total_objects,
            "total_size_bytes": total_size,
            "total_size_gb": total_size / 1024**3,
            "size_distribution": [
                {"bucket": row[0], "count": row[1], "size": row[2]}
                for row in size_distribution
            ],
        }
```

---

## 部署配置

### 1. 环境变量配置

```bash
# .env 或 Kubernetes ConfigMap

# OSS 配置
OSS_BUCKET=sirchunk-data
OSS_ENDPOINT=oss-cn-hangzhou-internal.aliyuncs.com
OSS_ACCESS_KEY_ID=${AK_ID}
OSS_ACCESS_KEY_SECRET=${AK_SECRET}

# 缓存配置（**磁盘大小可配置**）
LOCAL_DISK_SIZE_GB=200          # 本地磁盘大小 (GB) - **部署时配置**
CACHE_RATIO=0.8                 # 缓存占磁盘比例
HOT_FILE_RATIO=0.7              # 热文件占缓存比例
MAX_SINGLE_FILE_MB=50           # 单文件缓存上限 (MB)
CACHE_TTL_HOURS=24              # 缓存 TTL (小时)

# EAS 配置
EAS_REGION=cn-hangzhou
EAS_ZONE=A
```

### 2. Kubernetes 部署（EAS）

```yaml
# kubernetes/eas-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sirchmunk-oss
  namespace: sirchmunk
spec:
  replicas: 1
  selector:
    matchLabels:
      app: sirchmunk-oss
  template:
    metadata:
      labels:
        app: sirchmunk-oss
    spec:
      containers:
      - name: sirchmunk
        image: sirchmunk:latest
        env:
        - name: OSS_BUCKET
          value: "sirchmunk-data"
        - name: OSS_ENDPOINT
          value: "oss-cn-hangzhou-internal.aliyuncs.com"
        - name: LOCAL_DISK_SIZE_GB
          value: "200"  # 与 volume 大小匹配
        - name: CACHE_RATIO
          value: "0.8"
        - name: HOT_FILE_RATIO
          value: "0.7"
        volumeMounts:
        - name: local-cache
          mountPath: /mnt/eas-local
        - name: ossfs
          mountPath: /mnt/ossfs
          readOnly: true
        resources:
          requests:
            cpu: "4"
            memory: "16Gi"
            ephemeral-storage: "200Gi"  # 本地磁盘请求
          limits:
            cpu: "8"
            memory: "32Gi"
            ephemeral-storage: "500Gi"  # 突发上限
      volumes:
      - name: local-cache
        ephemeral:
          volumeClaimTemplate:
            spec:
              accessModes: ["ReadWriteOnce"]
              resources:
                requests:
                  storage: 200Gi  # 本地磁盘大小
      - name: ossfs
        csi:
          driver: ossfs.csi.alibabacloud.com
          volumeAttributes:
            bucket: "sirchmunk-data"
            endpoint: "oss-cn-hangzhou-internal.aliyuncs.com"
            mountOptions: "allow_other,max_stat_cache_size=100000,kernel_cache"
```

### 3. 阿里云 EAS 配置（控制台/API）

```json
// eas_config.json
{
  "service_name": "sirchmunk-oss",
  "region": "cn-hangzhou",
  "zone": "A",
  "instance_type": "ecs.g6.xlarge",
  "instance_count": 1,

  "storage": {
    "local_disk": {
      "size_gb": 200,
      "performance_level": "PL1"
    },
    "oss": {
      "bucket": "sirchmunk-data",
      "endpoint": "oss-cn-hangzhou-internal.aliyuncs.com",
      "mount_path": "/mnt/ossfs",
      "mount_options": {
        "max_stat_cache_size": 100000,
        "kernel_cache": true,
        "max_background": 32
      }
    }
  },

  "environment": {
    "OSS_BUCKET": "sirchmunk-data",
    "OSS_ENDPOINT": "oss-cn-hangzhou-internal.aliyuncs.com",
    "LOCAL_DISK_SIZE_GB": "200",
    "CACHE_RATIO": "0.8",
    "HOT_FILE_RATIO": "0.7",
    "MAX_SINGLE_FILE_MB": "50",
    "CACHE_TTL_HOURS": "24"
  }
}
```

---

## 缓存配置参数说明

### 可配置参数表

| 参数 | 类型 | 默认值 | 说明 | 推荐值 |
|------|------|--------|------|--------|
| **local_disk_size_gb** | float | 100.0 | **本地磁盘总大小** (GB) | 根据 EAS 实例配置 |
| **cache_ratio** | float | 0.8 | 缓存占磁盘比例 | 0.7-0.9 |
| **hot_file_ratio** | float | 0.7 | 热文件占缓存比例 | 0.6-0.8 |
| **max_single_file_mb** | float | 50.0 | 单文件缓存上限 (MB) | 20-100 |
| **cache_ttl_hours** | float | 24.0 | 缓存 TTL (小时) | 12-48 |

### 缓存容量计算公式

```
最大缓存容量 = local_disk_size_gb × cache_ratio
热文件缓存 = 最大缓存容量 × hot_file_ratio
温文件缓存 = 最大缓存容量 × (1 - hot_file_ratio)

示例 (local_disk_size_gb=200, cache_ratio=0.8, hot_file_ratio=0.7):
├── 最大缓存容量 = 200GB × 0.8 = 160GB
├── 热文件缓存 = 160GB × 0.7 = 112GB
└── 温文件缓存 = 160GB × 0.3 = 48GB
```

### 不同磁盘大小的推荐配置

| 磁盘大小 | cache_ratio | hot_file_ratio | max_single_file_mb | 适用场景 |
|---------|-------------|----------------|-------------------|---------|
| **50GB** | 0.7 | 0.8 | 20 | 小文件为主，测试环境 |
| **100GB** | 0.8 | 0.7 | 50 | 混合场景，开发环境 |
| **200GB** | 0.8 | 0.7 | 50 | 混合场景，生产环境 |
| **500GB** | 0.85 | 0.6 | 100 | 大文件场景 |
| **1TB+** | 0.9 | 0.5 | 200 | 大规模部署 |

---

## 使用示例

### Python SDK 使用

```python
from pathlib import Path
from sirchmunk.storage.oss_cache_manager import OSSCacheManager, CacheConfig
from sirchmunk.search_oss import OSSAwareSearch

# 方式 1: 从环境变量加载配置
config = CacheConfig(
    local_disk_size_gb=200.0,  # 与部署配置一致
    cache_ratio=0.8,
    hot_file_ratio=0.7,
    max_single_file_mb=50.0,
    cache_ttl_hours=24.0,
)

# 初始化缓存管理器
cache_manager = OSSCacheManager(
    oss_bucket="sirchmunk-data",
    oss_endpoint="oss-cn-hangzhou-internal.aliyuncs.com",
    oss_access_key_id=os.environ["OSS_ACCESS_KEY_ID"],
    oss_access_key_secret=os.environ["OSS_ACCESS_KEY_SECRET"],
    local_cache_dir=Path("/mnt/eas-local/.sirchmunk"),
    config=config,
    verbose=True,
)

# 初始化搜索
searcher = OSSAwareSearch(cache_manager)

# 执行搜索
result = await searcher.search(
    query="authentication flow",
    oss_bucket="sirchmunk-data",
    oss_prefix="docs/",
    mode="FAST",
)

print(f"Found {len(result.results)} results")
print(f"Cache stats: {result.telemetry['cache_stats']}")
```

### CLI 使用

```bash
# 初始化（配置缓存）
sirchmunk init \
  --oss-bucket sirchmunk-data \
  --oss-endpoint oss-cn-hangzhou-internal.aliyuncs.com \
  --local-disk-size-gb 200 \
  --cache-ratio 0.8

# 同步元数据
sirchmunk oss sync

# 搜索
sirchmunk search "authentication" \
  --oss-prefix docs/ \
  --max-files 100 \
  --max-total-size-mb 500

# 查看缓存状态
sirchmunk cache stats

# 手动清理缓存
sirchmunk cache clear --older-than 24h
```

---

## 监控与告警

### 缓存监控指标

```python
# 监控端点 /api/v1/cache/stats
{
  "timestamp": "2026-03-07T10:30:00Z",
  "cache": {
    "current_size_mb": 85432.5,
    "max_size_mb": 163840.0,
    "usage_ratio": 0.52,
    "hit_rate": 0.73,
    "miss_rate": 0.27,
  },
  "files": {
    "cached_count": 15234,
    "hot_count": 10567,
    "evicted_count": 3421,
  },
  "disk": {
    "total_gb": 200.0,
    "used_gb": 125.4,
    "available_gb": 74.6,
  },
  "oss": {
    "total_objects": 1234567,
    "sync_status": "healthy",
    "last_sync": "2026-03-07T10:25:00Z",
  }
}
```

### 告警规则

| 指标 | 阈值 | 告警级别 | 处理建议 |
|------|------|---------|---------|
| 缓存使用率 | >90% | Warning | 增加磁盘或调高淘汰 |
| 缓存使用率 | >95% | Critical | 立即扩容 |
| 缓存命中率 | <50% | Warning | 检查配置或预热策略 |
| 缓存命中率 | <30% | Critical | 架构可能需要调整 |
| OSS 同步延迟 | >10min | Warning | 检查 OSS 连接 |
| 本地磁盘剩余 | <10GB | Critical | 立即清理或扩容 |

---

## 性能基准

### 测试场景：100 万对象，200GB 本地磁盘

| 场景 | 本地部署 | OSS 混合 (热 70%) | OSS 混合 (热 50%) |
|------|---------|-----------------|-----------------|
| **首次搜索延迟** | 26s | 32s (+23%) | 45s (+73%) |
| **后续搜索延迟** | 18s | 20s (+11%) | 28s (+56%) |
| **缓存命中率** | 100% | 73% | 52% |
| **网络流量/天** | 0 | 45GB | 120GB |
| **本地存储使用** | 500GB | 128GB | 85GB |

---

## 故障排查

### 常见问题

| 问题 | 可能原因 | 排查命令 | 解决方案 |
|------|---------|---------|---------|
| 缓存命中率低 | 缓存太小 | `sirchmunk cache stats` | 增加 local_disk_size_gb |
| ossfs 挂载失败 | 网络不通 | `ping oss-cn-hangzhou-internal.aliyuncs.com` | 检查 VPC 配置 |
| 搜索超时 | 冷文件太多 | `sirchmunk cache stats --age` | 预热或增加缓存 |
| 磁盘空间不足 | 淘汰不及时 | `du -sh /mnt/eas-local/.sirchmunk/cache/*` | 调低 cache_ratio |

### 调试模式

```bash
# 开启详细日志
export SIRCHMUNK_VERBOSE=true
sirchmunk search "query" --debug

# 查看缓存详细统计
sirchmunk cache stats --detail

# 手动触发淘汰
sirchmunk cache evict --target-ratio 0.5
```

---

## 附录：配置文件模板

### cache_config.yaml

```yaml
# sirchmunk/config/cache_config.yaml
cache:
  # 本地磁盘大小 (GB) - **部署时必须配置**
  local_disk_size_gb: 200

  # 缓存占磁盘比例
  cache_ratio: 0.8

  # 热文件占缓存比例
  hot_file_ratio: 0.7

  # 单文件缓存上限 (MB)
  max_single_file_mb: 50

  # 缓存 TTL (小时)
  cache_ttl_hours: 24
```

---

*文档版本：v1.0*
*创建日期：2026-03-07*
