"""
Автотесты проекта (memories bot + site).
Запускаются при старте bot.py, результат отправляется создателю в Telegram.
"""

import asyncio
import os
import tempfile
import json
import re
import math
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import List, Tuple
import logging

logger = logging.getLogger(__name__)

# ─── Результаты ───────────────────────────────────────────────
_results: List[Tuple[bool, str]] = []   # (passed, name)

def _ok(name: str):
    _results.append((True, name))

def _fail(name: str, reason: str = ""):
    _results.append((False, f"{name}" + (f": {reason}" if reason else "")))

def _run(name: str, fn):
    """Синхронно запускает тест, перехватывает любые исключения."""
    try:
        fn()
        _ok(name)
    except AssertionError as e:
        _fail(name, str(e))
    except Exception as e:
        _fail(name, type(e).__name__ + ": " + str(e))

async def _run_async(name: str, coro_fn):
    try:
        await coro_fn()
        _ok(name)
    except AssertionError as e:
        _fail(name, str(e))
    except Exception as e:
        _fail(name, type(e).__name__ + ": " + str(e))

# ═══════════════════════════════════════════════════════════════
# БЛОК 1 — Config
# ═══════════════════════════════════════════════════════════════

def _test_config():
    from config import config, Config

    def t_creator_id():
        assert isinstance(config.CREATOR_ID, int), "CREATOR_ID не int"
        assert config.CREATOR_ID >= 0

    def t_ksusha_id():
        assert isinstance(config.KSUSHA_ID, int)

    def t_bot_token():
        assert isinstance(config.BOT_TOKEN, str)

    def t_date_met():
        assert config.DATE_MET is not None
        assert isinstance(config.DATE_MET, date)

    def t_site_open_date():
        assert config.SITE_OPEN_DATE is not None
        assert isinstance(config.SITE_OPEN_DATE, date)

    def t_categories():
        cats = config.CATEGORIES
        assert isinstance(cats, dict)
        assert len(cats) >= 3
        for k, v in cats.items():
            assert "title" in v
            assert "emoji" in v

    def t_media_folder_created():
        assert os.path.isdir(config.MEDIA_FOLDER)

    def t_api_secret_key():
        assert isinstance(config.API_SECRET_KEY, str)
        assert len(config.API_SECRET_KEY) > 0

    def t_passwords_exist():
        assert isinstance(config.SITE_AUTH_PASSWORD_KSYUSHA, str)
        assert isinstance(config.SITE_AUTH_PASSWORD_CREATOR, str)

    for name, fn in [
        ("Config: CREATOR_ID", t_creator_id),
        ("Config: KSUSHA_ID", t_ksusha_id),
        ("Config: BOT_TOKEN", t_bot_token),
        ("Config: DATE_MET", t_date_met),
        ("Config: SITE_OPEN_DATE", t_site_open_date),
        ("Config: CATEGORIES structure", t_categories),
        ("Config: media folder created", t_media_folder_created),
        ("Config: API_SECRET_KEY", t_api_secret_key),
        ("Config: site passwords", t_passwords_exist),
    ]:
        _run(name, fn)

# ═══════════════════════════════════════════════════════════════
# БЛОК 2 — Database (SQLite)
# ═══════════════════════════════════════════════════════════════

