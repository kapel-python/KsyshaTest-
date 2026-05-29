import os
import sys
import time
import json
import asyncio
import aiohttp
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import config
from database import db

RESULTS_FILE = "data/last_load_test.json"

def get_memory_usage_mb() -> float:
    try:
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    parts = line.split()
                    if len(parts) >= 2:
                        return float(parts[1]) / 1024.0
    except Exception:
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0

def load_last_result():
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return None

def save_result(result):
    try:
        os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
        with open(RESULTS_FILE, 'w') as f:
            json.dump(result, f, indent=2)
    except Exception:
        pass

async def fetch_url(session, url, semaphore):
    headers = {"X-Real-IP": "127.0.0.1"}
    async with semaphore:
        start_time = time.perf_counter()
        try:
            async with session.get(url, headers=headers, timeout=5) as response:
                await response.read()
                latency = (time.perf_counter() - start_time) * 1000.0
                if response.status == 200:
                    return True, latency
                else:
                    return False, latency
        except Exception:
            latency = (time.perf_counter() - start_time) * 1000.0
            return False, latency

async def run_http_load(base_url, total_requests, max_concurrent):
    semaphore = asyncio.Semaphore(max_concurrent)
    urls = []
    for i in range(total_requests):
        if i % 2 == 0:
            urls.append(f"{base_url}/")
        else:
            urls.append(f"{base_url}/api/version")
            
    success_count = 0
    failure_count = 0
    latencies = []
    
    async with aiohttp.ClientSession() as session:
        tasks = [asyncio.create_task(fetch_url(session, url, semaphore)) for url in urls]
        for task in asyncio.as_completed(tasks):
            success, latency = await task
            if success:
                success_count += 1
            else:
                failure_count += 1
            latencies.append(latency)
            
    avg_ms = sum(latencies) / len(latencies) if latencies else 0.0
    max_ms = max(latencies) if latencies else 0.0
    return success_count, failure_count, avg_ms, max_ms

def run_db_reads_sync(count: int):
    success = 0
    failures = 0
    latencies = []
    for _ in range(count):
        start_t = time.perf_counter()
        try:
            val = db.get_setting("test_version")
            lat = (time.perf_counter() - start_t) * 1000.0
            success += 1
            latencies.append(lat)
        except Exception:
            lat = (time.perf_counter() - start_t) * 1000.0
            failures += 1
            latencies.append(lat)
    return success, failures, latencies

