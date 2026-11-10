"""
YouTube OAuth Configuration & Verification Wizard for CrazzyDocs Shorts Studio.
Phase 5, Stage 3.12 Standard.

Provides a secure local interface for:
1. Installing & verifying Google OAuth 2.0 client secrets (backend/client_secrets.json).
2. Authorizing YouTube Data API v3 access with local loopback server.
3. Inspecting channel identity: Channel ID, Name, Thumbnail, Subscribers, and Status.
4. Performing verified, resumable private uploads with custom thumbnails.
5. Preserving the strict publishing gate: PUBLIC PUBLISHING REMAINS PERMANENTLY DISABLED.
6. Disconnecting channel and revoking credentials.

Never prints or logs secret credentials, access tokens, or refresh tokens.
"""

import argparse
import json
import logging
import os
import sys
from typing import Dict, Optional, Tuple

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from backend.app.services.youtube_publishing_engine import (
    YouTubePublishingEngine,
    YouTubeOAuthManager,
    MetadataGenerator,
    YouTubeMetadata,
    PublishingDatabase,
    PublicationRecord,
    compute_file_sha256,
    youtube_publishing_engine,
    DEFAULT_CLIENT_SECRETS,
    DEFAULT_TOKEN_FILE,
)

logger = logging.getLogger("configure_youtube_oauth")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

GITIGNORE_PATH = os.path.join(WORKSPACE_ROOT, ".gitignore")


