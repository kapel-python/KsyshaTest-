import os
import sys
import asyncio
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http_api import _generate_video_preview, _video_preview_semaphore

async def main():
    test_video = Path("/workspace/scratch/test_video.mp4")
    if not test_video.exists():
        print("Error: test_video.mp4 does not exist!")
        sys.exit(1)

    print("[+] Found test_video.mp4")

    # Clean up existing thumbnails
    for p in Path("/workspace/scratch/").glob("test_video_copy*_thumb.jpg"):
        try:
            p.unlink()
        except OSError:
            pass

    # Create 5 parallel copies of the video to avoid file collision and ensure concurrency testing
    copies = []
    for i in range(5):
        copy_path = Path(f"/workspace/scratch/test_video_copy_{i}.mp4")
        if copy_path.exists():
            copy_path.unlink()
        import shutil
        shutil.copy(str(test_video), str(copy_path))
        copies.append(copy_path)

    print("[+] Created 5 copies for concurrent generation testing")

    # To monitor concurrency, we will monkey-patch the command execution to measure concurrency
    active_tasks = 0
    max_concurrency = 0
    lock = asyncio.Lock()

    original_create_subprocess_exec = asyncio.create_subprocess_exec

    async def patched_create_subprocess_exec(*args, **kwargs):
        nonlocal active_tasks, max_concurrency
        async with lock:
            active_tasks += 1
            if active_tasks > max_concurrency:
                max_concurrency = active_tasks
            print(f"    [Active ffmpeg processes] entered: {active_tasks} (max: {max_concurrency})")
        
        try:
            # Let it run a bit to simulate realistic overlap if they run in parallel
            await asyncio.sleep(0.5)
            proc = await original_create_subprocess_exec(*args, **kwargs)
            return proc
        finally:
            async with lock:
                active_tasks -= 1
                print(f"    [Active ffmpeg processes] exited: {active_tasks}")

    asyncio.create_subprocess_exec = patched_create_subprocess_exec

    print("[+] Spawning 5 video preview generations concurrently...")
    start_time = time.monotonic()
    
    tasks = [_generate_video_preview(p) for p in copies]
    results = await asyncio.gather(*tasks)

    duration = time.monotonic() - start_time
    print(f"[+] All tasks finished in {duration:.2f} seconds")
    print(f"[+] Maximum measured parallel processes: {max_concurrency}")

    # Verify that max concurrency is <= 2
    if max_concurrency > 2:
        print(f"[-] ERROR: Max concurrency was {max_concurrency}, expected <= 2!")
        sys.exit(1)
    else:
        print("[+] SUCCESS: Concurrency is strictly bounded by the semaphore queue!")

    # Verify that all thumbnails were generated successfully
    for i, p in enumerate(copies):
        expected_thumb = p.with_stem(p.stem + "_thumb").with_suffix(".jpg")
        if not expected_thumb.is_file():
            print(f"[-] ERROR: Expected thumbnail not found for copy {i}: {expected_thumb}")
            sys.exit(1)
        # Check size of thumb is positive
        if expected_thumb.stat().st_size == 0:
            print(f"[-] ERROR: Generated thumbnail is empty: {expected_thumb}")
            sys.exit(1)

    print("[+] SUCCESS: All generated thumbnails are present and non-empty!")

    # Verify caching: second call should be instant and not trigger subprocess
    asyncio.create_subprocess_exec = lambda *args, **kwargs: exec("raise Exception('Subprocess spawned on cache hit!')")
    print("[+] Verifying cache hit behavior (should be immediate and not trigger ffmpeg)...")
    cache_start = time.monotonic()
    cached_result = await _generate_video_preview(copies[0])
    cache_duration = time.monotonic() - cache_start
    print(f"[+] Cache hit returned in {cache_duration * 1000:.2f} ms")

    if cached_result is None or not Path(cached_result).is_file():
        print("[-] ERROR: Cache hit did not return a valid path!")
        sys.exit(1)

    print("[+] SUCCESS: Video preview queue validation completed perfectly!")

    # Clean up copies
    for p in copies:
        p.unlink()
        thumb = p.with_stem(p.stem + "_thumb").with_suffix(".jpg")
        if thumb.exists():
            thumb.unlink()

if __name__ == "__main__":
    asyncio.run(main())
