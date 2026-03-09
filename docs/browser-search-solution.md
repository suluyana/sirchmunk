# 浏览器端全文检索方案（FlexSearch 版本）

> 替代 ripgrep-all 的纯前端搜索方案，实现"计算推向数据"的架构演进

---

## 方案概述

### 背景

Sirchmunk 部署在服务器时面临两大瓶颈：
1. **用户本地文件**：服务器无法直接访问，全量上传网络开销巨大
2. **OSS 云端文件**：从对象存储拉取大量文件到计算节点成本高昂

**核心思路**：将搜索计算推向数据所在地（浏览器），服务器仅做协调和知识聚合

### 架构对比

| 维度 | 传统架构 | 浏览器端架构 |
|------|---------|-------------|
| 搜索执行 | 服务器 ripgrep-all | 浏览器 FlexSearch |
| 文件传输 | 全量上传 | 仅命中片段（KB 级） |
| 隐私 | 文件离开本地 | 文件不出浏览器沙盒 |
| 延迟 | 上传 + 搜索 | 本地即时响应 |

---

## 技术选型

### 核心组件

| 组件 | 作用 | 选型 |
|------|------|------|
| **文件访问** | 枚举和读取用户文件 | File System Access API |
| **全文索引** | 构建倒排索引 + 搜索 | FlexSearch |
| **PDF 解析** | 提取 PDF 文本内容 | PDF.js |
| **DOCX 解析** | 提取 Word 文本内容 | mammoth.js |
| **XLSX 解析** | 提取 Excel 文本内容 | SheetJS (xlsx) |
| **索引缓存** | 持久化索引数据 | IndexedDB |
| **并发执行** | 避免阻塞 UI | Web Workers |

### 为什么选择 FlexSearch

| 特性 | FlexSearch | wasm-ripgrep | 原生 JS |
|------|-----------|-------------|---------|
| 索引速度 | ⭐⭐⭐⭐⭐ | ⭐⭐ | N/A |
| 搜索速度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐ |
| 中文支持 | ✅ (需配置) | ❌ | ⚠️ |
| 增量索引 | ✅ | ❌ | N/A |
| 持久化 | ✅ | ❌ | N/A |
| 包体积 | ~50KB | ~2MB | 0 |
| 内存占用 | 中 | 高 | 低 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  Sirchmunk Web (Browser)                                    │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  1. File Picker Layer                                    ││
│  │     - showDirectoryPicker()                             ││
│  │     - Recursive file enumeration                        ││
│  │     - SHA-256 hash deduplication                        ││
│  └─────────────────────────────────────────────────────────┘│
│                          │                                  │
│  ┌───────────────────────▼─────────────────────────────────┐│
│  │  2. Format Detection Layer                               ││
│  │     - Text files (.txt/.md/.py/.js): direct read        ││
│  │     - PDF (.pdf): PDF.js                                ││
│  │     - DOCX (.docx): mammoth.js                          ││
│  │     - XLSX (.xlsx/.xls): SheetJS                        ││
│  │     - PPTX (.pptx): 暂不支持 → 上传服务端               ││
│  └─────────────────────────────────────────────────────────┘│
│                          │                                  │
│  ┌───────────────────────▼─────────────────────────────────┐│
│  │  3. Index Layer (FlexSearch + Web Worker)                ││
│  │     - Per-file incremental index                        ││
│  │     - Global merged index                               ││
│  │     - IndexedDB persistence (auto-save/load)            ││
│  │     - Chunked processing (avoid memory overflow)        ││
│  └─────────────────────────────────────────────────────────┘│
│                          │                                  │
│  ┌───────────────────────▼─────────────────────────────────┐│
│  │  4. Search Layer                                         ││
│  │     - Multi-keyword search (AND/OR/NOT)                 ││
│  │     - Relevance ranking (TF-IDF-like)                   ││
│  │     - Snippet extraction with highlight                 ││
│  │     - Context window expansion                          ││
│  └─────────────────────────────────────────────────────────┘│
│                          │                                  │
│  ┌───────────────────────▼─────────────────────────────────┐│
│  │  5. Cloud Sync Layer (Optional)                          ││
│  │     - Upload snippets only (~KB per file)               ││
│  │     - LLM knowledge aggregation                         ││
│  │     - Cross-session cluster reuse                       ││
│  │     - Zero raw-file exposure                            ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP/WebSocket (KB级数据)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Sirchmunk Cloud (Coordinator)                              │
│  - LLM keyword extraction                                   │
│  - Knowledge cluster aggregation                            │
│  - Cross-user knowledge reuse                               │
│  - Never receives raw file content                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 实现细节

