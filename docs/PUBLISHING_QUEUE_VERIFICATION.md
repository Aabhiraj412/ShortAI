# Publishing Queue Verification Report

**Date:** 2026-11-12  
**Workspace:** `D:\Agents\CrazzyDocs`

## Architecture

Videos enter Publishing Queue after QC passes. User selects publication time independently.

## Implementation

- **youtube_publishing_engine.py**: manages `publishing_ledger` DB table
- **youtube_service.py**: uploads video and sets `scheduledPublishAt`
- **main.py**: `/api/publishing/queue` endpoints
- **dashboard.html**: Publishing Queue tab

## Key APIs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/publishing/queue` | List queued videos |
| POST | `/api/publishing/queue/{ledger_id}/schedule` | Set publication time |
| POST | `/api/publishing/queue/{ledger_id}/upload` | Upload to YouTube |
