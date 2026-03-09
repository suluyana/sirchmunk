# API Reference

## Base URL

```
https://api.sirchmunk.com/v1
```

## Endpoints

### POST /search

Execute a search query.

**Request:**
```json
{
  "query": "string (required)",
  "paths": ["array of paths"],
  "mode": "FAST|DEEP|FILENAME_ONLY",
  "max_tokens": 4000
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "answer": "Generated answer",
    "sources": [
      {
        "file": "path/to/file.md",
        "relevance": 0.95,
        "excerpt": "..."
      }
    ]
  }
}
```

### GET /health

Check service health.

**Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 86400
}
```

### POST /knowledge/clusters

Create or update a knowledge cluster.

**Request:**
```json
{
  "name": "string",
  "description": "string",
  "file_ids": ["array of file IDs"]
}
```

### GET /knowledge/clusters/{id}

Retrieve a specific knowledge cluster.

**Response:**
```json
{
  "id": "cluster_001",
  "name": "Authentication Systems",
  "description": "Documentation about auth flows",
  "created_at": "2024-01-15T10:30:00Z",
  "files": [
    {
      "id": "file_001",
      "name": "authentication.md",
      "size": 4096
    }
  ]
}
```

## Rate Limits

| Tier | Requests/minute | Requests/day |
|------|-----------------|--------------|
| Free | 10 | 100 |
| Pro | 60 | 10000 |
| Enterprise | 600 | Unlimited |

## Error Handling

All errors follow this format:

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable message",
    "details": {}
  }
}
```
