import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app_version import get_version_metadata, _get_repo_root
from database import db
import handlers

def run_checks():
    print("🚀 Starting Diagnostic Release Validation (READ-ONLY)...")
    
    # 1. App Version Syntax & Extraction
    current_ver, current_desc = get_version_metadata()
    print(f"✅ app_version.py readable. Version: {current_ver}, Desc: {current_desc}")
    
    # 2. Database Sync
    history = db.get_version_history()
    if not history:
        raise ValueError("version_history is empty")
    latest = history[0]
    if latest["version"] != current_ver:
        raise ValueError(f"DB version {latest['version']} does not match app_version.py {current_ver}")
    print(f"✅ version_history matches app_version.py")
    
    # 3. Increment Logic
    next_ver = handlers.increment_patch_version(current_ver)
    print(f"✅ increment_patch_version works: {current_ver} -> {next_ver}")
    
    # 4. Git Workspace Health
    repo_root = _get_repo_root()
    log_res = subprocess.run(["git", "log", "-1", "--oneline"], cwd=repo_root, capture_output=True, text=True, check=True)
    print(f"✅ git log works: {log_res.stdout.strip()}")
    
    status_res = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=True)
    print(f"✅ git status works (modifications: {len(status_res.stdout.strip().splitlines())})")
    
    remote_res = subprocess.run(["git", "ls-remote", "origin", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True)
    print(f"✅ git ls-remote works (remote is accessible)")
    
    print("✅ All diagnostic release checks passed safely.")

if __name__ == "__main__":
    run_checks()