def _test_database():
    import sqlite3
    from database import Database, db, Memory, Wish, ScheduledEvent
    from app_version import version as app_version, description as app_version_description

    tmp = tempfile.mktemp(suffix=".db")
    test_db = Database(db_path=tmp)

    # ── Пользователи ──────────────────────────────────────────

    def t_add_user():
        ok = test_db.add_or_update_user(111, "testuser", "Test", "User")
        assert ok

    def t_get_user():
        u = test_db.get_user(111)
        assert u is not None
        assert u["user_id"] == 111
        assert u["username"] == "testuser"

    def t_get_missing_user():
        u = test_db.get_user(999999)
        assert u is None

    def t_user_update():
        test_db.add_or_update_user(111, "newname", "NewFirst")
        u = test_db.get_user(111)
        assert u["username"] == "newname"

    def t_get_all_user_ids():
        ids = test_db.get_all_user_ids()
        assert isinstance(ids, list)
        assert 111 in ids

    def t_is_admin_default():
        # creator и ksusha — всегда админы, остальные нет
        assert test_db.is_admin(test_db.get_creator_id())
        assert test_db.is_admin(test_db.get_ksusha_id())

    def t_not_admin():
        assert not test_db.is_admin(999999)

    # ── Настройки ─────────────────────────────────────────────

    def t_set_get_setting():
        test_db.set_setting("test_key", "hello")
        assert test_db.get_setting("test_key") == "hello"

    def t_delete_setting():
        test_db.set_setting("del_key", "bye")
        test_db.delete_setting("del_key")
        assert test_db.get_setting("del_key") is None

    def t_missing_setting():
        assert test_db.get_setting("nonexistent_999") is None

    def t_version_registry_bootstrap():
        with test_db._get_connection() as conn:
            row = conn.execute(
                "SELECT version, description, git_commit FROM version_history WHERE version = ?",
                (app_version,),
            ).fetchone()
            assert row is not None
            assert row["description"] == app_version_description
            # git_commit may be None in CI without git, but column must exist
            assert "git_commit" in row.keys()

    def t_version_registry_idempotent_restart():
        with test_db._get_connection() as conn:
            before = conn.execute(
                "SELECT COUNT(*) AS c FROM version_history WHERE version = ?",
                (app_version,),
            ).fetchone()["c"]
        test_db._init_database()
        with test_db._get_connection() as conn:
            after = conn.execute(
                "SELECT COUNT(*) AS c FROM version_history WHERE version = ?",
                (app_version,),
            ).fetchone()["c"]
            assert before == 1
            assert after == 1

    def t_version_registry_update_description():
        probe_version = "9.9.9-test"
        with test_db._get_connection() as conn:
            test_db._register_version_history_entry(conn, probe_version, "old description")
            test_db._register_version_history_entry(conn, probe_version, "new description")
            test_db._register_version_history_entry(conn, probe_version, "new description")
            row = conn.execute(
                "SELECT description FROM version_history WHERE version = ?",
                (probe_version,),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM version_history WHERE version = ?",
                (probe_version,),
            ).fetchone()["c"]
            assert row is not None
            assert row["description"] == "new description"
            assert count == 1

    def t_version_registry_git_commit():
        """Commit hash is stored on first insert."""
        probe_version = "9.9.9-commit-test"
        with test_db._get_connection() as conn:
            test_db._register_version_history_entry(
                conn, probe_version, "desc", git_commit="abc12345"
            )
            row = conn.execute(
                "SELECT git_commit FROM version_history WHERE version = ?",
                (probe_version,),
            ).fetchone()
            assert row is not None
            assert row["git_commit"] == "abc12345"

    def t_version_registry_update_commit():
        """Commit hash is updated independently of description."""
        probe_version = "9.9.9-upd-commit"
        with test_db._get_connection() as conn:
            test_db._register_version_history_entry(
                conn, probe_version, "stable desc", git_commit="aaaaaaaa"
            )
            test_db._register_version_history_entry(
                conn, probe_version, "stable desc", git_commit="bbbbbbbb"
            )
            row = conn.execute(
                "SELECT description, git_commit FROM version_history WHERE version = ?",
                (probe_version,),
            ).fetchone()
            assert row["description"] == "stable desc"
            assert row["git_commit"] == "bbbbbbbb"

    def t_version_registry_noop_same_commit():
        """No update issued when version, description, and commit are unchanged."""
        probe_version = "9.9.9-noop"
        with test_db._get_connection() as conn:
            test_db._register_version_history_entry(
                conn, probe_version, "same", git_commit="cccccccc"
            )
            # Call again with identical data — should be a pure no-op
            test_db._register_version_history_entry(
                conn, probe_version, "same", git_commit="cccccccc"
            )
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM version_history WHERE version = ?",
                (probe_version,),
            ).fetchone()["c"]
            assert count == 1

    def t_user_setting():
        test_db.set_user_setting(111, "timezone", "Europe/Moscow")
        assert test_db.get_user_setting(111, "timezone") == "Europe/Moscow"

    def t_user_all_settings():
        s = test_db.get_user_all_settings(111)
        assert isinstance(s, dict)
        assert "timezone" in s

    def t_notifications_enabled_default():
        assert test_db.are_notifications_enabled(111)

    def t_notifications_disable():
        test_db.set_user_setting(111, "notifications_enabled", "0")
        assert not test_db.are_notifications_enabled(111)
        test_db.set_user_setting(111, "notifications_enabled", "1")

    def t_favorites_enabled_default():
        assert test_db.is_favorites_enabled(111)

    # ── Воспоминания ──────────────────────────────────────────

    def t_add_memory():
        mid = test_db.add_memory(111, "memories", "Тест", "01.01.2026", "Описание")
        assert mid > 0

    def t_get_memory():
        mid = test_db.add_memory(111, "important_moments", "Момент", "02.02.2026", "Текст")
        m = test_db.get_memory(mid)
        assert m is not None
        assert m.title == "Момент"
        assert m.category == "important_moments"
        assert m.content == "Текст"

    def t_get_missing_memory():
        m = test_db.get_memory(999999)
        assert m is None

    def t_update_memory():
        mid = test_db.add_memory(111, "memories", "Старое", "01.01.2026", "Старое описание")
        ok = test_db.update_memory(mid, title="Новое", content="Новое описание")
        assert ok
        m = test_db.get_memory(mid)
        assert m.title == "Новое"
        assert m.content == "Новое описание"

    def t_delete_memory():
        mid = test_db.add_memory(111, "memories", "Удаляемое", "01.01.2026", "X")
        ok = test_db.delete_memory(mid)
        assert ok
        assert test_db.get_memory(mid) is None

    def t_memories_by_category():
        # Добавим несколько и проверим фильтрацию
        test_db.add_memory(111, "important_dates", "Дата1", "01.01.2026", "Д1")
        test_db.add_memory(111, "important_dates", "Дата2", "02.01.2026", "Д2")
        mems = test_db.get_memories_by_category("important_dates")
        assert len(mems) >= 2
        for m in mems:
            assert m.category == "important_dates"

    def t_user_memories():
        mems = test_db.get_user_memories(111)
        assert isinstance(mems, list)
        assert len(mems) > 0

    def t_search_memories():
        test_db.add_memory(111, "memories", "Уникальный запрос XXYYZZ", "01.01.2026", "Содержание")
        results = test_db.search_memories("XXYYZZ")
        assert len(results) >= 1

    def t_search_memories_fuzzy():
        results = test_db.search_memories_fuzzy("XXYYZZ")
        assert len(results) >= 1

    def t_recent_memories():
        mems = test_db.get_recent_memories(limit=5)
        assert isinstance(mems, list)
        assert len(mems) <= 5

    # ── Приватность воспоминаний ──────────────────────────────

    def t_privacy_limited():
        mid = test_db.add_memory(111, "memories", "Лимит", "01.01.2026", "X")
        ok = test_db.set_memory_privacy_limited(mid, 3)
        assert ok
        p = test_db.get_memory_privacy(mid)
        assert p["privacy_type"] == "limited_views"
        assert p["privacy_views_limit"] == 3

    def t_privacy_password():
        mid = test_db.add_memory(111, "memories", "Пароль", "01.01.2026", "X")
        ok = test_db.set_memory_privacy_password(mid, "Вопрос?", "ответ")
        assert ok
        p = test_db.get_memory_privacy(mid)
        assert p["privacy_type"] == "password"
        assert p["privacy_question"] == "Вопрос?"
        assert p["privacy_answer"] == "ответ"

    def t_privacy_clear():
        mid = test_db.add_memory(111, "memories", "СброшПрив", "01.01.2026", "X")
        test_db.set_memory_privacy_limited(mid, 5)
        ok = test_db.clear_memory_privacy(mid)
        assert ok
        p = test_db.get_memory_privacy(mid)
        assert p["privacy_type"] is None

    def t_register_memory_view():
        mid = test_db.add_memory(111, "memories", "ViewTest", "01.01.2026", "X")
        test_db.set_memory_privacy_limited(mid, 2)
        used, limit, allowed = test_db.register_memory_view(mid, 222)
        assert allowed
        assert used == 1
        assert limit == 2

    def t_register_memory_view_exhaust():
        mid = test_db.add_memory(111, "memories", "ExhaustView", "01.01.2026", "X")
        test_db.set_memory_privacy_limited(mid, 1)
        test_db.register_memory_view(mid, 333)
        used, limit, allowed = test_db.register_memory_view(mid, 333)
        assert not allowed

    # ── Желания ───────────────────────────────────────────────

    def t_add_wish():
        wid = test_db.add_wish(111, "Хочу на море")
        assert wid > 0

    def t_get_wish():
        wid = test_db.add_wish(111, "Хочу торт")
        w = test_db.get_wish(wid)
        assert w is not None
        assert "торт" in w.content

    def t_get_missing_wish():
        w = test_db.get_wish(999999)
        assert w is None

    def t_update_wish():
        wid = test_db.add_wish(111, "Старое желание")
        ok = test_db.update_wish(wid, "Новое желание")
        assert ok
        w = test_db.get_wish(wid)
        assert "Новое" in w.content

    def t_wish_status():
        wid = test_db.add_wish(111, 1, "СтатусТест")
        ok = test_db.update_wish_status(wid, "in_progress")
        assert ok
        w = test_db.get_wish(wid)
        assert w.status == "in_progress"

    def t_wish_status_done():
        wid = test_db.add_wish(111, 1, "Завершено")
        test_db.update_wish_status(wid, "done")
        w = test_db.get_wish(wid)
        assert w.status == "done"

    def t_wish_status_invalid():
        wid = test_db.add_wish(111, 1, "НевалидныйСтатус")
        ok = test_db.update_wish_status(wid, "invalid_status_xyz")
        assert not ok

    def t_delete_wish():
        wid = test_db.add_wish(111, 1, "Удалить")
        ok = test_db.delete_wish(wid)
        assert ok
        assert test_db.get_wish(wid) is None

    def t_user_wishes():
        ws = test_db.get_user_wishes(111)
        assert isinstance(ws, list)

    def t_wish_by_number():
        wid = test_db.add_wish(222, 1, "ПоНомеру")
        w = test_db.get_wish_by_user_and_number(222, 1)
        assert w is not None
        assert "ПоНомеру" in w.content

    # ── События на дату ────────────────────────────────────────

    def t_add_scheduled_event():
        eid = test_db.add_scheduled_event(111, "День рождения", "Отпраздновать", "2026-06-15 12:00:00")
        assert eid > 0

    def t_get_scheduled_event():
        eid = test_db.add_scheduled_event(111, "Встреча", "Не опоздать", "2026-07-20 10:00:00")
        e = test_db.get_scheduled_event(eid)
        assert e is not None
        assert e.title == "Встреча"

    def t_get_missing_event():
        e = test_db.get_scheduled_event(999999)
        assert e is None

    def t_update_scheduled_event():
        eid = test_db.add_scheduled_event(111, "СтароеСобытие", "", "2026-08-01 00:00:00")
        ok = test_db.update_scheduled_event(eid, title="НовоеСобытие")
        assert ok
        e = test_db.get_scheduled_event(eid)
        assert e.title == "НовоеСобытие"

    def t_delete_scheduled_event():
        eid = test_db.add_scheduled_event(111, "УдалитьСобытие", "", "2026-09-01 00:00:00")
        ok = test_db.delete_scheduled_event(eid)
        assert ok
        assert test_db.get_scheduled_event(eid) is None

    def t_get_scheduled_events():
        with test_db._get_connection() as conn:
            conn.execute('DELETE FROM scheduled_events')
        test_db.add_scheduled_event(111, "Far Future", "", "2027-01-01 12:00:00")
        test_db.add_scheduled_event(111, "Near Future", "", "2026-06-01 12:00:00")
        test_db.add_scheduled_event(111, "Past Event", "", "2026-05-01 12:00:00")
        evs = test_db.get_scheduled_events(limit=100)
        assert isinstance(evs, list)
        assert len(evs) == 3
        assert evs[0].title == "Near Future"
        assert evs[1].title == "Far Future"
        assert evs[2].title == "Past Event"

    def t_scheduled_events_count():
        cnt = test_db.get_scheduled_events_count()
        assert isinstance(cnt, int)
        assert cnt >= 0

    def t_scheduled_events_paged():
        evs, total = test_db.get_scheduled_events_paged(page=1, per_page=5)
        assert isinstance(evs, list)
        assert isinstance(total, int)

    def t_search_scheduled_events():
        test_db.add_scheduled_event(111, "УникПоиск_XYZ", "", "2026-10-01 00:00:00")
        evs, total = test_db.search_scheduled_events("УникПоиск_XYZ")
        assert total >= 1

    def t_mark_notified():
        eid = test_db.add_scheduled_event(111, "Уведомить", "", "2025-01-01 00:00:00")
        ok = test_db.mark_scheduled_event_notified_for(eid, "creator")
        assert ok
        assert test_db.is_scheduled_event_notified_for(eid, "creator")
        assert not test_db.is_scheduled_event_notified_for(eid, "ksusha")

    # ── Избранное ─────────────────────────────────────────────

    def t_add_favorite():
        mid = test_db.add_memory(111, "memories", "ИзбрТест", "01.01.2026", "X")
        ok = test_db.add_favorite(111, "memory", mid)
        assert ok

    def t_is_favorite():
        mid = test_db.add_memory(111, "memories", "ИзбрПроверка", "01.01.2026", "X")
        test_db.add_favorite(111, "memory", mid)
        assert test_db.is_in_favorites(111, "memory", mid)

    def t_remove_favorite():
        mid = test_db.add_memory(111, "memories", "ИзбрУдалить", "01.01.2026", "X")
        test_db.add_favorite(111, "memory", mid)
        test_db.remove_favorite(111, "memory", mid)
        assert not test_db.is_in_favorites(111, "memory", mid)

    def t_user_favorites_raw():
        favs = test_db.get_user_favorites_raw(111)
        assert isinstance(favs, list)

    def t_favorites_paged():
        items, total = test_db.get_user_favorites_paged(111, page=1, per_page=10)
        assert isinstance(items, list)
        assert isinstance(total, int)

    # ── Статистика ────────────────────────────────────────────

    def t_category_stats():
        s = test_db.get_category_stats()
        assert isinstance(s, dict)

    def t_media_stats():
        s = test_db.get_media_stats()
        assert "photo_count" in s
        assert "video_count" in s

    def t_days_active():
        d = test_db.get_days_active()
        assert isinstance(d, int)
        assert d >= 0

    def t_get_total_stats():
        s = test_db.get_total_stats()
        assert "total_memories" in s
        assert "scheduled_events_count" in s
        assert "wishes_count" in s

    def t_user_stats():
        s = test_db.get_user_stats(111)
        assert "total_memories" in s
        assert "wishes_count" in s

    # ── Визиты и устройства ───────────────────────────────────

    def t_add_site_visit():
        test_db.add_site_visit("Europe/Moscow", "127.0.0.1", "TestBrowser", "testvisitor")

    def t_site_visits_summary():
        s = test_db.get_site_visits_summary("testvisitor")
        assert s["total"] >= 1

    def t_get_last_visit_info():
        info = test_db.get_last_site_visit_info("testvisitor")
        assert info is not None

    def t_add_visitor_site_time():
        test_db.add_visitor_site_time("testvisitor", 10)

    def t_get_visitor_total_seconds():
        sec = test_db.get_visitor_total_site_seconds("testvisitor")
        assert sec >= 10

    def t_add_or_update_device():
        test_db.add_or_update_device("testvisitor", ua_pretty="Chrome Test", role="creator")

    def t_get_device_by_visitor():
        dev = test_db.get_device_by_visitor_id("testvisitor")
        assert dev is not None
        assert dev["role"] == "creator"

    def t_get_devices_list():
        devs = test_db.get_devices_list()
        assert isinstance(devs, list)

    def t_delete_device():
        test_db.add_or_update_device("deleteme", ua_pretty="DelDevice")
        devs = test_db.get_devices_list()
        found = next((d for d in devs if d.get("visitor_id") == "deleteme"), None)
        assert found is not None
        ok = test_db.delete_device_by_id(found["id"])
        assert ok

    def t_category_open():
        test_db.add_category_open("testvisitor", "memories")

    def t_category_opens():
        opens = test_db.get_category_opens("testvisitor")
        assert opens["total"] >= 1
        assert "memories" in opens["by_section"]

    def t_streak_days():
        s = test_db.get_streak_days("testvisitor")
        assert isinstance(s, int)

    def t_visits_per_day():
        d = test_db.get_visits_per_day_for_month(
            datetime.now().year, datetime.now().month, "testvisitor"
        )
        assert isinstance(d, dict)

    def t_time_slots():
        slots = test_db.get_visits_for_time_slots("testvisitor")
        assert isinstance(slots, list)

    # ── ИИ-компаньон история ──────────────────────────────────

    def t_add_companion_message():
        mid = test_db.add_companion_message("testvisitor", "user", "creator", "Привет!")
        assert mid is not None and mid > 0

    def t_get_companion_history():
        test_db.add_companion_message("testvisitor", "assistant", "creator", "Привет пользователь!")
        hist = test_db.get_companion_history("testvisitor")
        assert isinstance(hist, list)
        assert len(hist) >= 2

    def t_clear_companion_history():
        test_db.add_companion_message("clearme", "user", "creator", "Удалить!")
        n = test_db.clear_companion_history("clearme")
        assert n >= 1
        hist = test_db.get_companion_history("clearme")
        assert len(hist) == 0

    # ── Уведомления ───────────────────────────────────────────

    def t_add_site_notification():
        nid = test_db.add_site_notification("Привет Ксюша!")
        assert nid > 0

    def t_get_pending_notification():
        test_db.add_site_notification("Ещё одно!")
        n = test_db.get_pending_site_notification()
        assert n is not None

    def t_mark_notification_delivered():
        nid = test_db.add_site_notification("Доставить!")
        test_db.mark_notification_delivered(nid)

    # ── Bot активность ────────────────────────────────────────

    def t_update_bot_last_active():
        test_db.update_bot_last_active(111, "тест")

    def t_get_bot_last_active():
        r = test_db.get_bot_last_active(111)
        assert r is not None
        assert r["action"] == "тест"

    # ── Анимации ──────────────────────────────────────────────

    def t_add_celebration():
        cid = test_db.add_celebration("test", "Тест анимации")
        assert cid > 0

    def t_get_pending_celebration():
        test_db.add_celebration("test2", "Анимация2")
        c = test_db.get_pending_celebration("newvisitor")
        assert c is not None

    def t_mark_celebration_delivered():
        cid = test_db.add_celebration("test3", "Анимация3")
        test_db.mark_celebration_delivered(cid, "somevisitor")

    def t_get_last_celebration_date():
        test_db.add_celebration("anniversary_2026", "Годовщина")
        d = test_db.get_last_celebration_date("anniversary_2026")
        assert d is not None

    # ── Сессии просмотра ──────────────────────────────────────

    def t_view_session():
        mid = test_db.add_memory(111, "memories", "СессияПросм", "01.01.2026", "X")
        sid = test_db.add_memory_view_session_start(mid, "testvisitor")
        assert sid > 0
        test_db.finish_memory_view_session(sid)

    def t_memory_view_stats():
        s = test_db.get_memory_view_stats("testvisitor")
        assert isinstance(s, dict)
        assert "total_duration_sec" in s

    def t_photos_opened():
        n = test_db.get_photos_opened_count("testvisitor")
        assert isinstance(n, int)

    # ── Экспорт / импорт ─────────────────────────────────────

    def t_get_export_data():
        d = test_db.get_export_data()
        assert "memories" in d
        assert "scheduled_events" in d
        assert "wishes" in d
        assert "version" in d

    def t_import_from_export():
        data = test_db.get_export_data()
        m, e, w = test_db.import_from_export(data)
        assert isinstance(m, int)
        assert isinstance(e, int)
        assert isinstance(w, int)

    # ── Бэкап ─────────────────────────────────────────────────

    def t_backup_database():
        backup_path = tmp + ".backup"
        ok = test_db.backup_database(backup_path)
        assert ok
        assert os.path.exists(backup_path)
        os.remove(backup_path)

    # ── Специальные пользователи ──────────────────────────────

    def t_get_creator_id():
        cid = test_db.get_creator_id()
        assert isinstance(cid, int)

    def t_get_ksusha_id():
        kid = test_db.get_ksusha_id()
        assert isinstance(kid, int)

    def t_set_creator_id_via_settings():
        test_db.set_setting("creator_id", "12345")
        assert test_db.get_creator_id() == 12345
        test_db.delete_setting("creator_id")

    def t_set_ksusha_id_via_settings():
        test_db.set_setting("ksusha_id", "54321")
        assert test_db.get_ksusha_id() == 54321
        test_db.delete_setting("ksusha_id")

    def t_is_creator():
        from config import config as _cfg
        assert test_db.is_creator(_cfg.CREATOR_ID)
        assert not test_db.is_creator(999999)

    # ── Запускаем все DB тесты ─────────────────────────────────
    db_tests = [
        ("DB: add_user", t_add_user),
        ("DB: get_user", t_get_user),
        ("DB: get missing user", t_get_missing_user),
        ("DB: update user", t_user_update),
        ("DB: get_all_user_ids", t_get_all_user_ids),
        ("DB: is_admin default", t_is_admin_default),
        ("DB: not admin", t_not_admin),
        ("DB: set/get setting", t_set_get_setting),
        ("DB: delete setting", t_delete_setting),
        ("DB: missing setting", t_missing_setting),
        ("DB: version registry bootstrap", t_version_registry_bootstrap),
        ("DB: version registry idempotent restart", t_version_registry_idempotent_restart),
        ("DB: version registry update description", t_version_registry_update_description),
        ("DB: version registry git commit stored", t_version_registry_git_commit),
        ("DB: version registry update commit", t_version_registry_update_commit),
        ("DB: version registry noop same commit", t_version_registry_noop_same_commit),
        ("DB: user setting", t_user_setting),
        ("DB: all user settings", t_user_all_settings),
        ("DB: notifications default", t_notifications_enabled_default),
        ("DB: disable notifications", t_notifications_disable),
        ("DB: favorites default", t_favorites_enabled_default),
        ("DB: add memory", t_add_memory),
        ("DB: get memory", t_get_memory),
        ("DB: get missing memory", t_get_missing_memory),
        ("DB: update memory", t_update_memory),
        ("DB: delete memory", t_delete_memory),
        ("DB: memories by category", t_memories_by_category),
        ("DB: user memories", t_user_memories),
        ("DB: search memories exact", t_search_memories),
        ("DB: search memories fuzzy", t_search_memories_fuzzy),
        ("DB: recent memories", t_recent_memories),
        ("DB: privacy limited", t_privacy_limited),
        ("DB: privacy password", t_privacy_password),
        ("DB: clear privacy", t_privacy_clear),
        ("DB: register view", t_register_memory_view),
        ("DB: register view exhaust", t_register_memory_view_exhaust),
        ("DB: add wish", t_add_wish),
        ("DB: get wish", t_get_wish),
        ("DB: get missing wish", t_get_missing_wish),
        ("DB: update wish", t_update_wish),
        ("DB: wish status in_progress", t_wish_status),
        ("DB: wish status done", t_wish_status_done),
        ("DB: wish status invalid", t_wish_status_invalid),
        ("DB: delete wish", t_delete_wish),
        ("DB: user wishes", t_user_wishes),
        ("DB: wish by number", t_wish_by_number),
        ("DB: add scheduled event", t_add_scheduled_event),
        ("DB: get scheduled event", t_get_scheduled_event),
        ("DB: get missing event", t_get_missing_event),
        ("DB: update scheduled event", t_update_scheduled_event),
        ("DB: delete scheduled event", t_delete_scheduled_event),
        ("DB: get all events", t_get_scheduled_events),
        ("DB: events count", t_scheduled_events_count),
        ("DB: events paged", t_scheduled_events_paged),
        ("DB: search events", t_search_scheduled_events),
        ("DB: mark event notified", t_mark_notified),
        ("DB: add favorite", t_add_favorite),
        ("DB: is favorite", t_is_favorite),
        ("DB: remove favorite", t_remove_favorite),
        ("DB: favorites raw", t_user_favorites_raw),
        ("DB: favorites paged", t_favorites_paged),
        ("DB: category stats", t_category_stats),
        ("DB: media stats", t_media_stats),
        ("DB: days active", t_days_active),
        ("DB: total stats", t_get_total_stats),
        ("DB: user stats", t_user_stats),
        ("DB: add site visit", t_add_site_visit),
        ("DB: site visits summary", t_site_visits_summary),
        ("DB: last visit info", t_get_last_visit_info),
        ("DB: visitor site time", t_add_visitor_site_time),
        ("DB: visitor total seconds", t_get_visitor_total_seconds),
        ("DB: add/update device", t_add_or_update_device),
        ("DB: get device by visitor", t_get_device_by_visitor),
        ("DB: get devices list", t_get_devices_list),
        ("DB: delete device", t_delete_device),
        ("DB: category open", t_category_open),
        ("DB: category opens stats", t_category_opens),
        ("DB: streak days", t_streak_days),
        ("DB: visits per day", t_visits_per_day),
        ("DB: time slots", t_time_slots),
        ("DB: add companion message", t_add_companion_message),
        ("DB: get companion history", t_get_companion_history),
        ("DB: clear companion history", t_clear_companion_history),
        ("DB: add site notification", t_add_site_notification),
        ("DB: get pending notification", t_get_pending_notification),
        ("DB: mark notification delivered", t_mark_notification_delivered),
        ("DB: bot last active", t_update_bot_last_active),
        ("DB: get bot last active", t_get_bot_last_active),
        ("DB: add celebration", t_add_celebration),
        ("DB: get pending celebration", t_get_pending_celebration),
        ("DB: mark celebration delivered", t_mark_celebration_delivered),
        ("DB: last celebration date", t_get_last_celebration_date),
        ("DB: view session", t_view_session),
        ("DB: memory view stats", t_memory_view_stats),
        ("DB: photos opened count", t_photos_opened),
        ("DB: export data", t_get_export_data),
        ("DB: import from export", t_import_from_export),
        ("DB: backup database", t_backup_database),
        ("DB: get_creator_id", t_get_creator_id),
        ("DB: get_ksusha_id", t_get_ksusha_id),
        ("DB: override creator_id via settings", t_set_creator_id_via_settings),
        ("DB: override ksusha_id via settings", t_set_ksusha_id_via_settings),
        ("DB: is_creator", t_is_creator),
    ]

    for name, fn in db_tests:
        _run(name, fn)

    # Чистим временный файл БД
    try:
        os.remove(tmp)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# БЛОК 3 — Utils
