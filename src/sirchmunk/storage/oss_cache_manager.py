# Copyright (c) ModelScope Contributors. All rights reserved.
"""
OSS Cache Manager for the hybrid architecture.

Implements intelligent caching strategies for OSS objects:
- Metadata pre-filtering via DuckDB
- Small-file-first download strategy
- LRU eviction with disk size linkage
- Support for both local SSD cache and ossfs mounted paths
"""

import asyncio
import os
import shutil
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger

import oss2
from sirchmunk.storage.duckdb_metadata import DuckDBMetadataStore
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
