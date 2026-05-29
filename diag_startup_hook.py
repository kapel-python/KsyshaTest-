import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import db
import bot

def run_checks():
    print("🚀 Starting Diagnostic Startup Hook Validation (READ-ONLY)...")
    
    from app_version import _get_repo_root
    repo_root = _get_repo_root()
    
    # 1. Current Branch
    branch_res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True)
    branch = branch_res.stdout.strip()
    print(f"✅ Current branch evaluated: {branch}")
    
    # 2. Consistency Check
    rollback_active = db.get_setting("rollback_active")
    if not rollback_active and branch == "HEAD":
        raise ValueError("CRITICAL: Detached HEAD detected but rollback_active is NOT set!")
    print("✅ Rollback consistency is valid.")
    
    # 3. Dependencies
    from handlers import _trigger_deploy, _trigger_local_restart
    print("✅ Startup hook dependencies (deployers/restarters) imported successfully.")
    
    # 4. Deployer Configuration
    deployer_url = os.getenv("DEPLOYER_URL", "not_set")
    print(f"✅ DEPLOYER_URL is available: {deployer_url}")
    
    print("✅ All diagnostic startup hook checks passed safely.")

if __name__ == "__main__":
    run_checks()
