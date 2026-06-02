import os
import sys
import time
import sqlite3
import shutil
from pathlib import Path
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config
from database import Database, db
from http_api import _generate_thumbnail

# Create temporary database copy for safe profiling
ORIGINAL_DB = "/workspace/data/memories.db"
TEMP_DB = "/root/KsyshaTest/scratch/memories_profile.db"

def setup_temp_db():
    shutil.copy(ORIGINAL_DB, TEMP_DB)
    # Instantiate temporary Database instance
    temp_db_mgr = Database(db_path=TEMP_DB)
    temp_db_mgr.hot_backup_enabled = True
    temp_db_mgr.hot_backup_path = TEMP_DB + ".hotbackup.db"
    
    # Populate with 500 memories, 100 wishes, 100 events
    with temp_db_mgr._get_connection() as conn:
        # Get couple and user
        cursor = conn.execute("SELECT user_id FROM users LIMIT 1")
        row = cursor.fetchone()
        user_id = row['user_id'] if row else 0
        
        cursor = conn.execute("SELECT id FROM couples LIMIT 1")
        row = cursor.fetchone()
        couple_id = row['id'] if row else 1
        
        # Populate memories
        print("Inserting 500 mock memories...")
        mem_data = []
        for i in range(500):
            mem_data.append((
                user_id, "memories", f"Mock Memory Title #{i}", "2026-06-02",
                f"Content for memory #{i}. Standard text for profiling purposes.",
                "photo", f"/media/u{user_id}_1780397782_mock_{i}.jpg", couple_id
            ))
        conn.executemany("""
            INSERT INTO memories (user_id, category, title, date, content, media_type, media_path, couple_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, mem_data)
        
        # Populate wishes
        print("Inserting 100 mock wishes...")
        wish_data = []
        for i in range(100):
            wish_data.append((
                user_id, f"Mock Wish #{i}", "created", f"/media/u{user_id}_1780397782_wish_{i}.jpg"
            ))
        conn.executemany("""
            INSERT INTO wishes (user_id, content, status, media_path)
            VALUES (?, ?, ?, ?)
        """, wish_data)
        
        # Populate events
        print("Inserting 100 mock events...")
        event_data = []
        for i in range(100):
            event_data.append((
                user_id, f"Mock Event #{i}", f"Description #{i}", "2026-06-02 12:00:00",
                f"/media/u{user_id}_1780397782_event_{i}.jpg"
            ))
        conn.executemany("""
            INSERT INTO scheduled_events (user_id, title, description, event_datetime, media_path)
            VALUES (?, ?, ?, ?, ?)
        """, event_data)
        
        conn.commit()
    return temp_db_mgr

def profile_db_backup(temp_db):
    print("\n=== 1. DB Save & Hot Backup Profiling ===")
    
    with temp_db._get_connection() as conn:
        cursor = conn.execute("SELECT user_id FROM users LIMIT 1")
        row = cursor.fetchone()
        user_id = row['user_id'] if row else 0
    
    # Test 1: Write with hot backup enabled
    temp_db.hot_backup_enabled = True
    start = time.perf_counter()
    id_with = temp_db.add_memory(user_id, "memories", "Profiling Moment With Backup", "2026-06-02", "Moment content")
    time_with = (time.perf_counter() - start) * 1000.0
    
    # Test 2: Write with hot backup disabled
    temp_db.hot_backup_enabled = False
    start = time.perf_counter()
    id_without = temp_db.add_memory(user_id, "memories", "Profiling Moment Without Backup", "2026-06-02", "Moment content")
    time_without = (time.perf_counter() - start) * 1000.0
    
    # Test 3: Backup alone
    temp_db.hot_backup_enabled = True
    start = time.perf_counter()
    temp_db._sync_hot_backup(reason="measurement")
    time_backup = (time.perf_counter() - start) * 1000.0
    
    share_pct = (time_backup / time_with) * 100.0
    
    print(f"Database file size (populated): {Path(TEMP_DB).stat().st_size / 1024:.1f} KB")
    print(f"Average time of moment creation (total): {time_with:.2f} ms")
    print(f"Time taken by database save alone (no backup): {time_without:.2f} ms")
    print(f"Time taken by hot backup alone: {time_backup:.2f} ms")
    print(f"Share of hot backup from total query: {share_pct:.1f}%")

def profile_media_access(temp_db):
    print("\n=== 2. Media Access Check Profiling ===")
    
    filename = "u0_1780397782_mock_499.jpg"
    like_mask = f"%{filename}%"
    
    with temp_db._get_connection() as conn:
        # Table counts
        memories_count = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
        wishes_count = conn.execute("SELECT count(*) FROM wishes").fetchone()[0]
        events_count = conn.execute("SELECT count(*) FROM scheduled_events").fetchone()[0]
        
        print(f"Table row counts:")
        print(f"  - memories: {memories_count}")
        print(f"  - wishes: {wishes_count}")
        print(f"  - scheduled_events: {events_count}")
        print()
        
        # Test query 1
        q1 = "SELECT user_id, couple_id, media_path, media_items FROM memories WHERE media_path LIKE ? OR media_items LIKE ?"
        print("SQL Query for memories check:")
        print(f"  {q1}")
        
        # Explain query plan 1
        explain_q1 = f"EXPLAIN QUERY PLAN {q1}"
        rows_exp = conn.execute(explain_q1, (like_mask, like_mask)).fetchall()
        print("EXPLAIN QUERY PLAN:")
        for r in rows_exp:
            print(f"  {dict(r)}")
        
        # Measure query times
        iterations = 50
        start = time.perf_counter()
        for _ in range(iterations):
            conn.execute(q1, (like_mask, like_mask)).fetchall()
        avg_q1 = ((time.perf_counter() - start) / iterations) * 1000.0
        
        q2 = "SELECT user_id, media_path FROM wishes WHERE media_path LIKE ?"
        start = time.perf_counter()
        for _ in range(iterations):
            conn.execute(q2, (like_mask,)).fetchall()
        avg_q2 = ((time.perf_counter() - start) / iterations) * 1000.0
        
        q3 = "SELECT user_id, media_path FROM scheduled_events WHERE media_path LIKE ?"
        start = time.perf_counter()
        for _ in range(iterations):
            conn.execute(q3, (like_mask,)).fetchall()
        avg_q3 = ((time.perf_counter() - start) / iterations) * 1000.0
        
        total_time = avg_q1 + avg_q2 + avg_q3
        print(f"\nAverage execution times (500 memories, 100 wishes, 100 events):")
        print(f"  - Memories query (SCAN): {avg_q1:.3f} ms")
        print(f"  - Wishes query (SCAN): {avg_q2:.3f} ms")
        print(f"  - Events query (SCAN): {avg_q3:.3f} ms")
        print(f"  - Total media access check time: {total_time:.3f} ms")

def profile_preview_generation():
    print("\n=== 3. Preview Generation Profiling ===")
    
    img_path = Path("/root/KsyshaTest/media/temp_test_image.jpg")
    img_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1920, 1080), color="blue")
    img.save(img_path, "JPEG", quality=90)
    
    try:
        # Measure current LANCZOS + optimize=True
        start = time.perf_counter()
        with Image.open(img_path) as im:
            im.draft(None, (400, 400))
            w, h = im.size
            scale = min(400 / w, 400 / h)
            new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
            thumb = im.resize(new_size, Image.LANCZOS)
            thumb_path = img_path.with_stem(img_path.stem + "_thumb_lanczos").with_suffix(".jpg")
            thumb.save(thumb_path, "JPEG", quality=75, optimize=True)
        time_current = (time.perf_counter() - start) * 1000.0
        
        # Measure optimized BILINEAR + optimize=False
        start = time.perf_counter()
        with Image.open(img_path) as im:
            im.draft(None, (400, 400))
            w, h = im.size
            scale = min(400 / w, 400 / h)
            new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
            thumb = im.resize(new_size, Image.BILINEAR)
            thumb_path_opt = img_path.with_stem(img_path.stem + "_thumb_bilinear").with_suffix(".jpg")
            thumb.save(thumb_path_opt, "JPEG", quality=75, optimize=False)
        time_optimized = (time.perf_counter() - start) * 1000.0
        
        print(f"Current preview generation time (LANCZOS + optimize): {time_current:.2f} ms")
        print(f"Expected preview generation time (BILINEAR, no optimize): {time_optimized:.2f} ms")
        reduction = ((time_current - time_optimized) / time_current) * 100.0
        print(f"Preview generation time reduction: {reduction:.1f}%")
        
        # Cleanup temp images
        img_path.unlink(missing_ok=True)
        thumb_path.unlink(missing_ok=True)
        thumb_path_opt.unlink(missing_ok=True)
    except Exception as e:
        print(f"ERROR profiling preview: {e}")
        img_path.unlink(missing_ok=True)

def cleanup_temp_db():
    for p in [TEMP_DB, TEMP_DB + ".hotbackup.db", TEMP_DB + ".hotbackup.db.prev"]:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass

if __name__ == "__main__":
    print("Setting up temporary populated database...")
    temp_db = setup_temp_db()
    try:
        profile_db_backup(temp_db)
        profile_media_access(temp_db)
        profile_preview_generation()
    finally:
        print("\nCleaning up temporary database...")
        cleanup_temp_db()
        print("Cleanup done.")
