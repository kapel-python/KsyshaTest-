import re

with open("/root/KsyshaTest/handlers.py", "r", encoding="utf-8") as f:
    content = f.read()

start_marker = "def _get_version_by_commit(commit_hash: str) -> Optional[dict]:"
end_marker = 'BACKUP_EXPORT_DIR = "backup"'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print("Error: Markers not found!")
    exit(1)

new_code = """@router.callback_query(F.data.startswith("admin_version_history:"))
async def admin_version_history(callback: CallbackQuery):
    \"\"\"Отображение истории версий с пагинацией\"\"\"
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    page_str = callback.data.replace("admin_version_history:", "")
    try:
        page = int(page_str)
    except ValueError:
        page = 0
        
    versions = db.get_version_history()
    total_versions = len(versions)
    page_size = 10
    total_pages = max(1, (total_versions + page_size - 1) // page_size)
    
    if page < 0:
        page = 0
    elif page >= total_pages:
        page = total_pages - 1
        
    start_idx = page * page_size
    end_idx = start_idx + page_size
    page_versions = versions[start_idx:end_idx]
    
    lines = ["📦 История версий"]
    
    if not page_versions:
        lines.append("\\nИстория версий пуста.")
    else:
        for v in page_versions:
            ver = v.get("version") or "—"
            desc = v.get("description") or "—"
            commit = v.get("git_commit")
            commit_str = commit[:7] if commit else "—"
            
            date_str = "—"
            created_at = v.get("created_at")
            if created_at:
                try:
                    dt_part = created_at.split('.')[0]
                    dt = datetime.strptime(dt_part, "%Y-%m-%d %H:%M:%S")
                    date_str = dt.strftime("%d.%m.%Y %H:%M")
                except Exception:
                    try:
                        dt = datetime.fromisoformat(created_at)
                        date_str = dt.strftime("%d.%m.%Y %H:%M")
                    except Exception:
                        date_str = created_at
                
            # Escape HTML characters to avoid parsing errors in Telegram
            ver_esc = html.escape(str(ver))
            desc_esc = html.escape(str(desc))
            commit_esc = html.escape(str(commit_str))
            date_esc = html.escape(str(date_str))
            
            lines.append(
                f"\\nВерсия: {ver_esc}\\n"
                f"Описание: {desc_esc}\\n"
                f"Коммит: {commit_esc}\\n"
                f"Дата: {date_esc}"
            )
            
    lines.append(f"\\nСтраница {page + 1} из {total_pages}")
    
    text = "\\n".join(lines)
    
    keyboard = []
    
    # 1. Под списком отображаем по одной кнопке для каждой версии на текущей странице
    for v in page_versions:
        ver = v.get("version")
        if ver:
            keyboard.append([InlineKeyboardButton(text=f"📦 Версия {ver}", callback_data=f"admin_version_detail:{ver}:{page}")])
            
    # 2. Кнопки пагинации
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_version_history:{page - 1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"admin_version_history:{page + 1}"))
        
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await callback_edit_or_answer(callback, 
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("admin_version_detail:"))
async def admin_version_detail(callback: CallbackQuery):
    \"\"\"Детальная информация о выбранной версии\"\"\"
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    parts = callback.data.split(":")
    ver_name = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    
    await callback.answer("Загружаю детали версий…")
    
    # Fetch details from DB
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT version, description, git_commit, created_at FROM version_history WHERE version = ? LIMIT 1",
            (ver_name,)
        ).fetchone()
        
    if not row:
        await callback.answer("Версия не найдена.", show_alert=True)
        return
        
    ver = row["version"]
    desc = row["description"]
    commit = row["git_commit"]
    commit_str = commit[:7] if commit else "—"
    
    date_str = "—"
    created_at = row["created_at"]
    if created_at:
        try:
            dt_part = created_at.split('.')[0]
            dt = datetime.strptime(dt_part, "%Y-%m-%d %H:%M:%S")
            date_str = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            try:
                dt = datetime.fromisoformat(created_at)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except Exception:
                date_str = created_at
                
    ver_esc = html.escape(str(ver))
    desc_esc = html.escape(str(desc))
    commit_esc = html.escape(str(commit_str))
    date_esc = html.escape(str(date_str))
    
    text = (
        f"📦 <b>Версия {ver_esc}</b>\\n\\n"
        "Описание:\\n"
        f"{desc_esc}\\n\\n"
        "Коммит:\\n"
        f"{commit_esc}\\n\\n"
        "Дата:\\n"
        f"{date_esc}\\n\\n"
        "Действия:\\n"
        "(rollback functionality will be implemented later)"
    )
    
    keyboard = [
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=f"admin_version_history:{page}")]
    ]
    
    await callback_edit_or_answer(callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )


"""

replaced_content = content[:start_idx] + new_code + content[end_idx:]

with open("/root/KsyshaTest/handlers.py", "w", encoding="utf-8") as f:
    f.write(replaced_content)

print("Success: Replacement complete!")