async def run_diagnostics(progress_cb=None):
    async def report_progress(msg):
        if progress_cb:
            if asyncio.iscoroutinefunction(progress_cb):
                await progress_cb(msg)
            else:
                progress_cb(msg)

    start_time = time.perf_counter()
    base_url = f"http://127.0.0.1:{config.HTTP_PORT}"
    
    last_res = load_last_result()
    ram_before = get_memory_usage_mb()
    
    await report_progress("🔥 Инициализация нагрузочных тестов...\nПамять: {:.1f} MB".format(ram_before))
        
    await report_progress("⚡ Запуск HTTP нагрузочного теста (50 запросов)...")
    s1_ok, s1_fail, s1_avg, s1_max = await run_http_load(base_url, 50, 50)
    
    await report_progress("⚡ Запуск HTTP нагрузочного теста (100 запросов)...")
    s2_ok, s2_fail, s2_avg, s2_max = await run_http_load(base_url, 100, 50)
    
    await report_progress("⚡ Запуск HTTP нагрузочного теста (250 запросов, финальный)...")
    s3_ok, s3_fail, s3_avg, s3_max = await run_http_load(base_url, 100, 50)
    
    http_total_ok = s1_ok + s2_ok + s3_ok
    http_total_fail = s1_fail + s2_fail + s3_fail
    http_total = http_total_ok + http_total_fail
    http_avg = (s1_avg * 50 + s2_avg * 100 + s3_avg * 100) / 250.0
    http_max = max(s1_max, s2_max, s3_max)
    
    await report_progress("⚡ Запуск нагрузочного теста БД (100 чтений)...")
    db1_ok, db1_fail, db1_lats = await asyncio.to_thread(run_db_reads_sync, 100)
    
    await report_progress("⚡ Запуск нагрузочного теста БД (500 чтений)...")
    db2_ok, db2_fail, db2_lats = await asyncio.to_thread(run_db_reads_sync, 400)
    
    await report_progress("⚡ Запуск нагрузочного теста БД (1000 чтений, финальный)...")
    db3_ok, db3_fail, db3_lats = await asyncio.to_thread(run_db_reads_sync, 500)
    
    db_total_ok = db1_ok + db2_ok + db3_ok
    db_total_fail = db1_fail + db2_fail + db3_fail
    db_total = db_total_ok + db_total_fail
    all_db_lats = db1_lats + db2_lats + db3_lats
    db_avg = sum(all_db_lats) / len(all_db_lats) if all_db_lats else 0.0
    
    await report_progress("⚡ Запуск теста стабильности и проверки здоровья...")
        
    health_website = True
    health_db = True
    health_diag = True
    
    async with aiohttp.ClientSession() as session:
        headers = {"X-Real-IP": "127.0.0.1"}
        for _ in range(5):
            try:
                async with session.get(f"{base_url}/", headers=headers, timeout=3) as r:
                    if r.status != 200:
                        health_website = False
            except Exception:
                health_website = False
                
            try:
                db_val = db.get_setting("test_version")
                if db_val is None:
                    health_db = False
            except Exception:
                health_db = False
                
            try:
                async with session.get(f"{base_url}/api/version", headers=headers, timeout=3) as r:
                    if r.status != 200:
                        health_diag = False
            except Exception:
                health_diag = False
                
            await asyncio.sleep(0.1)
            
    ram_after = get_memory_usage_mb()
    ram_growth = ram_after - ram_before
    elapsed_time = time.perf_counter() - start_time
    
    new_res = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "http": {
            "total": http_total,
            "success": http_total_ok,
            "failed": http_total_fail,
            "avg_ms": round(http_avg, 1),
            "max_ms": round(http_max, 1)
        },
        "db": {
            "total": db_total,
            "success": db_total_ok,
            "failed": db_total_fail,
            "avg_ms": round(db_avg, 2)
        },
        "memory": {
            "ram_before": round(ram_before, 1),
            "ram_after": round(ram_after, 1),
            "growth": round(ram_growth, 1)
        },
        "health": {
            "website": "OK" if health_website else "FAIL",
            "db": "OK" if health_db else "FAIL",
            "diagnostics": "OK" if health_diag else "FAIL"
        },
        "elapsed_seconds": round(elapsed_time, 2)
    }
    
    save_result(new_res)
    return new_res, last_res

if __name__ == "__main__":
    print("🚀 Starting Production-Safe Load Test standalone...")
    new_res, last_res = asyncio.run(run_diagnostics(lambda msg: print(msg)))
    print("\n--- STANDALONE REPORT ---")
    print(f"Timestamp: {new_res['timestamp']}")
    print(f"HTTP: {new_res['http']['success']}/{new_res['http']['total']} (avg: {new_res['http']['avg_ms']} ms, max: {new_res['http']['max_ms']} ms)")
    print(f"DB: {new_res['db']['success']}/{new_res['db']['total']} (avg: {new_res['db']['avg_ms']} ms)")
    print(f"Memory: {new_res['memory']['ram_before']} MB -> {new_res['memory']['ram_after']} MB (growth: {new_res['memory']['growth']} MB)")
    print(f"Health: Website: {new_res['health']['website']}, DB: {new_res['health']['db']}, Diagnostics: {new_res['health']['diagnostics']}")
    print(f"Elapsed: {new_res['elapsed_seconds']}s")
    if last_res:
        print(f"Previous: Timestamp: {last_res['timestamp']}, HTTP avg: {last_res['http']['avg_ms']} ms, DB avg: {last_res['db']['avg_ms']} ms")
    print("-------------------------\n")