# ═══════════════════════════════════════════════════════════════

def _test_utils():
    from utils import (
        substitute_params, format_datetime_russian, format_datetime_for_user,
        parse_ai_date_to_db, format_scheduled_event_datetime,
        is_scheduled_event_moment_passed, is_scheduled_event_expired,
        format_time_remaining, sanitize_html_for_telegram,
        validate_date, validate_content,
        preserve_formatting, _parse_user_agent, is_wishes_available,
        format_scheduled_event_datetime_for_timezone,
    )
    from api import (
        _parse_date_local_ru,
        _coerce_non_future_ai_date,
        _extract_today_from_context,
    )

    # ── substitute_params ────────────────────────────────────

    def t_params_days_together():
        result = substitute_params("Нас {days_together} дней!")
        assert "{days_together}" not in result
        assert "дней!" in result

    def t_params_day_of_week():
        result = substitute_params("{day_of_week}")
        days = ["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"]
        assert any(d in result for d in days)

    def t_params_season():
        result = substitute_params("{season}")
        seasons = ["зима","весна","лето","осень"]
        assert any(s in result for s in seasons)

    def t_params_month_name():
        result = substitute_params("{month_name}")
        months = ["январь","февраль","март","апрель","май","июнь",
                  "июль","август","сентябрь","октябрь","ноябрь","декабрь"]
        assert any(m in result for m in months)

    def t_params_day_of_month():
        result = substitute_params("{day_of_month}")
        assert result.isdigit()
        assert 1 <= int(result) <= 31

    def t_params_no_change_without_braces():
        result = substitute_params("Просто текст без параметров")
        assert result == "Просто текст без параметров"

    def t_params_empty():
        result = substitute_params("")
        assert result == ""

    def t_params_none():
        result = substitute_params(None)
        assert result is None

    def t_params_time_together():
        result = substitute_params("{time_together}")
        assert "{time_together}" not in result

    def t_params_anniversary():
        result = substitute_params("{days_until_anniversary}")
        assert "{days_until_anniversary}" not in result

    # ── format_datetime_russian ───────────────────────────────

    def t_format_dt_standard():
        result = format_datetime_russian("2026-03-08 12:30:00")
        assert "2026" in result
        assert "12:30" in result

    def t_format_dt_date_only():
        result = format_datetime_russian("2026-03-08")
        assert "2026" in result

    def t_format_dt_iso():
        result = format_datetime_russian("2026-01-01 00:00:00")
        assert "2026" in result

    # ── format_datetime_for_user ─────────────────────────────

    def t_format_for_user_moscow():
        result = format_datetime_for_user("2026-03-08 09:00:00", "Europe/Moscow")
        assert "2026" in result

    def t_format_for_user_bishkek():
        result = format_datetime_for_user("2026-03-08 09:00:00", "Asia/Bishkek")
        assert "2026" in result

    def t_format_for_user_none_tz():
        result = format_datetime_for_user("2026-03-08 09:00:00", None)
        assert "2026" in result

    def t_format_for_user_empty():
        result = format_datetime_for_user("", None)
        assert result == ""

    # ── parse_ai_date_to_db ───────────────────────────────────

    def t_parse_ai_date_simple():
        result = parse_ai_date_to_db("25.1.2026")
        assert result is not None
        assert "2026-01-25" in result

    def t_parse_ai_date_with_time():
        result = parse_ai_date_to_db("25.1.2026 14:30")
        assert result is not None
        assert "2026-01-25" in result
        assert "14:30" in result

    def t_parse_ai_date_iso():
        result = parse_ai_date_to_db("2026-06-15")
        assert result is not None
        assert "2026-06-15" in result

    def t_parse_ai_date_short_year():
        result = parse_ai_date_to_db("25.1.26")
        assert result is not None
        assert "2026" in result

    def t_parse_ai_date_invalid():
        result = parse_ai_date_to_db("не дата вообще!")
        assert result is None

    def t_parse_ai_date_empty():
        result = parse_ai_date_to_db("")
        assert result is None

    # ── local ru date parsing (onboarding) ───────────────────

    def t_local_date_relative_today():
        base = date(2026, 5, 25)
        assert _parse_date_local_ru("сегодня", today=base) == "25.5.2026"
        assert _parse_date_local_ru("вчера", today=base) == "24.5.2026"

    def t_local_date_without_year_numeric():
        # 30.10 при "сегодня" 25.05 -> прошлый год
        base = date(2026, 5, 25)
        assert _parse_date_local_ru("30.10", today=base) == "30.10.2025"

    def t_local_date_without_year_text():
        base = date(2026, 11, 1)
        assert _parse_date_local_ru("30 октября", today=base) == "30.10.2026"

    def t_local_date_month_typo():
        base = date(2026, 12, 1)
        assert _parse_date_local_ru("30 октябя", today=base) == "30.10.2026"
        assert _parse_date_local_ru("5 сентебря 2024", today=base) == "5.9.2024"

    def t_extract_today_from_context():
        ctx = "Сейчас у пользователя: 25 мая 2026, понедельник, 13:00 (час 13, минута 0). Месяц: май, год: 2026."
        assert _extract_today_from_context(ctx) == date(2026, 5, 25)

    def t_coerce_non_future_ai_date_respects_context_today():
        # Для пользователя "сегодня" = 25.05.2026 дата 26.05.2026 должна считаться будущей
        today_ctx = date(2026, 5, 25)
        assert _coerce_non_future_ai_date("26.5.2026", today=today_ctx) == ""
        assert _coerce_non_future_ai_date("25.5.2026 09:30", today=today_ctx) == "25.5.2026 09:30"

    # ── is_scheduled_event_moment_passed ─────────────────────

    def t_event_passed():
        past = "2020-01-01 00:00:00"
        assert is_scheduled_event_moment_passed(past, None)

    def t_event_future():
        future = "2099-01-01 00:00:00"
        assert not is_scheduled_event_moment_passed(future, None)

    def t_event_empty():
        assert not is_scheduled_event_moment_passed("", None)

    # ── format_time_remaining ─────────────────────────────────

    def t_time_remaining_past():
        result = format_time_remaining("2020-01-01 00:00:00", None)
        assert result == "—" or result == "0"

    def t_time_remaining_future():
        future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        result = format_time_remaining(future, None)
        assert "д" in result or "ч" in result

    def t_time_remaining_empty():
        result = format_time_remaining("", None)
        assert result == "—"

    # ── sanitize_html_for_telegram ────────────────────────────

    def t_sanitize_plain():
        result = sanitize_html_for_telegram("Привет мир")
        assert result == "Привет мир"

    def t_sanitize_bold():
        result = sanitize_html_for_telegram("<b>жирный</b>")
        assert "<b>жирный</b>" in result

    def t_sanitize_italic():
        result = sanitize_html_for_telegram("<i>курсив</i>")
        assert "<i>курсив</i>" in result

    def t_sanitize_removes_script():
        result = sanitize_html_for_telegram("текст <script>alert(1)</script> конец")
        assert "<script>" not in result

    def t_sanitize_ampersand():
        result = sanitize_html_for_telegram("a & b")
        assert "&amp;" in result

    def t_sanitize_link():
        result = sanitize_html_for_telegram('<a href="https://example.com">ссылка</a>')
        assert '<a href="https://example.com">ссылка</a>' in result

    def t_sanitize_unclosed_tag():
        result = sanitize_html_for_telegram("<b>незакрытый")
        assert "</b>" in result

    def t_sanitize_empty():
        result = sanitize_html_for_telegram("")
        assert result == ""

    def t_sanitize_code():
        result = sanitize_html_for_telegram("<code>x = 1</code>")
        assert "<code>" in result

    # ── text_and_entities_to_html ─────────────────────────────

    def t_entities_plain():
        result = text_and_entities_to_html("Привет", [])
        assert "Привет" in result

    def t_entities_empty():
        result = text_and_entities_to_html("", [])
        assert result == ""

    # ── truncate_text ─────────────────────────────────────────

    def t_truncate_short():
        assert truncate_text("Короткий", 20) == "Короткий"

    def t_truncate_long():
        long = "A" * 200
        result = truncate_text(long, 50)
        assert len(result) <= 50
        assert result.endswith("...")

    def t_truncate_exact():
        text = "A" * 50
        assert truncate_text(text, 50) == text

    def t_truncate_empty():
        assert truncate_text("", 10) == ""

    # ── validate_title ────────────────────────────────────────

    def t_validate_title_ok():
        ok, err = validate_title("Нормальное название")
        assert ok

    def t_validate_title_empty():
        ok, err = validate_title("")
        assert not ok

    def t_validate_title_short():
        ok, err = validate_title("А")
        assert not ok

    def t_validate_title_too_long():
        ok, err = validate_title("А" * 200)
        assert not ok

    # ── validate_date ─────────────────────────────────────────

    def t_validate_date_ok():
        ok, err = validate_date("01 января 2026")
        assert ok

    def t_validate_date_empty():
        ok, err = validate_date("")
        assert not ok

    def t_validate_date_too_long():
        ok, err = validate_date("А" * 60)
        assert not ok

    # ── validate_content ──────────────────────────────────────

    def t_validate_content_ok():
        ok, err = validate_content("Нормальное описание")
        assert ok

    def t_validate_content_too_long():
        ok, err = validate_content("А" * 5000)
        assert not ok

    def t_validate_content_empty():
        ok, err = validate_content("")
        assert ok  # пустое описание допустимо


    # ── preserve_formatting ───────────────────────────────────

    def t_preserve_formatting_bold():
        result = preserve_formatting("<b>жирный</b>")
        assert "<b>жирный</b>" in result

    def t_preserve_formatting_empty():
        assert preserve_formatting("") == ""

    def t_preserve_formatting_none():
        assert preserve_formatting(None) == ""

    # ── _parse_user_agent ─────────────────────────────────────

    def t_ua_android():
        ua = "Mozilla/5.0 (Linux; Android 13; SM-G991B Build/TP1A) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36"
        result = _parse_user_agent(ua)
        assert "Android" in result or "Chrome" in result

    def t_ua_iphone():
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
        result = _parse_user_agent(ua)
        assert "iOS" in result or "iPhone" in result

    def t_ua_windows():
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"
        result = _parse_user_agent(ua)
        assert "Windows" in result

    def t_ua_empty():
        result = _parse_user_agent("")
        assert isinstance(result, str)

    # ── is_wishes_available ───────────────────────────────────

    def t_wishes_available_type():
        result = is_wishes_available()
        assert isinstance(result, bool)

    utils_tests = [
        ("Utils: params days_together", t_params_days_together),
        ("Utils: params day_of_week", t_params_day_of_week),
        ("Utils: params season", t_params_season),
        ("Utils: params month_name", t_params_month_name),
        ("Utils: params day_of_month", t_params_day_of_month),
        ("Utils: params no change", t_params_no_change_without_braces),
        ("Utils: params empty", t_params_empty),
        ("Utils: params None", t_params_none),
        ("Utils: params time_together", t_params_time_together),
        ("Utils: params anniversary", t_params_anniversary),
        ("Utils: format_datetime_russian", t_format_dt_standard),
        ("Utils: format_datetime date-only", t_format_dt_date_only),
        ("Utils: format_datetime ISO", t_format_dt_iso),
        ("Utils: format_for_user Moscow", t_format_for_user_moscow),
        ("Utils: format_for_user Bishkek", t_format_for_user_bishkek),
        ("Utils: format_for_user no tz", t_format_for_user_none_tz),
        ("Utils: format_for_user empty", t_format_for_user_empty),
        ("Utils: parse_ai_date simple", t_parse_ai_date_simple),
        ("Utils: parse_ai_date with time", t_parse_ai_date_with_time),
        ("Utils: parse_ai_date ISO", t_parse_ai_date_iso),
        ("Utils: parse_ai_date short year", t_parse_ai_date_short_year),
        ("Utils: parse_ai_date invalid", t_parse_ai_date_invalid),
        ("Utils: parse_ai_date empty", t_parse_ai_date_empty),
        ("Utils: local_date relative today", t_local_date_relative_today),
        ("Utils: local_date without year numeric", t_local_date_without_year_numeric),
        ("Utils: local_date without year text", t_local_date_without_year_text),
        ("Utils: local_date month typo", t_local_date_month_typo),
        ("Utils: extract today from context", t_extract_today_from_context),
        ("Utils: coerce non future by context date", t_coerce_non_future_ai_date_respects_context_today),
        ("Utils: event passed", t_event_passed),
        ("Utils: event future", t_event_future),
        ("Utils: event empty", t_event_empty),
        ("Utils: time remaining past", t_time_remaining_past),
        ("Utils: time remaining future", t_time_remaining_future),
        ("Utils: time remaining empty", t_time_remaining_empty),
        ("Utils: sanitize plain", t_sanitize_plain),
        ("Utils: sanitize bold", t_sanitize_bold),
        ("Utils: sanitize italic", t_sanitize_italic),
        ("Utils: sanitize removes script", t_sanitize_removes_script),
        ("Utils: sanitize ampersand", t_sanitize_ampersand),
        ("Utils: sanitize link", t_sanitize_link),
        ("Utils: sanitize unclosed tag", t_sanitize_unclosed_tag),
        ("Utils: sanitize empty", t_sanitize_empty),
        ("Utils: sanitize code", t_sanitize_code),
        ("Utils: entities plain", t_entities_plain),
        ("Utils: entities empty", t_entities_empty),
        ("Utils: truncate short", t_truncate_short),
        ("Utils: truncate long", t_truncate_long),
        ("Utils: truncate exact", t_truncate_exact),
        ("Utils: truncate empty", t_truncate_empty),
        ("Utils: validate_title ok", t_validate_title_ok),
        ("Utils: validate_title empty", t_validate_title_empty),
        ("Utils: validate_title short", t_validate_title_short),
        ("Utils: validate_title long", t_validate_title_too_long),
        ("Utils: validate_date ok", t_validate_date_ok),
        ("Utils: validate_date empty", t_validate_date_empty),
        ("Utils: validate_date long", t_validate_date_too_long),
        ("Utils: validate_content ok", t_validate_content_ok),
        ("Utils: validate_content long", t_validate_content_too_long),
        ("Utils: validate_content empty", t_validate_content_empty),
        ("Utils: wish_num 1", t_wish_num_1),
        ("Utils: wish_num 2", t_wish_num_2),
        ("Utils: wish_num 3", t_wish_num_3),
        ("Utils: wish_num other", t_wish_num_other),
        ("Utils: preserve_formatting bold", t_preserve_formatting_bold),
        ("Utils: preserve_formatting empty", t_preserve_formatting_empty),
        ("Utils: preserve_formatting None", t_preserve_formatting_none),
        ("Utils: parse UA android", t_ua_android),
        ("Utils: parse UA iphone", t_ua_iphone),
        ("Utils: parse UA windows", t_ua_windows),
        ("Utils: parse UA empty", t_ua_empty),
        ("Utils: is_wishes_available type", t_wishes_available_type),
    ]
    for name, fn in utils_tests:
        _run(name, fn)