def ensure_gitignored():
    """Ensures backend/client_secrets.json and backend/token.json are git-ignored."""
    patterns = ["*client_secret*.json", "token.json", "*credentials*.json"]
    if os.path.exists(GITIGNORE_PATH):
        with open(GITIGNORE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        missing = [p for p in patterns if p not in content]
        if missing:
            with open(GITIGNORE_PATH, "a", encoding="utf-8") as f:
                f.write("\n# YouTube OAuth Credentials (Strictly Gitignored)\n")
                for p in missing:
                    f.write(f"{p}\n")
    else:
        with open(GITIGNORE_PATH, "w", encoding="utf-8") as f:
            f.write("# YouTube OAuth Credentials (Strictly Gitignored)\n")
            for p in patterns:
                f.write(f"{p}\n")


def print_header(title: str):
    print("\n" + "=" * 65)
    print(f"  {title.upper()}")
    print("=" * 65)


def check_auth_status(oauth: Optional[YouTubeOAuthManager] = None) -> Dict:
    """Retrieves and prints current OAuth and channel connection status."""
    ensure_gitignored()
    mgr = oauth or YouTubeOAuthManager()
    status = mgr.get_auth_status()
    db = PublishingDatabase()
    quota_used = db.get_quota_used_today()

    print_header("YouTube Channel Authentication Status")
    print(f"Client Secrets Installed : {'[YES] ' + mgr.client_secrets_file if status['secrets_installed'] else '[NO] Missing ' + mgr.client_secrets_file}")
    print(f"Token File Installed     : {'[YES] ' + mgr.token_file if status['token_installed'] else '[NO] Missing ' + mgr.token_file}")
    print(f"Is Authenticated         : {'[YES] Active' if status['is_authenticated'] else '[NO] Inactive'}")
    print(f"Can Refresh Token        : {'[YES] Refresh token verified' if status['can_refresh'] else '[NO] Missing refresh token'}")
    if status.get("token_expiry"):
        print(f"Token Expiry (UTC)       : {status['token_expiry']}")
    print(f"Channel Connected        : {'[YES]' if status['channel_connected'] else '[NO]'}")
    print(f"Daily Quota Used Today   : {quota_used} / 10,000 units")

    ch = status.get("channel_info")
    if ch:
        print("\n--- CONNECTED CHANNEL DETAILS ---")
        print(f"  Channel ID       : {ch.get('id')}")
        print(f"  Channel Title    : {ch.get('title')}")
        print(f"  Channel Handle   : {ch.get('custom_url') or 'N/A'}")
        print(f"  Subscribers      : {ch.get('subscribers', 'Hidden')}")
        print(f"  Total Videos     : {ch.get('video_count', 'N/A')}")
        print(f"  Total Views      : {ch.get('view_count', 'N/A')}")
        print(f"  Thumbnail URL    : {ch.get('thumbnail_url') or 'N/A'}")
        print(f"  Privacy Status   : {ch.get('privacy_status', 'N/A')}")
        print(f"  Long Uploads     : {ch.get('long_uploads_status', 'N/A')}")
    elif status.get("error"):
        print(f"\n[NOTE / ERROR]: {status['error']}")

    if not status["secrets_installed"]:
        print("\n[ACTION REQUIRED]:")
        print("  1. Visit Google Cloud Console: https://console.cloud.google.com/apis/credentials")
        print("  2. Create an OAuth 2.0 Client ID (Application type: Desktop app).")
        print("  3. Download the client secret JSON file.")
        print(f"  4. Place it at: {mgr.client_secrets_file}")
        print("     Or run: python backend/scripts/configure_youtube_oauth.py --install-secret <path_to_json>")

    elif not status["is_authenticated"]:
        print("\n[NEXT STEP]:")
        print("  Run interactive browser sign-in:")
        print("  python backend/scripts/configure_youtube_oauth.py --auth")

    return status


def run_interactive_auth(port: int = 8088) -> bool:
    """Executes browser-based OAuth authorization flow."""
    mgr = YouTubeOAuthManager()
    if not os.path.exists(mgr.client_secrets_file):
        print(f"\n[ERROR] Missing {mgr.client_secrets_file}.")
        print("Please obtain OAuth 2.0 Desktop Client credentials from Google Cloud Console first.")
        return False

    print("\n" + "=" * 65)
    print("  LAUNCHING GOOGLE OAUTH AUTHORIZATION")
    print("=" * 65)
    print("Starting local redirect server on localhost...")
    print("Your web browser will open Google's account sign-in screen.")
    print("Sign in with the Google account owning your YouTube channel and grant permissions.")

    success, channel_info, err = mgr.start_interactive_auth(port=port, open_browser=True)
    if success and channel_info:
        print("\n[SUCCESS] YouTube channel successfully connected and verified!")
        print(f"  Channel Title : {channel_info.get('title')}")
        print(f"  Channel ID    : {channel_info.get('id')}")
        print(f"  Handle        : {channel_info.get('custom_url') or 'N/A'}")
        print(f"  Subscribers   : {channel_info.get('subscribers', 'Hidden')}")
        print(f"  Thumbnail URL : {channel_info.get('thumbnail_url', 'N/A')}")
        return True
    elif success:
        print("\n[SUCCESS] OAuth token acquired, but channel info could not be fetched.")
        return True
    else:
        print(f"\n[FAILURE] OAuth authorization failed: {err}")
        return False


def disconnect_channel() -> bool:
    """Disconnects YouTube channel by revoking and deleting token.json."""
    mgr = YouTubeOAuthManager()
    print_header("Disconnecting YouTube Channel")
    ok = mgr.disconnect_channel()
    if ok:
        print("[SUCCESS] Local OAuth token revoked and removed. Channel disconnected.")
    else:
        print("[ERROR] Failed to remove token file.")
    return ok


def test_refresh_token() -> bool:
    """Explicitly tests token refresh against Google's OAuth endpoint and verifies API call."""
    mgr = YouTubeOAuthManager()
    print_header("Testing Google OAuth Token Refresh")
    if not os.path.exists(mgr.token_file):
        print(f"[ERROR] Token file does not exist at {mgr.token_file}. Please authenticate first via --auth.")
        return False

    status_before = mgr.get_auth_status()
    print(f"Before Refresh Expiry : {status_before.get('token_expiry')}")
    print("Sending refresh request to Google OAuth token endpoint...")

    ok, msg = mgr.refresh_token_safely()
    if not ok:
        print(f"[FAILED] {msg}")
        return False

    print(f"[SUCCESS] {msg}")
    status_after = mgr.get_auth_status()
    print(f"After Refresh Expiry  : {status_after.get('token_expiry')}")

    # Confirm API access with refreshed credentials
    ch = status_after.get("channel_info")
    if ch:
        print("\n--- RE-VERIFIED CHANNEL ACCESS WITH REFRESHED TOKEN ---")
        print(f"  Channel ID    : {ch.get('id')}")
        print(f"  Channel Title : {ch.get('title')}")
        print(f"  Channel Handle: {ch.get('custom_url') or 'N/A'}")
        print(f"  Subscribers   : {ch.get('subscribers', '0')}")
        print("\n[VERIFIED] Access token refresh succeeded and channel API is fully operational!")
        return True
    else:
        print("[WARNING] Token was refreshed, but channel query failed.")
        return False


def run_private_upload(
    video_path: Optional[str] = None,
    thumbnail_path: Optional[str] = None,
    topic_title: Optional[str] = None,
    opening_hook: Optional[str] = None,
    visibility: str = "private",
) -> Dict:
    """
    Performs verified, resumable private upload of an approved showcase video.
    Strictly prohibits public uploads.
    """
    # Security check
    if visibility == "public":
        raise PermissionError("SECURITY GATE: Automatic public publishing is prohibited. Only 'private' or 'unlisted' allowed.")

    # Default to latest verified canonical showcase deliverables if not specified
    default_video = os.path.join(WORKSPACE_ROOT, "media", "productions", "prod_cycle_4_20261110_1800", "video", "showcase_cycle_4_20261110_1800.mp4")
    if not os.path.exists(default_video):
        default_video = os.path.join(WORKSPACE_ROOT, "media", "video", "showcase_cycle_4_20261110_1800.mp4")
    if not os.path.exists(default_video):
        default_video = os.path.join(WORKSPACE_ROOT, "media", "video", "showcase_stage_3_4_tsutomu_yamaguchi.mp4")

    default_thumb = os.path.join(WORKSPACE_ROOT, "media", "productions", "prod_cycle_4_20261110_1800", "thumbnails", "thumb_cycle_4_20261110_1800.png")
    if not os.path.exists(default_thumb):
        default_thumb = os.path.join(WORKSPACE_ROOT, "media", "thumbnails", "thumb_cycle_4_20261110_1800.png")

    v_path = video_path or default_video
    t_path = thumbnail_path or default_thumb

    if not os.path.exists(v_path):
        print(f"[ERROR] Video file not found: {v_path}")
        return {"success": False, "error": f"Video file not found: {v_path}"}

    v_title = topic_title or "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived"
    v_hook = opening_hook or "She fell over 33,000 feet without a parachute—and survived to tell the story."

    metadata = MetadataGenerator.generate_shorts_metadata(
        topic_title=v_title,
        opening_hook=v_hook,
        category="Unbelievable Real-Life Stories",
        key_entities=["Vesna Vulovic", "JAT Flight 367", "Guinness World Record", "Aviation History"],
        attributions=[
            {"original_filename": "vesna_archive.jpg", "license": "Public Domain", "attribution": "Historical Press Archive"},
            {"original_filename": "dc9_aircraft.jpg", "license": "CC BY-SA 3.0", "attribution": "Aviation Memorial Registry"}
        ]
    )

    print_header("Executing Verified Private Video Upload")
    print(f"Video File       : {v_path} ({os.path.getsize(v_path) / (1024*1024):.2f} MB)")
    print(f"Thumbnail File   : {t_path} ({os.path.getsize(t_path) / (1024*1024):.2f} MB)")
    print(f"Visibility       : {visibility.upper()} (Safe mode)")
    print(f"Title            : {metadata.title}")
    print(f"Category ID      : {metadata.category_id}")
    print(f"Tags Count       : {len(metadata.tags)}")

    def progress(p: float):
        sys.stdout.write(f"\r  Uploading: {int(p * 100)}% complete...")
        sys.stdout.flush()

    res = youtube_publishing_engine.upload_short(
        video_path=v_path,
        topic_title=v_title,
        metadata=metadata,
        thumbnail_path=t_path if os.path.exists(t_path) else None,
        visibility=visibility,
        progress_callback=progress,
        allow_public_bypass=False,
    )
    print("\n")

    if res.get("success"):
        print("[SUCCESS] Private upload completed successfully!")
        print(f"  YouTube Video ID : {res.get('video_id')}")
        print(f"  YouTube URL      : {res.get('youtube_url')}")
        print(f"  Visibility       : {res.get('visibility')}")
        print(f"  Thumbnail Status : {res.get('thumbnail_status')}")
        if res.get("thumbnail_error"):
            print(f"  Thumbnail Notice : {res.get('thumbnail_error')}")
        print(f"  Local Thumbnail  : {res.get('thumbnail_local_path')} (Preserved locally)")
        print(f"  Ledger Record ID : {res.get('ledger_id')}")
    else:
        print(f"[FAILED] Upload could not be completed: {res.get('error')}")
        print(f"  Status Code      : {res.get('status')}")

    return res


def main():
    parser = argparse.ArgumentParser(description="CrazzyDocs YouTube OAuth & Upload Wizard")
    parser.add_argument("--status", action="store_true", help="Display current OAuth and channel connection status")
    parser.add_argument("--setup", action="store_true", help="Interactive OAuth setup and connection wizard")
    parser.add_argument("--install-secret", type=str, help="Install OAuth client secrets from file path or JSON string")
    parser.add_argument("--auth", action="store_true", help="Launch interactive browser OAuth authorization flow")
    parser.add_argument("--port", type=int, default=8088, help="Port for local OAuth redirect server (default: 8088)")
    parser.add_argument("--disconnect", action="store_true", help="Revoke token and disconnect YouTube channel")
    parser.add_argument("--verify-channel", action="store_true", help="Verify and display connected channel details")
    parser.add_argument("--upload-showcase", action="store_true", help="Upload approved showcase video as PRIVATE")
    parser.add_argument("--video-path", type=str, help="Custom video file path for upload")
    parser.add_argument("--thumbnail-path", type=str, help="Custom thumbnail file path for upload")
    parser.add_argument("--title", type=str, help="Custom topic title for upload")
    parser.add_argument("--test-refresh", action="store_true", help="Safely test OAuth token refresh with Google and verify API access")

    args = parser.parse_args()

    ensure_gitignored()

    if args.test_refresh:
        ok = test_refresh_token()
        sys.exit(0 if ok else 1)

    if args.install_secret:
        mgr = YouTubeOAuthManager()
        ok, msg = mgr.install_client_secrets(args.install_secret)
        print(msg)
        if ok:
            check_auth_status(mgr)
        sys.exit(0 if ok else 1)

    if args.disconnect:
        ok = disconnect_channel()
        sys.exit(0 if ok else 1)

    if args.auth:
        ok = run_interactive_auth(port=args.port)
        sys.exit(0 if ok else 1)

    if args.verify_channel:
        status = check_auth_status()
        sys.exit(0 if status.get("channel_connected") else 1)

    if args.upload_showcase:
        res = run_private_upload(
            video_path=args.video_path,
            thumbnail_path=args.thumbnail_path,
            topic_title=args.title,
            visibility="private"
        )
        sys.exit(0 if res.get("success") else 1)

    if args.setup:
        print_header("Interactive YouTube OAuth Setup Wizard")
        mgr = YouTubeOAuthManager()
        if not os.path.exists(mgr.client_secrets_file):
            print("OAuth client secrets file not found.")
            print(f"Target location: {mgr.client_secrets_file}\n")
            path_input = input("Enter path to your downloaded client secret JSON file (or press Enter to cancel): ").strip()
            if path_input:
                ok, msg = mgr.install_client_secrets(path_input)
                print(msg)
                if not ok:
                    sys.exit(1)
            else:
                print("Setup cancelled.")
                sys.exit(0)

        # Launch OAuth
        ok = run_interactive_auth(port=args.port)
        if ok:
            check_auth_status(mgr)
        sys.exit(0 if ok else 1)

    # Default to status check if no specific action given
    check_auth_status()


if __name__ == "__main__":
    main()
