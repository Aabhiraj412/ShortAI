"""
Stage 3.21F: YouTube Video Verification and Permanent Deletion Script.
Target: 4DCas1e4vHI
"""

import sys
import json
from datetime import datetime
from googleapiclient.errors import HttpError
from backend.app.services.youtube_service import YouTubePublisherService

EXPECTED_VIDEO_ID = "4DCas1e4vHI"
EXPECTED_CHANNEL_ID = "UCUOZOEBngXgt6W7fmus0bvw"
EXPECTED_LEDGER_TITLE = "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived"

def main():
    print("=================================================================")
    print("STAGE 3.21F — YOUTUBE VIDEO VERIFICATION AND PERMANENT DELETION")
    print("=================================================================")

    service = YouTubePublisherService()
    yt = service.get_youtube_client()
    if not yt:
        print("ERROR: Failed to obtain authenticated YouTube client.")
        sys.exit(1)

    # 1. Verify Channel ID
    ch_res = yt.channels().list(mine=True, part="id,snippet").execute()
    channels = ch_res.get("items", [])
    if not channels:
        print("ERROR: No channels found for authenticated user.")
        sys.exit(1)
    actual_channel_id = channels[0].get("id")
    actual_channel_title = channels[0].get("snippet", {}).get("title")
    print(f"Authenticated Channel: '{actual_channel_title}' (ID: {actual_channel_id})")
    assert actual_channel_id == EXPECTED_CHANNEL_ID, f"Channel ID mismatch: expected {EXPECTED_CHANNEL_ID}, got {actual_channel_id}"

    # 2. Query target video
    print(f"\nQuerying target video: {EXPECTED_VIDEO_ID}...")
    v_res = yt.videos().list(part="snippet,status", id=EXPECTED_VIDEO_ID).execute()
    items = v_res.get("items", [])
    if not items:
        print(f"ERROR: Video {EXPECTED_VIDEO_ID} not found on YouTube.")
        sys.exit(1)

    v = items[0]
    vid_id = v.get("id")
    snip = v.get("snippet", {})
    stat = v.get("status", {})

    actual_title = snip.get("title")
    actual_vid_channel_id = snip.get("channelId")
    actual_visibility = stat.get("privacyStatus")
    actual_upload_date = snip.get("publishedAt")

    print("\n--- Verified Target Video Details ---")
    print(f"  Video ID:        {vid_id}")
    print(f"  YouTube Title:   {actual_title}")
    print(f"  Ledger Title:    {EXPECTED_LEDGER_TITLE}")
    print(f"  Channel ID:      {actual_vid_channel_id}")
    print(f"  Visibility:      {actual_visibility}")
    print(f"  Published At:    {actual_upload_date}")

    # Strict verifications
    assert vid_id == EXPECTED_VIDEO_ID, f"Video ID mismatch: {vid_id}"
    assert actual_vid_channel_id == EXPECTED_CHANNEL_ID, f"Channel ownership mismatch: {actual_vid_channel_id}"
    assert actual_visibility == "private", f"Expected visibility 'private', found '{actual_visibility}'"

    # Verify no other video is selected
    print("\nVerifying that only target video is targeted...")
    # Confirm channel search
    search_res = yt.search().list(part="id", forMine=True, type="video", maxResults=50).execute()
    channel_video_ids = [it["id"]["videoId"] for it in search_res.get("items", []) if "videoId" in it.get("id", {})]
    print(f"Current total videos on channel: {len(channel_video_ids)} (IDs: {channel_video_ids})")
    assert EXPECTED_VIDEO_ID in channel_video_ids or len(channel_video_ids) == 1, "Target video not confirmed in channel video list"

    # 3. Permanently Delete Target Video
    print(f"\nExecuting permanent deletion of video: {EXPECTED_VIDEO_ID}...")
    try:
        del_req = yt.videos().delete(id=EXPECTED_VIDEO_ID)
        del_req.execute()
        print(f"SUCCESS: Delete request sent for video ID {EXPECTED_VIDEO_ID}.")
    except Exception as e:
        print(f"CRITICAL ERROR during deletion: {e}")
        sys.exit(1)

    # 4. Post-deletion verification
    print(f"\nVerifying video {EXPECTED_VIDEO_ID} is no longer available...")
    post_res = yt.videos().list(part="snippet,status", id=EXPECTED_VIDEO_ID).execute()
    post_items = post_res.get("items", [])
    if len(post_items) == 0:
        print(f"CONFIRMED: Video {EXPECTED_VIDEO_ID} returned 0 items. Permanent deletion verified!")
    else:
        print(f"ERROR: Video {EXPECTED_VIDEO_ID} still returned: {post_items}")
        sys.exit(1)

    # Re-check channel video list
    search_res_post = yt.search().list(part="id", forMine=True, type="video", maxResults=50).execute()
    post_vids = [it["id"]["videoId"] for it in search_res_post.get("items", []) if "videoId" in it.get("id", {})]
    print(f"Post-deletion channel video count: {len(post_vids)}")
    assert EXPECTED_VIDEO_ID not in post_vids, f"ERROR: {EXPECTED_VIDEO_ID} still in channel video list!"

    log_payload = {
        "deleted_video_id": EXPECTED_VIDEO_ID,
        "deleted_at": datetime.now().isoformat(),
        "channel_id": actual_channel_id,
        "channel_title": actual_channel_title,
        "youtube_title": actual_title,
        "ledger_title": EXPECTED_LEDGER_TITLE,
        "visibility_at_deletion": actual_visibility,
        "published_at": actual_upload_date,
        "post_deletion_verified": True
    }
    with open("D:/Agents/CrazzyDocs/docs/STAGE_3_21F_YOUTUBE_DELETION_LOG.json", "w", encoding="utf-8") as f:
        json.dump(log_payload, f, indent=2)
    print("Wrote deletion audit to D:/Agents/CrazzyDocs/docs/STAGE_3_21F_YOUTUBE_DELETION_LOG.json")

if __name__ == "__main__":
    main()
