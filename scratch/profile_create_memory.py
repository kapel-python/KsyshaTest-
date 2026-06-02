import os
import sys
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config
from database import db

logging.basicConfig(level=logging.INFO)

def main():
    print(f"Database path: {db.db_path}")
    print(f"Hot backup enabled: {db.hot_backup_enabled}")
    
    # Get a valid user ID
    with db._get_connection() as conn:
        cursor = conn.execute("SELECT user_id FROM users LIMIT 1")
        row = cursor.fetchone()
        if not row:
            print("No users found in database!")
            return
        user_id = row['user_id']
        print(f"Using valid user_id: {user_id}")
    
    # Measure time of a single insert (add_memory)
    start_time = time.perf_counter()
    memory_id = db.add_memory(
        user_id=user_id,
        category="memories",
        title="Profiling test",
        date="2026-06-02",
        content="Profiling memory creation performance."
    )
    duration_ms = (time.perf_counter() - start_time) * 1000.0
    print(f"add_memory completed in {duration_ms:.2f} ms, created memory ID: {memory_id}")
    
    # Measure database insert WITHOUT hot backup (if we disable it temporarily)
    db.hot_backup_enabled = False
    start_time = time.perf_counter()
    memory_id_no_backup = db.add_memory(
        user_id=user_id,
        category="memories",
        title="Profiling test no backup",
        date="2026-06-02",
        content="Profiling memory creation without hot backup."
    )
    duration_no_backup_ms = (time.perf_counter() - start_time) * 1000.0
    print(f"add_memory (WITHOUT backup) completed in {duration_no_backup_ms:.2f} ms, ID: {memory_id_no_backup}")
    
    # Measure time of _sync_hot_backup itself
    db.hot_backup_enabled = True
    start_time = time.perf_counter()
    db._sync_hot_backup(reason="profile_test")
    backup_duration_ms = (time.perf_counter() - start_time) * 1000.0
    print(f"_sync_hot_backup completed in {backup_duration_ms:.2f} ms")
    
    # Let's clean up
    with db._get_connection() as conn:
        conn.execute("DELETE FROM memories WHERE id IN (?, ?)", (memory_id, memory_id_no_backup))
        conn.commit()
    print("Cleanup done.")

if __name__ == "__main__":
    main()
