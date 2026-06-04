```diff
diff --git a/database.py b/database.py
index ab87ddf..cc0f19a 100644
--- a/database.py
+++ b/database.py
@@ -2275,7 +2275,7 @@ class Database:
         from datetime import datetime, timedelta
         try:
             now = datetime.utcnow()
-            since = (now - timedelta(hours=1)).isoformat()
+            since = (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
             
             with self._get_connection() as conn:
                 if visitor_id:
```