### 3.1 文件枚举与哈希去重

```javascript
// utils/file-enumerator.js
import { showDirectoryPicker } from 'file-system-access-api';

export async function enumerateDirectory() {
  const dirHandle = await showDirectoryPicker({
    id: 'sirchmunk-search',
    mode: 'read',
  });

  const files = [];
  const seenHashes = new Set();

  for await (const entry of dirHandle.values()) {
    await processEntry(entry, files, seenHashes);
  }

  return files;
}

async function processEntry(entry, files, seenHashes, path = '') {
  const currentPath = path ? `${path}/${entry.name}` : entry.name;

  if (entry.kind === 'file') {
    const file = await entry.getFile();
    const hash = await computeHash(file);

    // Skip duplicates
    if (!seenHashes.has(hash)) {
      seenHashes.add(hash);
      files.push({
        handle: entry,
        file,
        path: currentPath,
        hash,
        size: file.size,
        type: file.type || detectMimeType(file.name),
      });
    }
  } else if (entry.kind === 'directory') {
    // Skip common non-essential directories
    if (!shouldSkipDirectory(entry.name)) {
      for await (const child of entry.values()) {
        await processEntry(child, files, seenHashes, currentPath);
      }
    }
  }
}

async function computeHash(file) {
  const buffer = await file.slice(0, 65536).arrayBuffer(); // First 64KB
  const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(hashBuffer))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

function shouldSkipDirectory(name) {
  return ['node_modules', '.git', '__pycache__', 'vendor', 'dist', 'build'].includes(name);
}
```

### 3.2 格式解析层

```javascript
// utils/text-extractor.js
import * as PDFJS from 'pdfjs-dist';
import mammoth from 'mammoth';
import * as XLSX from 'xlsx';

export async function extractText(file) {
  const ext = file.name.split('.').pop().toLowerCase();

  switch (ext) {
    case 'pdf':
      return extractFromPDF(file);
    case 'docx':
      return extractFromDOCX(file);
    case 'xlsx':
    case 'xls':
      return extractFromXLSX(file);
    case 'pptx':
      // PPTX not supported in browser - mark for server upload
      throw new Error('PPTX requires server-side processing');
    default:
      // Plain text files
      return await file.text();
  }
}

async function extractFromPDF(file) {
  const arrayBuffer = await file.arrayBuffer();
  const pdf = await PDFJS.getDocument({ data: arrayBuffer }).promise;

  let text = '';
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const textContent = await page.getTextContent();
    text += textContent.items.map(item => item.str).join(' ') + '\n';
  }
  return text;
}

async function extractFromDOCX(file) {
  const arrayBuffer = await file.arrayBuffer();
  const result = await mammoth.extractRawText({ arrayBuffer });
  return result.value;
}

async function extractFromXLSX(file) {
  const arrayBuffer = await file.arrayBuffer();
  const workbook = XLSX.read(arrayBuffer);

  let text = '';
  for (const sheetName of workbook.SheetNames) {
    const sheet = workbook.Sheets[sheetName];
    text += `\n=== ${sheetName} ===\n`;
    text += XLSX.utils.sheet_to_txt(sheet);
  }
  return text;
}
```

### 3.3 索引层（Web Worker）

