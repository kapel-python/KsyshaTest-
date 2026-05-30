import requests
import sqlite3
import json
import subprocess
import os
import hmac
import hashlib
import base64

API_KEY = "Dh3HL-90TNmcoeWp1cAPErX8WqOJLl1as3u3amWkz8Q"
BASE_URL = "http://127.0.0.1:25086"

def _ai_hmac_secret() -> bytes:
    return API_KEY.encode("utf-8")

def _sign_payload(value: str) -> str:
    mac = hmac.new(_ai_hmac_secret(), value.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")

HEADERS = {
    "X-Api-Key": API_KEY,
    "Content-Type": "application/json",
    "X-Visitor-Id": "creator",
    "X-Visitor-Signature": _sign_payload("visitor:creator")
}

def get_server_tz():
    return subprocess.check_output(['date']).decode('utf-8').strip()

def run_test():
    # 1. Create a Memory
    print("=== Creating Memory ===")
    mem_payload = {
        "visitor_id": "creator",
        "category": "memories",
        "title": "Test Memory Timezone",
        "date": "2026-05-30",
        "content": "Testing timezone rendering"
    }
    r = requests.post(f"{BASE_URL}/api/create_memory", json=mem_payload, headers=HEADERS)
    print("Create Memory API status:", r.status_code, r.text)
    if False:
        return
    
    # 2. Create a Wish
    print("\n=== Creating Wish ===")
    wish_payload = {
        "visitor_id": "creator",
        "content": "Test Wish Timezone"
    }
    r = requests.post(f"{BASE_URL}/api/create_wish", json=wish_payload, headers=HEADERS)
    print("Create Wish API status:", r.status_code, r.text)
    
    # 3. Check DB
    print("\n=== Checking Database ===")
    db_path = "/workspace/data/memories.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cur.execute("SELECT id, date, created_at FROM memories WHERE title='Test Memory Timezone' ORDER BY id DESC LIMIT 1")
    mem_row = cur.fetchone()
    print(f"Memory in DB -> id: {mem_row[0]}, date: {mem_row[1]}, created_at: {mem_row[2]}")
    mem_id = mem_row[0]
    
    cur.execute("SELECT id, created_at FROM wishes WHERE content='Test Wish Timezone' ORDER BY id DESC LIMIT 1")
    wish_row = cur.fetchone()
    print(f"Wish in DB -> id: {wish_row[0]}, created_at: {wish_row[1]}")
    wish_id = wish_row[0]
    
    conn.close()

    # 4. Check API Response
    print("\n=== Checking API Response ===")
    r_mem = requests.get(f"{BASE_URL}/api/memories?visitor_id=creator", headers=HEADERS)
    mem_data = [m for m in r_mem.json().get('items', []) if m.get('id') == mem_id][0]
    print("Memory API JSON:", json.dumps({
        "date": mem_data.get("date"),
        "date_human": mem_data.get("date_human"),
        "date_resolved": mem_data.get("date_resolved")
    }, ensure_ascii=False))
    
    r_wish = requests.get(f"{BASE_URL}/api/wishes?visitor_id=creator", headers=HEADERS)
    print(r_wish.json()); wish_data = [w for w in r_wish.json().get('items', []) if w.get('id') == wish_id][0]
    print("Wish API JSON:", json.dumps({
        "created_at": wish_data.get("created_at"),
        "date_human": wish_data.get("date_human")
    }, ensure_ascii=False))

    # 5. Simulate Frontend rendering using Node.js
    print("\n=== Simulating Frontend Rendering (Node.js) ===")
    node_script = f"""
    const tz = 'Europe/Moscow';
    function parseUtcDate(str) {{
        if (!str) return null;
        let iso = str.trim();
        if (iso.length === 10) {{
          iso += 'T00:00:00Z';
        }} else {{
          if (iso.indexOf('T') === -1) {{
            iso = iso.replace(' ', 'T');
          }}
          if (!iso.endsWith('Z') && iso.indexOf('+') === -1 && iso.indexOf('-') === -1) {{
            iso += 'Z';
          }}
        }}
        const d = new Date(iso);
        return isNaN(d.getTime()) ? null : d;
    }}
    
    function formatDateTimeLocal(utcStr) {{
        if (!utcStr) return '';
        const d = parseUtcDate(utcStr);
        if (!d) return utcStr;
        let day, monthIdx, year, hours, minutes;
        try {{
          const parts = new Intl.DateTimeFormat('en-US', {{
            timeZone: tz,
            year: 'numeric', month: 'numeric', day: 'numeric',
            hour: 'numeric', minute: 'numeric', hour12: false
          }}).formatToParts(d);
          const partMap = {{}};
          parts.forEach(p => partMap[p.type] = p.value);
          day = Number(partMap.day);
          monthIdx = Number(partMap.month) - 1;
          year = Number(partMap.year);
          hours = Number(partMap.hour);
          minutes = Number(partMap.minute);
        }} catch (e) {{
          day = d.getDate(); monthIdx = d.getMonth(); year = d.getFullYear();
          hours = d.getHours(); minutes = d.getMinutes();
        }}
        const months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
        const dd = day < 10 ? '0'+day : day;
        const mm = (monthIdx+1) < 10 ? '0'+(monthIdx+1) : (monthIdx+1);
        const hh = hours < 10 ? '0'+hours : hours;
        const min = minutes < 10 ? '0'+minutes : minutes;
        return `${{day}} ${{months[monthIdx]}} ${{year}} г., ${{hh}}:${{min}}`;
    }}
    
    console.log("User timezone simulated:", tz);
    console.log("Memory DB date rendered (via formatDateTimeLocal):", formatDateTimeLocal('{mem_data.get("date_resolved")}'));
    console.log("Memory date_human passed directly:", '{mem_data.get("date_human")}');
    
    console.log("Wish DB created_at rendered (via formatDateTimeLocal):", formatDateTimeLocal('{wish_data.get("created_at")}'));
    console.log("Wish date_human passed directly:", '{wish_data.get("date_human")}');
    """
    
    with open("render_test.js", "w") as f:
        f.write(node_script)
    
    out = subprocess.check_output(['node', 'render_test.js']).decode('utf-8')
    print(out)

if __name__ == "__main__":
    print(f"Server timezone: {get_server_tz()}")
    run_test()
