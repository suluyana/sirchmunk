# Copyright (c) ModelScope Contributors. All rights reserved.
"""
DuckDB-driven OSS object metadata store for the OSS hybrid architecture.

Provides fast SQL-based filtering and indexing for OSS objects, enabling
efficient candidate selection before downloading files to local cache.

Features:
- Columnar storage for fast analytical queries
- Automatic indexing on filename, directory, file_type, size_bucket
- Incremental sync support via last_modified tracking
"""

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
