import asyncio
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db
from app_version import get_git_status_info, _get_repo_root, get_version_metadata
import handlers

async def main():
    print("🚀 Starting release 1.0.235 flow...")
    
    status_info = get_git_status_info()
    repo_root = _get_repo_root()
    
    branch = status_info.get("branch")
    print(f"Current branch: {branch}")
    
    if branch == "HEAD" or not branch or branch == "—":
        print("Checking out main branch...")
        subprocess.run(["git", "checkout", "main"], cwd=repo_root, check=True)
        status_info = get_git_status_info()
        branch = status_info.get("branch")
        print(f"Switched to branch: {branch}")
        
    current_ver, _ = get_version_metadata()
    print(f"Current version on disk: {current_ver}")
    
    new_version = "1.0.235"
    description = "Release v1.0.235"
    
    print(f"Target version: {new_version}")
    print(f"Target description: {description}")
    
    try:
        subprocess.run(["git", "config", "user.name"], cwd=repo_root, check=True, capture_output=True)
    except Exception:
        subprocess.run(["git", "config", "user.name", "Ksysha Bot"], cwd=repo_root)
        subprocess.run(["git", "config", "user.email", "bot@ksysha.local"], cwd=repo_root)
        
    print("Updating app_version.py...")
    handlers.update_app_version_file(new_version, description)
    
    print("Staging and committing...")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    commit_msg = f"Release v{new_version}: {description}"
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_root, check=True)
    
    print("Pushing to remote...")
    subprocess.run(["git", "push"], cwd=repo_root, check=True)
    
    res_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True)
    new_commit = res_hash.stdout.strip()
    print(f"New commit: {new_commit}")
    
    print("Registering release in DB...")
    with db._get_connection() as conn:
        db._register_version_history_entry(conn, new_version, description, git_commit=new_commit)
        
    db.delete_setting("rollback_active")
    db.delete_setting("rollback_previous_commit")
    db.delete_setting("rollback_previous_version")
    db.delete_setting("rollback_target_commit")
    
    print("Triggering local restart...")
    ok, reason = await handlers._trigger_local_restart()
    print(f"Restart trigger status: {ok}, reason: {reason}")
    print("🎉 Release flow finished!")

if __name__ == "__main__":
    asyncio.run(main())
