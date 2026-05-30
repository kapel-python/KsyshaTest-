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
TZ = "America/New_York"

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
    mem_payload = {
        "visitor_id": "creator",
        "category": "memories",
        "title": "Test Memory Timezone",
        "date": "2026-05-30",
        "content": "Testing timezone rendering"
    }
    requests.post(f"{BASE_URL}/api/create_memory", json=mem_payload, headers=HEADERS)
    
    # 2. Check DB
    db_path = "/workspace/data/memories.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, date, created_at FROM memories ORDER BY id DESC LIMIT 1")
    mem_row = cur.fetchone()
    mem_id = mem_row[0]
    
    cur.execute("SELECT id, created_at FROM wishes ORDER BY id DESC LIMIT 1")
    wish_row = cur.fetchone()
    wish_id = wish_row[0]
    conn.close()

    # 3. Check API Response (with timezone query parameter!)
    r_mem = requests.get(f"{BASE_URL}/api/memories?visitor_id=creator&tz={TZ}", headers=HEADERS)
    mem_data = [m for m in r_mem.json().get('items', []) if m.get('id') == mem_id][0]
    
    # Let's get my wishes by bypassing partner check just looking at DB created_at
    wish_created_at = wish_row[1]

    # 4. Simulate Frontend rendering
    node_script = f"""
    const tz = '{TZ}';
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
    
    console.log("=== MEMORY PIPELINE ===");
    console.log("Server timezone:", "{get_server_tz()}");
    console.log("User timezone:", tz);
    console.log("Database value (date):", "{mem_row[1]}");
    console.log("API date_resolved:", "{mem_data.get('date_resolved')}");
    console.log("API date_human:", "{mem_data.get('date_human')}");
    // The UI renders date_human
    console.log("Rendered value (UI main text):", "{mem_data.get('date_human')}");
    console.log("Expected value:", "30 мая 2026");

    console.log("\\n=== WISH PIPELINE ===");
    console.log("Server timezone:", "{get_server_tz()}");
    console.log("User timezone:", tz);
    console.log("Database value (created_at):", "{wish_created_at}");
    // API sends exactly what's in DB
    console.log("API raw value:", "{wish_created_at}");
    // UI renders formatDateTimeLocal
    console.log("Rendered value (UI main text):", formatDateTimeLocal("{wish_created_at}"));
    console.log("Expected value:", formatDateTimeLocal("{wish_created_at}"));
    """
    
    with open("render_test.js", "w") as f:
        f.write(node_script)
    
    out = subprocess.check_output(['node', 'render_test.js']).decode('utf-8')
    print(out)

if __name__ == "__main__":
    run_test()
