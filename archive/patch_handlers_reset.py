import re

with open("/root/KsyshaTest/handlers.py", "r") as f:
    content = f.read()

# We need to replace the unconditional db.set_setting("rollback_active", "1")
# with a conditional one inside admin_confirm_rollback.

# First, remove the unconditional block
unconditional_block = """        # 1. Store persistent state in SQLite settings
        db.set_setting("rollback_active", "1")
        db.set_setting("rollback_previous_commit", current_commit)
        db.set_setting("rollback_previous_version", current_ver)
        db.set_setting("rollback_target_commit", commit)
        
        # Save history for diagnostics
        from datetime import datetime, timezone
        db.set_setting("last_rollback_date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        db.set_setting("last_rollback_target", commit)
        db.set_setting("last_rollback_source_ver", current_ver)
        db.set_setting("last_rollback_dest_ver", ver)"""

if unconditional_block in content:
    content = content.replace(unconditional_block, "")
else:
    print("Warning: unconditional block not found")

# Now replace the checkout block
checkout_block = """        # 2. Perform git checkout to target commit in host workspace
        import subprocess
        checkout_res = subprocess.run(
            ["git", "checkout", commit],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10
        )"""

new_checkout_block = """        # 2. Perform git checkout to target commit in host workspace
        import subprocess
        from datetime import datetime, timezone

        check_main = subprocess.run(["git", "rev-parse", "main"], cwd=repo_root, capture_output=True, text=True)
        is_main = check_main.returncode == 0 and check_main.stdout.strip().startswith(commit[:7])

        if is_main:
            # We are returning to the tip of main. Clear rollback state.
            db.delete_setting("rollback_active")
            db.delete_setting("rollback_previous_commit")
            db.delete_setting("rollback_previous_version")
            db.delete_setting("rollback_target_commit")
            
            db.set_setting("last_rollback_date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
            db.set_setting("last_rollback_target", "main")
            db.set_setting("last_rollback_source_ver", current_ver)
            db.set_setting("last_rollback_dest_ver", ver)

            checkout_res = subprocess.run(
                ["git", "checkout", "main"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10
            )
        else:
            # Rolling back to older commit. Set rollback_active to block releases.
            db.set_setting("rollback_active", "1")
            db.set_setting("rollback_previous_commit", current_commit)
            db.set_setting("rollback_previous_version", current_ver)
            db.set_setting("rollback_target_commit", commit)
            
            db.set_setting("last_rollback_date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
            db.set_setting("last_rollback_target", commit)
            db.set_setting("last_rollback_source_ver", current_ver)
            db.set_setting("last_rollback_dest_ver", ver)

            checkout_res = subprocess.run(
                ["git", "checkout", commit],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10
            )"""

if checkout_block in content:
    content = content.replace(checkout_block, new_checkout_block)
    with open("/root/KsyshaTest/handlers.py", "w") as f:
        f.write(content)
    print("Patch applied successfully.")
else:
    print("Warning: checkout block not found")

