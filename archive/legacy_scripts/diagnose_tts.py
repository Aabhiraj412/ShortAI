import asyncio
import os
import socket
import sys
import time
import edge_tts

AUDIO_DIR = r"D:\AI Arts\AI\media\audio"
TEST_OUTPUT = os.path.join(AUDIO_DIR, "test_voice.mp3")
PRIMARY_VOICE = "en-US-ChristopherNeural"
FALLBACK_VOICE = "en-US-GuyNeural"
TEST_TEXT = "Hello, this is a voice generation test."
TIMEOUT_SECONDS = 45

def check_network():
    print("--- 1. Network & DNS Diagnostics ---", flush=True)
    endpoints = [
        ("speech.platform.bing.com", 443),
        ("eastus.tts.speech.microsoft.com", 443)
    ]
    for host, port in endpoints:
        start = time.time()
        try:
            ip = socket.gethostbyname(host)
            dns_time = (time.time() - start) * 1000
            print(f"[DNS OK] {host} -> {ip} ({dns_time:.1f}ms)", flush=True)
            
            s = socket.create_connection((ip, port), timeout=5)
            s.close()
            tcp_time = (time.time() - start) * 1000
            print(f"[TCP OK] Connected to {host}:{port} in {tcp_time:.1f}ms", flush=True)
        except Exception as e:
            print(f"[NETWORK ERROR] Failed to connect to {host}:{port}: {e}", flush=True)

async def test_tts_voice(voice_name: str, output_path: str) -> bool:
    print(f"\n--- Testing Edge-TTS Voice: {voice_name} ---", flush=True)
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
            print(f"Removed previous test file: {output_path}", flush=True)
        except Exception as e:
            print(f"Warning: Could not remove old file: {e}", flush=True)
    
    start_time = time.time()
    try:
        print(f"Initializing Communicate with voice='{voice_name}'...", flush=True)
        communicate = edge_tts.Communicate(text=TEST_TEXT, voice=voice_name)
        print("Calling communicate.save() with timeout...", flush=True)
        
        await asyncio.wait_for(communicate.save(output_path), timeout=TIMEOUT_SECONDS)
        elapsed = time.time() - start_time
        
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            if file_size > 0:
                print(f"[SUCCESS] Voice '{voice_name}' rendered successfully!", flush=True)
                print(f"  File: {output_path}", flush=True)
                print(f"  Size: {file_size} bytes ({file_size / 1024:.2f} KB)", flush=True)
                print(f"  Time: {elapsed:.2f}s", flush=True)
                return True
            else:
                print(f"[ERROR] Output file exists but is 0 bytes.", flush=True)
                return False
        else:
            print(f"[ERROR] Output file was not created.", flush=True)
            return False
            
    except asyncio.TimeoutError:
        print(f"[TIMEOUT ERROR] TTS generation timed out after {TIMEOUT_SECONDS}s", flush=True)
        return False
    except Exception as e:
        print(f"[EXCEPTION] Failed with voice '{voice_name}': {type(e).__name__}: {e}", flush=True)
        return False

async def main():
    print("=" * 60, flush=True)
    print("Edge-TTS Diagnostic Suite", flush=True)
    print(f"Python: {sys.version.split()[0]} | Edge-TTS: {edge_tts.__version__}", flush=True)
    print(f"Target Directory: {AUDIO_DIR}", flush=True)
    print("=" * 60, flush=True)
    
    check_network()
    
    # Test primary voice
    success = await test_tts_voice(PRIMARY_VOICE, TEST_OUTPUT)
    
    # If primary failed, test fallback voice
    if not success:
        print("\nAttempting fallback voice...", flush=True)
        success = await test_tts_voice(FALLBACK_VOICE, TEST_OUTPUT)
        
    if success:
        print("\n" + "=" * 60, flush=True)
        print("DIAGNOSIS COMPLETE: Edge-TTS is fully functional!", flush=True)
        print("=" * 60, flush=True)
        sys.exit(0)
    else:
        print("\n" + "=" * 60, flush=True)
        print("DIAGNOSIS COMPLETE: Edge-TTS test failed.", flush=True)
        print("=" * 60, flush=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