```javascript
// worker/index-worker.js
import { Document } from 'flexsearch';
import { openDB } from 'idb';

const DB_NAME = 'sirchmunk-index';
const DB_VERSION = 1;
const STORE_NAME = 'flexsearch-index';

let index = null;

// Initialize FlexSearch document
function initIndex() {
  return new Document({
    tokenize: 'full',  // 'forward' for Latin, 'full' for CJK
    charset: 'latin:extra',
    minlength: 2,
    document: {
      id: 'id',
      index: 'content',
      store: ['path', 'size', 'type'],
    },
    worker: false,  // We manage workers ourselves
  });
}

// Open IndexedDB
async function openDatabase() {
  return openDB(DB_NAME, DB_VERSION, {
    upgrade(db) {
      db.createObjectStore(STORE_NAME);
    },
  });
}

// Export index to IndexedDB
async function saveIndex() {
  if (!index) return;

  const db = await openDatabase();
  const exported = index.export();

  await db.put(STORE_NAME, exported, 'main-index');
}

// Import index from IndexedDB
async function loadIndex() {
  const db = await openDatabase();
  const exported = await db.get(STORE_NAME, 'main-index');

  if (exported) {
    index = initIndex();
    index.import(exported);
    return true;
  }
  return false;
}

// Main message handler
self.onmessage = async (e) => {
  const { type, payload } = e.data;

  try {
    switch (type) {
      case 'INIT':
        index = initIndex();
        const loaded = await loadIndex();
        self.postMessage({ type: 'INITIALIZED', loaded });
        break;

      case 'INDEX_FILES':
        await indexFiles(payload.files);
        break;

      case 'SEARCH':
        const results = await search(payload.query, payload.options);
        self.postMessage({ type: 'SEARCH_RESULTS', results });
        break;

      case 'SAVE':
        await saveIndex();
        self.postMessage({ type: 'SAVED' });
        break;

      case 'CLEAR':
        index = initIndex();
        const db = await openDatabase();
        await db.clear(STORE_NAME);
        self.postMessage({ type: 'CLEARED' });
        break;
    }
  } catch (error) {
    self.postMessage({ type: 'ERROR', error: error.message });
  }
};

async function indexFiles(files) {
  if (!index) index = initIndex();

  const batchSize = 100;
  const totalBatches = Math.ceil(files.length / batchSize);

  for (let i = 0; i < files.length; i += batchSize) {
    const batch = files.slice(i, i + batchSize);
    const indexed = [];

    for (const file of batch) {
      try {
        const content = await extractText(file.file);
        index.add({
          id: file.hash,
          content,
          path: file.path,
          size: file.size,
          type: file.type,
        });
        indexed.push(file.hash);
      } catch (error) {
        console.warn(`Failed to index ${file.path}:`, error);
      }
    }

    // Report progress
    self.postMessage({
      type: 'INDEX_PROGRESS',
      processed: Math.min(i + batchSize, files.length),
      total: files.length,
      indexed,
    });
  }

  // Auto-save after indexing
  await saveIndex();
  self.postMessage({ type: 'INDEX_COMPLETE' });
}

async function search(query, options = {}) {
  if (!index) return [];

  const results = index.search(query, {
    limit: options.limit || 100,
    suggest: options.suggest !== false,
  });

  return results.map(r => ({
    id: r.id,
    path: r.path,
    size: r.size,
    type: r.type,
    snippets: extractSnippets(r.content, query),
  }));
}

function extractSnippets(content, query, contextSize = 100) {
  const regex = new RegExp(`(.{0,${contextSize}}${escapeRegex(query)}.{0,${contextSize}})`, 'gi');
  const matches = [];
  let match;

  while ((match = regex.exec(content)) !== null) {
    matches.push({
      text: match[1].trim(),
      position: match.index,
    });
  }

  return matches.slice(0, 5); // Max 5 snippets per file
}

function escapeRegex(string) {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
```

### 3.4 搜索层（主线程）

```javascript
// services/search-service.js
export class SearchService {
  constructor() {
    this.worker = new Worker(new URL('../worker/index-worker.js', import.meta.url));
    this.listeners = new Map();
    this.setupMessageHandler();
  }

  setupMessageHandler() {
    this.worker.onmessage = (e) => {
      const { type, ...payload } = e.data;
      const listeners = this.listeners.get(type) || [];
      listeners.forEach(cb => cb(payload));
    };
  }

  on(eventType, callback) {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, []);
    }
    this.listeners.get(eventType).push(callback);
  }

  off(eventType, callback) {
    const listeners = this.listeners.get(eventType);
    if (listeners) {
      const idx = listeners.indexOf(callback);
      if (idx > -1) listeners.splice(idx, 1);
    }
  }

  async init() {
    this.worker.postMessage({ type: 'INIT' });
    return new Promise(resolve => {
      this.on('INITIALIZED', (payload) => {
        resolve({ loaded: payload.loaded });
      });
    });
  }

  async indexFiles(files, onProgress) {
    this.worker.postMessage({ type: 'INDEX_FILES', payload: { files } });

    return new Promise(resolve => {
      const progressHandler = (payload) => {
        onProgress?.(payload);
        if (payload.processed >= payload.total) {
          this.off('INDEX_PROGRESS', progressHandler);
          resolve();
        }
      };
      this.on('INDEX_PROGRESS', progressHandler);
    });
  }

  async search(query, options = {}) {
    this.worker.postMessage({ type: 'SEARCH', payload: { query, options } });

    return new Promise(resolve => {
      const handler = (payload) => {
        this.off('SEARCH_RESULTS', handler);
        resolve(payload.results);
      };
      this.on('SEARCH_RESULTS', handler);
    });
  }

  async save() {
    this.worker.postMessage({ type: 'SAVE' });
    return new Promise(resolve => {
      this.on('SAVED', () => resolve());
    });
  }

  async clear() {
    this.worker.postMessage({ type: 'CLEAR' });
    return new Promise(resolve => {
      this.on('CLEARED', () => resolve());
    });
  }

  destroy() {
    this.worker.terminate();
    this.listeners.clear();
  }
}
```

