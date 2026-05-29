import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import db
import handlers

def run_checks():
    print("🚀 Starting Diagnostic Rollback Validation (READ-ONLY)...")
    
    # 1. History Validation
    history = db.get_version_history()
    if len(history) < 2:
        raise ValueError("Not enough versions in history to rollback")
    target_rec = history[1]
    target_commit = target_rec["git_commit"]
    print(f"✅ Rollback targets available. Previous commit: {target_commit}")
    
    # 2. Commit Object Verification
    if not target_commit:
        raise ValueError("Target commit is empty")
        
    from app_version import _get_repo_root
    repo_root = _get_repo_root()
    # verify commit exists in local git objects
    cat_res = subprocess.run(["git", "cat-file", "-t", target_commit], cwd=repo_root, capture_output=True, text=True, check=False)
    if cat_res.returncode != 0 or cat_res.stdout.strip() != "commit":
        raise ValueError(f"Commit {target_commit} does not exist in local git objects")
    print("✅ Previous commit exists locally.")
    
    # 3. Rollback State Keys
    rollback_active = db.get_setting("rollback_active")
    print(f"✅ Rollback keys readable. rollback_active: {rollback_active}")
    
    # 4. UI Renderer Safety
    status_text = handlers._get_rollback_status_block()
    if not status_text:
        raise ValueError("Status text is empty")
    print("✅ Rollback status UI rendered successfully (no KeyErrors).")

if __name__ == "__main__":
    run_checks()
