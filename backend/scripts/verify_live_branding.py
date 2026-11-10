"""
Live HTTP Verification Script for Dashboard YouTube Branding.
"""
import urllib.request

endpoints = [
    "http://127.0.0.1:8000/favicon.ico",
    "http://127.0.0.1:8000/static/branding/Youtube-Icon.png",
    "http://127.0.0.1:8000/dashboard"
]

for url in endpoints:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read()
            ct = resp.headers.get("Content-Type")
            print(f"{url} -> Status: {resp.status}, Content-Type: {ct}, Size: {len(content)} bytes")
            if "dashboard" in url:
                html = content.decode("utf-8")
                print("  Contains favicon link:", "/favicon.ico?v=20261111" in html)
                print("  Contains header icon class:", "yt-header-icon" in html)
                print("  Contains YouTube branding src:", "/static/branding/Youtube-Icon.png?v=20261111" in html)
    except Exception as e:
        print(f"Error querying {url}: {e}")
