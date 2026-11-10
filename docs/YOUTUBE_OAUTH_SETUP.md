# YouTube Data API v3 OAuth2 Setup Guide

**Project Root:** `D:\AI Arts\AI\`  
**Target Publication Time:** 10:00 AM IST daily (04:30 UTC)  
**Initial Publishing Mode:** `MANUAL_APPROVAL`  

---

## 1. Google Cloud Console Setup

To enable automated or one-click uploads to your YouTube channel:

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g. `AI-Shorts-Studio`).
3. In the sidebar, navigate to **APIs & Services > Library**.
4. Search for **YouTube Data API v3** and click **Enable**.
5. Navigate to **APIs & Services > OAuth consent screen**:
   - User Type: **External**.
   - App Name: `AI Shorts Studio`.
   - Add your Gmail as developer and test user.
6. Navigate to **APIs & Services > Credentials**:
   - Click **Create Credentials > OAuth client ID**.
   - Application Type: **Desktop Application**.
   - Name: `Shorts Studio Desktop Client`.
7. Download the credentials JSON file and save it as:
   `D:\AI Arts\AI\backend\client_secrets.json`

---

## 2. Authorization Flow

1. On the first upload attempt or via the Studio Dashboard:
   - A secure local OAuth2 browser consent prompt opens.
   - Sign in with your YouTube channel's Google account.
   - Click **Allow** to grant `youtube.upload` permissions.
2. The refresh token is saved securely to:
   `D:\AI Arts\AI\backend\token.json`
3. Antigravity and the backend automatically reuse and refresh this token silently.
4. Your account password is **never requested or stored**.

---

## 3. Scheduled Publishing Workflow

Once authenticated:
1. Video is uploaded initially with `privacyStatus: "private"`.
2. The returned `videoId` is monitored for YouTube encoding completion.
3. The custom 1080×1920 thumbnail is set via `youtube.thumbnails().set()`.
4. The publication schedule is locked for **10:00 AM IST**.
5. During development, the default mode is `MANUAL_APPROVAL`, so videos remain private on disk/channel until approved.