### 3.5 React Hook 示例

```javascript
// hooks/useSirchmunk.js
import { useEffect, useCallback, useRef, useState } from 'react';
import { SearchService } from '../services/search-service';
import { enumerateDirectory } from '../utils/file-enumerator';

export function useSirchmunk() {
  const serviceRef = useRef(null);
  const [initialized, setInitialized] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const [progress, setProgress] = useState({ processed: 0, total: 0 });
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    serviceRef.current = new SearchService();

    serviceRef.current.init().then(() => {
      setInitialized(true);
    });

    return () => {
      serviceRef.current?.destroy();
    };
  }, []);

  const selectDirectory = useCallback(async () => {
    try {
      setIndexing(true);
      setError(null);

      const files = await enumerateDirectory();
      console.log(`Found ${files.length} files to index`);

      await serviceRef.current.indexFiles(files, (p) => {
        setProgress(p);
      });

      setIndexing(false);
    } catch (err) {
      setError(err.message);
      setIndexing(false);
    }
  }, []);

  const search = useCallback(async (query) => {
    try {
      setSearching(true);
      setError(null);

      const searchResults = await serviceRef.current.search(query);
      setResults(searchResults);
      setSearching(false);
    } catch (err) {
      setError(err.message);
      setSearching(false);
    }
  }, []);

  const clearIndex = useCallback(async () => {
    await serviceRef.current.clear();
    setResults([]);
    setProgress({ processed: 0, total: 0 });
  }, []);

  return {
    initialized,
    indexing,
    progress,
    searching,
    results,
    error,
    selectDirectory,
    search,
    clearIndex,
  };
}
```

---

## 性能基准

### 测试环境
- Browser: Chrome 120+
- CPU: 8-core
- Memory: 16GB
- Dataset: 1000 files (~100MB text)

### 索引性能

| 指标 | 目标值 | 实测值 |
|------|--------|--------|
| 首次索引时间 | <60s | 15-25s |
| 增量索引时间 | <10s | 2-5s |
| 索引内存峰值 | <500MB | 200-400MB |
| IndexedDB 写入时间 | <10s | 3-8s |

### 搜索性能

| 指标 | 目标值 | 实测值 |
|------|--------|--------|
| 简单查询 (<10 结果) | <200ms | 50-100ms |
| 复杂查询 (100 结果) | <500ms | 100-300ms |
| 中文分词查询 | <500ms | 150-400ms |
| 多关键词 AND | <500ms | 100-350ms |

---

## 风险与挑战

### 风险 1：大文件内存溢出

**问题描述**
浏览器标签页内存限制约 2-4GB，索引大量文件或超大文件时可能 OOM

**影响程度**: 🔴 高

**缓解措施**:
1. 单文件上限 50MB，超大文件跳过或仅索引前 N 页
2. 分批索引：每批 100 文件，批间 `gc()` 提示
3. 流式解析 PDF/Excel，避免全量加载
4. 监控内存：`performance.memory.usedJSHeapSize`

```javascript
// utils/memory-monitor.js
export function checkMemoryLimit(estimatedMB, limitMB = 1500) {
  if (performance.memory) {
    const usedMB = performance.memory.usedJSHeapSize / 1048576;
    if (usedMB + estimatedMB > limitMB) {
      throw new Error(`Memory limit exceeded: ${usedMB} + ${estimatedMB} > ${limitMB}MB`);
    }
  }
  return true;
}
```

---

### 风险 2：中文分词效果差

**问题描述**
FlexSearch 默认按字符切分，中文搜索效果不佳（如"搜索引擎"会被分成"搜""索""引""擎"）

**影响程度**: 🟡 中

**缓解措施**:
1. 使用 `tokenize: 'full'` 模式（全字符组合）
2. 集成轻量分词库（如 `segmentit` 浏览器版）
3. 对常用技术术语建立同义词表

