import re
import subprocess
import os

with open("/root/KsyshaTest/index.html", "r", encoding="utf-8") as f:
    content = f.read()

scripts = re.findall(r"<script>(.*?)</script>", content, re.DOTALL)

for i, script in enumerate(scripts):
    filename = f"/root/KsyshaTest/scratch/script_{i}.js"
    with open(filename, "w", encoding="utf-8") as sf:
        sf.write(script)
    
    # Run node syntax check
    res = subprocess.run(["node", "--check", filename], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error in script {i}:")
        print(res.stderr)
    else:
        print(f"Script {i} syntax is OK")
    
    os.remove(filename)
