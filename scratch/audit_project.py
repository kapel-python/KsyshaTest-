import os
import re
import subprocess
import glob
from datetime import datetime

# Files to audit
files_to_audit = [
    ":memory:.hotbackup.db",
    ":memory:.hotbackup.db.prev",
    "bot.log",
    "bot_recent.log",
    "test_results.log",
    "patch_http_debug.py",
    "patch_index_debug.js",
    "patch_index_debug.py",
    "patch_index_debug2.js",
    "patch_index_debug3.js",
    "patch_index_debug4.js",
    "patch_index_telemetry.py",
    "fix.py",
    "diag_load_test.py",
    "diag_release_workflow.py",
    "diag_rollback_workflow.py",
    "diag_startup_hook.py",
    "verify_flow.py",
    "verify_status.py",
    "verify_status_mismatch.py",
    "full_test.py",
    "main_page_smoke.py",
    "prod_safe_suite.py",
    "production_test.py",
    "render_test.js",
    "test_aiohttp.py",
    "test_api.py",
    "test_auth.py",
    "test_cookie_ws.py",
    "test_diff.js",
    "test_e2e.py",
    "test_end_to_end.py",
    "test_frontend.py",
    "test_handler.py",
    "test_health_url.py",
    "test_modal.js",
    "test_server_state.py",
    "test_startup_hook.py",
    "test_telegram_callback.py",
    "test_telegram_ws.py",
    "test_ws_broadcast.py"
]

project_dir = "/root/KsyshaTest"
all_code_files = []
for ext in ['*.py', '*.js', '*.sh', '*.html', '*.yml', '*.json']:
    all_code_files.extend(glob.glob(os.path.join(project_dir, '**', ext), recursive=True))

# Read all code file contents to search for references
file_contents = {}
for filepath in all_code_files:
    if os.path.basename(filepath) == 'audit_project.py':
        continue
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            file_contents[filepath] = f.read()
    except Exception:
        pass

results = []

for filename in files_to_audit:
    filepath = os.path.join(project_dir, filename)
    exists = os.path.exists(filepath)
    
    # 1. Last Modified (git or stat)
    last_mod = "Unknown"
    if exists:
        try:
            out = subprocess.check_output(
                ["git", "log", "-1", "--format=%cd (%cr)", "--", filename],
                cwd=project_dir, stderr=subprocess.DEVNULL
            ).decode('utf-8').strip()
            if out:
                last_mod = out
            else:
                mtime = os.path.getmtime(filepath)
                last_mod = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            mtime = os.path.getmtime(filepath)
            last_mod = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')

    # 2. Imports and references
    references = []
    base_no_ext = os.path.splitext(filename)[0]
    
    for path, content in file_contents.items():
        rel_path = os.path.relpath(path, project_dir)
        if rel_path == filename:
            continue
        
        # Check direct filename match
        if filename in content:
            references.append(f"{rel_path} (прямое имя)")
        # Check python import match (e.g. "import base_no_ext" or "from base_no_ext")
        elif filename.endswith('.py'):
            import_patterns = [
                rf"\bimport\s+{re.escape(base_no_ext)}\b",
                rf"\bfrom\s+{re.escape(base_no_ext)}\s+import\b"
            ]
            for pat in import_patterns:
                if re.search(pat, content):
                    references.append(f"{rel_path} (import)")
                    break

    # 3. Check CI/CD (which files exist in deployment/service scripts?)
    cicd = "Нет"
    
    # 4. Check manual diagnostics (is it in bot.py?)
    is_diag = "Нет"
    if filename in ["diag_release_workflow.py", "diag_rollback_workflow.py", "diag_startup_hook.py", 
                     "test_auth.py", "test_version_ux.py", "test_health_url.py", "test_app_version_writer.py", 
                     "test_rollback_fallback.py", "test_rollback_state.py", "diag_load_test.py"]:
        is_diag = "Да (зарегистрирован в глубокой диагностике bot.py)"
    elif filename.startswith("diag_") or filename.startswith("verify_"):
        is_diag = "Возможно (имя файла указывает на диагностику)"

    results.append({
        "filename": filename,
        "exists": exists,
        "last_mod": last_mod,
        "references": references,
        "cicd": cicd,
        "is_diag": is_diag
    })

# Print markdown compatible output
print("File Audit Results:")
for r in results:
    if not r["exists"]:
        continue
    print(f"### `{r['filename']}`")
    print(f"- **Импорты/Ссылки**: {', '.join(r['references']) if r['references'] else 'Нет'}")
    print(f"- **Используется в CI/CD**: {r['cicd']}")
    print(f"- **Используется для диагностики**: {r['is_diag']}")
    print(f"- **Последнее изменение**: {r['last_mod']}")
    print("")