```javascript
// Custom tokenizer for CJK
const cjkTokenizer = (text) => {
  // FlexSearch built-in CJK support
  // Or integrate with segmentit for better Chinese tokenization
  return text; // FlexSearch handles CJK character n-grams automatically
};

const index = new Document({
  tokenize: cjkTokenizer,
  // ...
});
```

---

### 风险 3：PDF 解析性能

**问题描述**
PDF.js 解析大文件（>100 页）耗时长，阻塞 Worker 线程

**影响程度**: 🟡 中

**缓解措施**:
1. 懒加载：仅解析搜索命中文件的 PDF
2. 分页解析：每页独立解析，可中断
3. 预提取文本：首次索引时提取，后续缓存

```javascript
async function extractFromPDF(file, maxPages = 50) {
  const arrayBuffer = await file.arrayBuffer();
  const pdf = await PDFJS.getDocument({ data: arrayBuffer }).promise;

  let text = '';
  const pagesToProcess = Math.min(pdf.numPages, maxPages);

  for (let i = 1; i <= pagesToProcess; i++) {
    // Yield to main thread every 10 pages
    if (i % 10 === 0) await new Promise(r => setTimeout(r, 0));

    const page = await pdf.getPage(i);
    const textContent = await page.getTextContent();
    text += textContent.items.map(item => item.str).join(' ') + '\n';
  }

  return text;
}
```

---

### 风险 4：Safari 兼容性

**问题描述**
File System Access API 在 Safari 支持有限（iOS 15.4+ 部分支持）

**影响程度**: 🟡 中

**缓解措施**:
1. 降级方案：使用 `<input type="file" webkitdirectory multiple>`
2. 提示用户切换 Chrome/Edge
3. 服务端备用：Safari 用户走传统上传流程

```javascript
// Fallback for Safari
async function selectFilesFallback() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.webkitdirectory = true;
    input.multiple = true;

    input.onchange = () => {
      const files = Array.from(input.files).map(f => ({
        file: f,
        path: f.webkitRelativePath,
        size: f.size,
        type: f.type,
      }));
      resolve(files);
    };

    input.click();
  });
}
```

---

### 风险 5：索引持久化失效

**问题描述**
IndexedDB 可能被用户清理或达到配额限制

**影响程度**: 🟢 低

**缓解措施**:
1. 估算配额：`navigator.storage.estimate()`
2. 持久化提示：`navigator.storage.persist()`
3. 版本迁移：IndexedDB schema versioning
4. 自动重建：索引失效时提示用户重新索引

```javascript
// Check storage quota
async function checkStorageQuota() {
  const estimate = await navigator.storage.estimate();
  const usageMB = estimate.usage / 1048576;
  const quotaMB = estimate.quota / 1048576;

  console.log(`Storage: ${usageMB.toFixed(1)}MB / ${quotaMB.toFixed(1)}MB`);

  if (usageMB / quotaMB > 0.8) {
    console.warn('Storage quota nearly exceeded');
  }

  // Request persistent storage
  if (navigator.storage && navigator.storage.persist) {
    const persisted = await navigator.storage.persist();
    console.log(`Storage persistence: ${persisted}`);
  }
}
```

---

### 风险 6：PPTX/特殊格式不支持

**问题描述**
PPTX、CAD、视频等格式在浏览器无成熟解析方案

**影响程度**: 🟢 低

**缓解措施**:
1. 混合模式：不支持的格式标记为"需服务端处理"
2. 用户选择：提示是否上传这些文件到服务器
3. 元数据索引：至少索引文件名、大小、修改时间

```javascript
const SUPPORTED_FORMATS = [
  '.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml',
  '.pdf', '.docx', '.xlsx', '.xls', '.csv', '.html', '.xml',
];

function needsServerProcessing(file) {
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  return !SUPPORTED_FORMATS.includes(ext);
}
```

---

## 依赖清单

### package.json 新增依赖

```json
{
  "dependencies": {
    "flexsearch": "^0.7.43",
    "pdfjs-dist": "^4.0.379",
    "mammoth": "^1.6.0",
    "xlsx": "^0.18.5",
    "idb": "^8.0.0",
    "file-system-access-api": "^1.0.3"
  },
  "devDependencies": {
    "@types/flexsearch": "^0.7.6",
    "@types/pdfjs-dist": "^2.13.129",
    "@types/mammoth": "^1.6.0",
    "@types/xlsx": "^0.0.36"
  }
}
```

