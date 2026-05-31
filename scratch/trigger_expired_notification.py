import os
import sys
import time
from datetime import datetime, timezone, timedelta

# Add project path to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import db

def main():
    print("=== TRIGGERING EXPIRED EVENT FOR REAL BOT ===")
    db.db_path = "/workspace/data/memories.db"
    db.hot_backup_path = "/workspace/data/memories.db.hotbackup.db"
    db.hot_backup_prev_path = "/workspace/data/memories.db.hotbackup.db.prev"
    
    # Get creator and partner IDs from env or defaults
    creator_id = int(os.getenv("CREATOR_ID", "8036527559") or 8036527559)
    partner_id = int(os.getenv("KSUSHA_ID", "111111111") or 111111111)
    
    # Ensure they are in a couple in the running DB
    with db._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (creator_id, "creator"))
        conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (partner_id, "partner"))
        
        # Insert a couple if not exists
        conn.execute("""
            INSERT OR IGNORE INTO couples (user1_id, user2_id, created_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (min(creator_id, partner_id), max(creator_id, partner_id)))
        conn.commit()
        
    # Enable notifications for both
    db.set_user_setting(creator_id, "notifications_enabled", "1")
    db.set_user_setting(partner_id, "notifications_enabled", "1")
    db.set_user_setting(creator_id, "notif_events_enabled", "1")
    db.set_user_setting(partner_id, "notif_events_enabled", "1")
    db.set_user_setting(creator_id, "timezone", "Europe/Moscow")
    db.set_user_setting(partner_id, "timezone", "Europe/Moscow")
        
    # Create an expired event (e.g. 5 minutes ago)
    # The current time is around 2026-05-31 19:12
    expired_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    
    # Clear any past notifications for safety
    with db._get_connection() as conn:
        conn.execute("DELETE FROM scheduled_events WHERE title = 'PROD EXPIRED EVENT DEBUG'")
        conn.commit()

    event_id = db.add_scheduled_event(
        user_id=creator_id,
        title="PROD EXPIRED EVENT DEBUG",
        description="Debugging double notification delivery.",
        event_datetime=expired_time,
        is_recurring=0
    )
    
    print(f"Created expired event #{event_id} at {expired_time}. Waiting 15 seconds for scheduler...")
    time.sleep(15)
    print("Done waiting. Please check docker logs now.")

if __name__ == "__main__":
    main()
