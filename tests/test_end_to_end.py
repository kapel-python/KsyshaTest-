import sys
import subprocess
from database import db

repo_root = "/root/KsyshaTest"

def verify_state(step, expect_detached, expect_active):
    res = subprocess.run(["git", "branch", "--show-current"], cwd=repo_root, capture_output=True, text=True)
    is_detached = res.stdout.strip() == ""
    active = db.get_setting("rollback_active") == "1"
    
    print(f"[{step}] Detached: {is_detached} (expected {expect_detached}), Active: {active} (expected {expect_active})")
    if is_detached != expect_detached or active != expect_active:
        print(f"FAILED AT {step}!")
        sys.exit(1)

print("--- INITIAL STATE ---")
subprocess.run(["git", "checkout", "main"], cwd=repo_root, capture_output=True)
db.delete_setting("rollback_active")
verify_state("Initial", False, False)

print("\n--- 1. Rollback to old version ---")
# Simulating admin_confirm_rollback to 50dc070
commit = "50dc070"
subprocess.run(["git", "checkout", commit], cwd=repo_root, capture_output=True)
db.set_setting("rollback_active", "1")
verify_state("Rollback Old", True, True)

print("\n--- 2. Rollback to latest main ---")
# Simulating patched admin_confirm_rollback
commit = "ef7b40e"
check_main = subprocess.run(["git", "rev-parse", "main"], cwd=repo_root, capture_output=True, text=True)
is_main = check_main.returncode == 0 and check_main.stdout.strip().startswith(commit[:7])

if is_main:
    db.delete_setting("rollback_active")
    subprocess.run(["git", "checkout", "main"], cwd=repo_root, capture_output=True)
else:
    db.set_setting("rollback_active", "1")
    subprocess.run(["git", "checkout", commit], cwd=repo_root, capture_output=True)

verify_state("Rollback to Main", False, False)

print("\n--- 3. Release ---")
print("Release allowed because rollback_active is cleared and branch is main!")