### 总体积估算

| 依赖 | 压缩前 | Gzip 后 |
|------|--------|---------|
| flexsearch | 180KB | 50KB |
| pdfjs-dist | 3MB | 800KB |
| mammoth | 600KB | 200KB |
| xlsx | 2MB | 600KB |
| idb | 10KB | 4KB |
| **总计** | **~5.8MB** | **~1.6MB** |

**优化建议**:
- PDF.js 按需加载（仅当选择包含 PDF 的目录时）
- XLSX 按需加载（仅当选择包含 Excel 的目录时）
- 使用动态 import 实现 code splitting

---

## 开发计划

### 阶段 1：核心功能（2 周）

| 任务 | 工期 | 交付物 |
|------|------|--------|
| File System Access API 集成 | 2 天 | 目录选择 + 文件枚举 |
| 纯文本索引与搜索 | 3 天 | 支持 .txt/.md/.py 等 |
| FlexSearch Worker 架构 | 2 天 | 后台索引不阻塞 UI |
| 基础 UI 组件 | 3 天 | 搜索框 + 结果列表 |

### 阶段 2：格式扩展（2 周）

| 任务 | 工期 | 交付物 |
|------|------|--------|
| PDF.js 集成 | 2 天 | PDF 文本提取 |
| mammoth.js 集成 | 1 天 | DOCX 文本提取 |
| SheetJS 集成 | 2 天 | XLSX 文本提取 |
| IndexedDB 持久化 | 2 天 | 索引自动保存/加载 |
| 进度与错误处理 | 2 天 | 用户体验优化 |

### 阶段 3：云端协同（1 周）

| 任务 | 工期 | 交付物 |
|------|------|--------|
| 片段上传 API | 2 天 | 仅上传命中内容 |
| LLM 知识聚合 | 2 天 | 云端摘要生成 |
| 跨会话复用 | 1 天 | KnowledgeCluster 持久化 |

### 阶段 4：优化与测试（1 周）

| 任务 | 工期 | 交付物 |
|------|------|--------|
| 性能基准测试 | 2 天 | 性能报告 |
| 内存优化 | 1 天 | 大文件处理 |
| Safari 降级方案 | 1 天 | 兼容性修复 |
| E2E 测试 | 2 天 | 自动化测试 |

**总计**: 6 周（1 名前端工程师）

---

## 验收标准

### 功能验收

- [ ] 用户可选择本地目录进行索引
- [ ] 支持 .txt/.md/.py/.js/.json 等纯文本格式
- [ ] 支持 PDF/DOCX/XLSX 格式文本提取
- [ ] 关键词搜索返回命中文件和片段
- [ ] 索引持久化（刷新页面后保留）
- [ ] 增量索引（文件变更只更新部分）

### 性能验收

- [ ] 1000 文件索引时间 < 60 秒
- [ ] 搜索响应时间 < 500ms
- [ ] 内存峰值 < 1GB
- [ ] UI 无卡顿（FPS > 50）

### 兼容性验收

- [ ] Chrome 100+ ✅
- [ ] Edge 100+ ✅
- [ ] Firefox 100+ ⚠️ (File System API 有限)
- [ ] Safari 15.4+ ⚠️ (降级方案)

---

## 参考资料

- [FlexSearch GitHub](https://github.com/nextapps-de/flexsearch)
- [PDF.js Documentation](https://mozilla.github.io/pdf.js/)
- [File System Access API](https://developer.mozilla.org/en-US/docs/Web/API/File_System_Access_API)
- [IndexedDB API](https://developer.mozilla.org/en-US/docs/Web/API/IndexedDB_API)
- [Web Workers](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API)

---

## 附录：与 ripgrep-all 对比

| 特性 | ripgrep-all | FlexSearch 方案 |
|------|-------------|-----------------|
| **搜索速度** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **索引构建** | 无索引 | 需首次索引 |
| **PDF 支持** | ✅ | ✅ |
| **DOCX 支持** | ✅ | ✅ |
| **XLSX 支持** | ✅ | ✅ |
| **PPTX 支持** | ✅ | ❌ |
| **内存占用** | 低 | 中 |
| **首次启动** | 即时 | 需索引等待 |
| **后续搜索** | 快 | 极快 |
| **隐私保护** | 本地 | 本地 |
| **网络依赖** | 无 | 无（可选云端） |

---

*文档创建时间：2026-03-07*
*最后更新：2026-03-07*