# ═══════════════════════════════════════════════════════════════
# БЛОК 4 — HTTP API (без сети, только логика функций)
# ═══════════════════════════════════════════════════════════════

def _test_http_api():
    from http_api import (
        _format_ago_ru, _parse_user_agent, _is_private_ip,
        _memory_to_public_dict, _wish_to_public_dict,
        _event_to_public_dict, _collect_site_data,
    )
    import tempfile
    from database import Database

    tmp = tempfile.mktemp(suffix=".db")
    test_db = Database(db_path=tmp)

    def t_format_ago_now():
        utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        result = _format_ago_ru(utc)
        assert "только что" in result or "мин" in result

    def t_format_ago_hour():
        dt = datetime.now(timezone.utc) - timedelta(hours=2)
        result = _format_ago_ru(dt.strftime("%Y-%m-%d %H:%M:%S"))
        assert "ч назад" in result

    def t_format_ago_day():
        dt = datetime.now(timezone.utc) - timedelta(days=3)
        result = _format_ago_ru(dt.strftime("%Y-%m-%d %H:%M:%S"))
        assert "дн." in result or "нед." in result

    def t_format_ago_empty():
        result = _format_ago_ru("")
        assert result == ""

    def t_private_ip_local():
        assert _is_private_ip("192.168.1.1")
        assert _is_private_ip("10.0.0.1")
        assert _is_private_ip("127.0.0.1")

    def t_private_ip_public():
        assert not _is_private_ip("8.8.8.8")
        assert not _is_private_ip("185.218.137.132")

    def t_private_ip_empty():
        assert not _is_private_ip("")

    def t_collect_site_data_structure():
        data = _collect_site_data(None)
        assert "memories" in data
        assert "events" in data
        assert "wishes" in data
        assert "stats" in data
        assert "is_open" in data

    def t_collect_site_data_tz():
        data = _collect_site_data("Europe/Moscow")
        assert isinstance(data["memories"], list)

    def t_collect_site_data_users():
        data = _collect_site_data(None)
        assert "users" in data
        assert "creator" in data["users"]
        assert "ksyusha" in data["users"]

    def t_collect_site_data_favorites():
        data = _collect_site_data(None)
        assert "favorites" in data

    def t_collect_site_data_settings():
        data = _collect_site_data(None)
        assert "user_settings" in data

    http_tests = [
        ("HTTP: format_ago now", t_format_ago_now),
        ("HTTP: format_ago 2h", t_format_ago_hour),
        ("HTTP: format_ago 3d", t_format_ago_day),
        ("HTTP: format_ago empty", t_format_ago_empty),
        ("HTTP: private IP local", t_private_ip_local),
        ("HTTP: private IP public", t_private_ip_public),
        ("HTTP: private IP empty", t_private_ip_empty),
        ("HTTP: collect_site_data structure", t_collect_site_data_structure),
        ("HTTP: collect_site_data with tz", t_collect_site_data_tz),
        ("HTTP: collect_site_data users", t_collect_site_data_users),
        ("HTTP: collect_site_data favorites", t_collect_site_data_favorites),
        ("HTTP: collect_site_data settings", t_collect_site_data_settings),
    ]
    for name, fn in http_tests:
        _run(name, fn)

    try:
        os.remove(tmp)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# БЛОК 5 — Constants
