import re

with open("test_version_ux.py", "r") as f:
    content = f.read()

content = content.replace('"version": "1.0.8"', '"version": "1.1.0"')
content = content.replace('admin_version_detail:1.0.8:0', 'admin_version_detail:1.1.0:0')
content = content.replace('admin_rollback_trigger:1.0.8', 'admin_rollback_trigger:1.1.0')

with open("test_version_ux.py", "w") as f:
    f.write(content)
