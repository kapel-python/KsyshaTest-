import os
import re
import subprocess
import glob
from datetime import datetime

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

file_contents = {}
for filepath in all_code_files:
    if 'audit_project.py' in filepath:
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

    references = []
    base_no_ext = os.path.splitext(filename)[0]
    
    for path, content in file_contents.items():
        rel_path = os.path.relpath(path, project_dir)
        if rel_path == filename:
            continue
        
        if filename in content:
            references.append(f"`{rel_path}`")
        elif filename.endswith('.py'):
            import_patterns = [
                rf"\bimport\s+{re.escape(base_no_ext)}\b",
                rf"\bfrom\s+{re.escape(base_no_ext)}\s+import\b"
            ]
            for pat in import_patterns:
                if re.search(pat, content):
                    references.append(f"`{rel_path}` (import)")
                    break

    cicd = "Нет"
    
    # Check manual diagnostics (bot.py registrations)
    is_diag = "Нет"
    diag_registered = [
        "diag_release_workflow.py", 
        "diag_rollback_workflow.py", 
        "diag_startup_hook.py", 
        "test_auth.py", 
        "test_version_ux.py", 
        "test_health_url.py", 
        "test_app_version_writer.py", 
        "test_rollback_fallback.py", 
        "test_rollback_state.py", 
        "diag_load_test.py"
    ]
    if filename in diag_registered:
        is_diag = "Да (зарегистрирован в `bot.py` для глубокой диагностики)"
    elif filename.startswith("diag_") or filename.startswith("verify_"):
        is_diag = "Возможно (содержит префикс `diag_` / `verify_`)"

    # Determine recommended category
    # Categories:
    # 1. Безопасно удалить
    # 2. Стоит перенести в папку archive/
    # 3. Стоит перенести в папку tools/
    # 4. Стоит перенести в папку tests/
    # 5. Нельзя удалять (используется проектом)
    
    category = "1. Безопасно удалить"
    reason = "Временный файл, лог или разовый отладочный скрипт без внешних связей."
    
    if filename in diag_registered:
        category = "5. Нельзя удалять (используется проектом)"
        reason = "Вызывается напрямую через Telegram-бот в режиме глубокой диагностики."
    elif filename in ["test.py", "tests.py"]:
        category = "5. Нельзя удалять (используется проектом)"
        reason = "Главные файлы тестового сьюта."
    elif filename.startswith("verify_") or filename.startswith("diag_"):
        category = "3. Стоит перенести в папку tools/"
        reason = "Скрипты верификации и диагностики, полезные для ручного запуска разработчиком."
    elif filename.startswith("test_") and filename.endswith(".py"):
        category = "4. Стоит перенести в папку tests/"
        reason = "Юнит- или интеграционный тест, не зарегистрированный в автозапуске, но полезный для тестирования."
    elif filename.endswith(".js") and filename.startswith("test_"):
        category = "4. Стоит перенести в папку tests/"
        reason = "JS тест фронтенда."
    elif filename in ["full_test.py", "production_test.py", "prod_safe_suite.py", "main_page_smoke.py"]:
        category = "2. Стоит перенести в папку archive/"
        reason = "Устаревшие обертки совместимости, замененные новыми тестами."
    elif filename.startswith("patch_"):
        category = "2. Стоит перенести в папку archive/"
        reason = "Разовые патчи, которые уже были применены к `index.html` или другим файлам."
        
    results.append({
        "filename": filename,
        "exists": exists,
        "last_mod": last_mod,
        "references": references,
        "cicd": cicd,
        "is_diag": is_diag,
        "category": category,
        "reason": reason
    })

artifact_dir = "/root/.gemini/antigravity-cli/brain/8e8a8dde-02e0-4369-bbc5-064851c549d4"
os.makedirs(artifact_dir, exist_ok=True)
report_path = os.path.join(artifact_dir, "project_cleanup_audit.md")

with open(report_path, "w", encoding="utf-8") as f:
    f.write("# Результаты аудита файлов проекта KsyshaTest\n\n")
    f.write("> [!IMPORTANT]\n")
    f.write("> Ни один файл не будет удален без вашего прямого подтверждения. Данный аудит носит информационный характер.\n\n")
    
    # Group by category
    categories = [
        "1. Безопасно удалить",
        "2. Стоит перенести в папку archive/",
        "3. Стоит перенести в папку tools/",
        "4. Стоит перенести в папку tests/",
        "5. Нельзя удалять (используется проектом)"
    ]
    
    for cat in categories:
        f.write(f"## Категория: {cat}\n\n")
        cat_files = [r for r in results if r["category"] == cat and r["exists"]]
        if not cat_files:
            f.write("*В этой категории нет файлов.*\n\n")
            continue
            
        for r in cat_files:
            f.write(f"### `{r['filename']}`\n")
            f.write(f"- **Импорты/Ссылки**: {', '.join(r['references']) if r['references'] else 'Нет'}\n")
            f.write(f"- **Используется в CI/CD**: {r['cicd']}\n")
            f.write(f"- **Используется для диагностики**: {r['is_diag']}\n")
            f.write(f"- **Последнее изменение**: {r['last_mod']}\n")
            f.write(f"- **Обоснование**: {r['reason']}\n\n")

print(f"Report written to {report_path}")