# ═══════════════════════════════════════════════════════════════

def _test_constants():
    from constants import (
        PER_PAGE_DEFAULT, PER_PAGE_FAVORITES, PER_PAGE_EVENTS,
        PER_PAGE_MEMORIES, LIMIT_RECENT_MEMORIES, LIMIT_SEARCH_MEMORIES,
        LIMIT_SEARCH_FUZZY, TITLE_PREVIEW_LENGTH, WISH_CONTENT_PREVIEW_LENGTH,
        SEARCH_QUERY_MAX_LENGTH, CREATOR_TG_LINK, CREATOR_BUTTON_TEXT,
        MSG_ACCESS_DENIED, MSG_NOT_FOUND, MSG_MEMORY_NOT_FOUND,
        MSG_EVENT_NOT_FOUND, MSG_WISH_NOT_FOUND, MSG_ERROR,
    )

    def t_pagination_positive():
        assert PER_PAGE_DEFAULT > 0
        assert PER_PAGE_FAVORITES > 0
        assert PER_PAGE_EVENTS > 0
        assert PER_PAGE_MEMORIES > 0

    def t_limits_positive():
        assert LIMIT_RECENT_MEMORIES > 0
        assert LIMIT_SEARCH_MEMORIES > 0
        assert LIMIT_SEARCH_FUZZY > 0

    def t_preview_lengths():
        assert TITLE_PREVIEW_LENGTH > 0
        assert WISH_CONTENT_PREVIEW_LENGTH > 0
        assert SEARCH_QUERY_MAX_LENGTH > 0

    def t_tg_link():
        assert "t.me" in CREATOR_TG_LINK

    def t_messages_not_empty():
        assert MSG_ACCESS_DENIED
        assert MSG_NOT_FOUND
        assert MSG_MEMORY_NOT_FOUND
        assert MSG_EVENT_NOT_FOUND
        assert MSG_WISH_NOT_FOUND
        assert MSG_ERROR

    for name, fn in [
        ("Constants: pagination positive", t_pagination_positive),
        ("Constants: limits positive", t_limits_positive),
        ("Constants: preview lengths", t_preview_lengths),
        ("Constants: TG link", t_tg_link),
        ("Constants: messages not empty", t_messages_not_empty),
    ]:
        _run(name, fn)

