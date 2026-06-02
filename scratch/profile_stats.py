import os
import sys
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config
from database import db
from http_api import _build_profile_stats_for_visitor

logging.basicConfig(level=logging.INFO)

def main():
    visitor_id = "creator" # using creator as sample
    print("Profiling profile stats generation...")
    
    start_time = time.perf_counter()
    stats = _build_profile_stats_for_visitor(visitor_id, "Europe/Moscow")
    duration_ms = (time.perf_counter() - start_time) * 1000.0
    print(f"Profile stats built in {duration_ms:.2f} ms")
    import pprint
    pprint.pprint(stats)

if __name__ == "__main__":
    main()