# ═══════════════════════════════════════════════════════════════
# БЛОК 6 — Файловая система и структура проекта
# ═══════════════════════════════════════════════════════════════

def _test_filesystem():
    project_root = Path(__file__).resolve().parent

    def t_index_html_exists():
        assert (project_root / "index.html").exists()

    def t_admin_html_exists():
        assert (project_root / "admin.html").exists()

    def t_stats_html_exists():
        assert (project_root / "stats.html").exists()

    def t_bot_py_exists():
        assert (project_root / "bot.py").exists()

    def t_database_py_exists():
        assert (project_root / "database.py").exists()

    def t_utils_py_exists():
        assert (project_root / "utils.py").exists()

    def t_api_py_exists():
        assert (project_root / "api.py").exists()

    def t_http_api_py_exists():
        assert (project_root / "http_api.py").exists()

    def t_config_py_exists():
        assert (project_root / "config.py").exists()

    def t_handlers_py_exists():
        assert (project_root / "handlers.py").exists()

    def t_index_html_has_api_key_placeholder():
        html = (project_root / "index.html").read_text(encoding="utf-8")
        assert "{{API_SECRET_KEY}}" in html

    def t_admin_html_has_api_key_placeholder():
        html = (project_root / "admin.html").read_text(encoding="utf-8")
        assert "{{API_SECRET_KEY}}" in html

    def t_stats_html_has_placeholders():
        html = (project_root / "stats.html").read_text(encoding="utf-8")
        assert "{{TOTAL_VISITS}}" in html
        assert "{{STREAK_DAYS}}" in html

    def t_media_folder_writable():
        from config import config
        test_file = Path(config.MEDIA_FOLDER) / "_test_write.tmp"
        test_file.write_text("test")
        assert test_file.exists()
        test_file.unlink()

    for name, fn in [
        ("FS: index.html exists", t_index_html_exists),
        ("FS: admin.html exists", t_admin_html_exists),
        ("FS: stats.html exists", t_stats_html_exists),
        ("FS: bot.py exists", t_bot_py_exists),
        ("FS: database.py exists", t_database_py_exists),
        ("FS: utils.py exists", t_utils_py_exists),
        ("FS: api.py exists", t_api_py_exists),
        ("FS: http_api.py exists", t_http_api_py_exists),
        ("FS: config.py exists", t_config_py_exists),
        ("FS: handlers.py exists", t_handlers_py_exists),
        ("FS: index.html API key placeholder", t_index_html_has_api_key_placeholder),
        ("FS: admin.html API key placeholder", t_admin_html_has_api_key_placeholder),
        ("FS: stats.html placeholders", t_stats_html_has_placeholders),
        ("FS: media folder writable", t_media_folder_writable),
    ]:
        _run(name, fn)

# ═══════════════════════════════════════════════════════════════
# БЛОК 7 — Бизнес-логика: параметры, форматирование, edge cases
# ═══════════════════════════════════════════════════════════════

def _test_business_logic():

    def t_date_met_is_before_today():
        from config import config
        assert config.DATE_MET < date.today()

    def t_site_open_after_date_met():
        from config import config
        assert config.SITE_OPEN_DATE >= config.DATE_MET

    def t_days_together_positive():
        from config import config
        delta = date.today() - config.DATE_MET
        assert delta.days >= 0

    def t_parse_ai_date_formats():
        from utils import parse_ai_date_to_db
        cases = [
            ("15.6.2026", "2026-06-15"),
            ("1.1.2026", "2026-01-01"),
            ("31.12.2025", "2025-12-31"),
        ]
        for inp, expected in cases:
            result = parse_ai_date_to_db(inp)
            assert result is not None, f"Не распознана дата: {inp}"
            assert expected in result, f"Ожидали {expected}, получили {result}"

    def t_parse_date_validation_retry():
        from api import validate_parsed_date
        from datetime import date
        today = date(2026, 5, 31)
        assert validate_parsed_date("через 7 дней", "7.6.2026", today, True) is True
        assert validate_parsed_date("31 мая 2027", "31.5.2025", today, True) is False
        assert validate_parsed_date("31 мая 2027", "31.5.2027", today, True) is True
        assert validate_parsed_date("через 7 дней", "31.5.2026", today, True) is False
        
        # Test strict past date/time validation
        dt_ctx = "Сейчас у пользователя: 31 мая 2026, воскресенье, 21:24 (час 21, минута 24). Месяц: май, год: 2026."
        assert validate_parsed_date("событие", "1.6.2025", today, True, dt_ctx) is False
        assert validate_parsed_date("событие", "1.6.2026", today, True, dt_ctx) is True
        assert validate_parsed_date("событие", "31.5.2026 21:00", today, True, dt_ctx) is False
        assert validate_parsed_date("событие", "31.5.2026 22:00", today, True, dt_ctx) is True

    def t_format_time_remaining_days():
        from utils import format_time_remaining
        future = (datetime.now(timezone.utc) + timedelta(days=10, hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        result = format_time_remaining(future, None)
        assert "д" in result or "г" in result

    def t_format_time_remaining_hours():
        from utils import format_time_remaining
        future = (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        result = format_time_remaining(future, None)
        assert "ч" in result

    def t_sanitize_preserves_bold_italic():
        from utils import sanitize_html_for_telegram
        html = "<b>жирный</b> и <i>курсив</i>"
        result = sanitize_html_for_telegram(html)
        assert "<b>жирный</b>" in result
        assert "<i>курсив</i>" in result

    def t_sanitize_strips_div():
        from utils import sanitize_html_for_telegram
        result = sanitize_html_for_telegram("<div>текст</div>")
        assert "<div>" not in result
        assert "текст" in result

    def t_format_site_data_memories_sorted():
        from http_api import _collect_site_data
        data = _collect_site_data(None)
        mems = data["memories"]
        assert isinstance(mems, list)
        for m in mems:
            assert "id" in m
            assert "title" in m
            assert "category" in m

    def t_format_site_data_events_have_utc():
        from http_api import _collect_site_data
        data = _collect_site_data(None)
        for e in data["events"]:
            assert "event_utc_iso" in e
            assert "is_passed" in e

    def t_format_site_data_wishes_structure():
        from http_api import _collect_site_data
        data = _collect_site_data(None)
        wishes = data["wishes"]
        assert "ksusha" in wishes
        for w in wishes["ksusha"]:

            assert "content_html" in w

    def t_admin_keyboard_structure():
        from utils import create_admin_keyboard
        kb = create_admin_keyboard()
        assert kb is not None
        assert len(kb.inline_keyboard) > 0
        first_row = kb.inline_keyboard[0]
        button_texts = [btn.text for btn in first_row]
        assert "История версий" in button_texts

    def t_main_keyboard_structure():
        from utils import create_main_keyboard
        from config import config
        kb = create_main_keyboard(config.CREATOR_ID)
        assert kb is not None
        assert len(kb.inline_keyboard) > 0

    def t_category_keyboard_empty():
        from utils import create_category_keyboard_paged
        kb = create_category_keyboard_paged("memories", [], page=1)
        assert kb is not None

    def t_settings_time_keyboard():
        from utils import create_settings_time_keyboard
        kb = create_settings_time_keyboard("Europe/Moscow")
        assert kb is not None
        flat = [btn for row in kb.inline_keyboard for btn in row]
        texts = [b.text for b in flat]
        assert any("✅" in t for t in texts)

    def t_wish_keyboards():
        from utils import create_wish_user_keyboard, create_wish_admin_keyboard
        kb_user = create_wish_user_keyboard(1)
        kb_admin = create_wish_admin_keyboard(1)
        assert kb_user is not None
        assert kb_admin is not None

    def t_favorites_keyboard_empty():
        from utils import create_favorites_menu_keyboard
        kb = create_favorites_menu_keyboard([], page=1, total_pages=1, total=0)
        assert kb is not None

    def t_search_results_keyboard_empty():
        from utils import create_search_results_keyboard
        kb = create_search_results_keyboard([])
        assert kb is not None

    for name, fn in [
        ("Logic: date_met before today", t_date_met_is_before_today),
        ("Logic: site_open after date_met", t_site_open_after_date_met),
        ("Logic: days_together positive", t_days_together_positive),
        ("Logic: parse_ai_date various formats", t_parse_ai_date_formats),
        ("Logic: parse date validation retry", t_parse_date_validation_retry),
        ("Logic: time_remaining days", t_format_time_remaining_days),
        ("Logic: time_remaining hours", t_format_time_remaining_hours),
        ("Logic: sanitize preserves b/i", t_sanitize_preserves_bold_italic),
        ("Logic: sanitize strips div", t_sanitize_strips_div),
        ("Logic: site_data memories structure", t_format_site_data_memories_sorted),
        ("Logic: site_data events have utc", t_format_site_data_events_have_utc),
        ("Logic: site_data wishes structure", t_format_site_data_wishes_structure),
        ("Logic: admin keyboard structure", t_admin_keyboard_structure),
        ("Logic: main keyboard structure", t_main_keyboard_structure),
        ("Logic: category keyboard empty", t_category_keyboard_empty),
        ("Logic: settings time keyboard", t_settings_time_keyboard),
        ("Logic: wish keyboards", t_wish_keyboards),
        ("Logic: favorites keyboard empty", t_favorites_keyboard_empty),
        ("Logic: search results keyboard empty", t_search_results_keyboard_empty),
    ]:
        _run(name, fn)

# ═══════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА
# ═══════════════════════════════════════════════════════════════

async def run_all_tests() -> Tuple[int, int, List[str]]:
    """
    Запускает все тесты и возвращает (passed, failed, failed_names).
    """
    _results.clear()

    blocks = [
        _test_config,
        _test_constants,
        _test_filesystem,
        _test_database,
        _test_utils,
        _test_http_api,
        _test_business_logic,
    ]

    for block in blocks:
        try:
            block()
        except Exception as e:
            _fail(f"BLOCK ERROR [{block.__name__}]", str(e))

    passed = sum(1 for ok, _ in _results if ok)
    failed = sum(1 for ok, _ in _results if not ok)
    failed_names = [name for ok, name in _results if not ok]

    return passed, failed, failed_names
