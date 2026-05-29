import sqlite3
import json
import os

source_of_truth = '/workspace/data/memories.db'
ghost_dbs = [
    '/app/data/memories.db',
    '/root/KsyshaTest/data/memories.db'
]

def get_versions(db_path):
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM version_history ORDER BY created_at ASC')
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        return []

truth_versions = get_versions(source_of_truth)
truth_version_strings = {v['version'] for v in truth_versions}

migrated_versions = []

with sqlite3.connect(source_of_truth) as conn:
    for ghost in ghost_dbs:
        ghost_versions = get_versions(ghost)
        for gv in ghost_versions:
            if gv['version'] not in truth_version_strings:
                print(f"Migrating {gv['version']} from {ghost} to source of truth")
                conn.execute(
                    'INSERT INTO version_history (version, description, git_commit, created_at) VALUES (?, ?, ?, ?)',
                    (gv['version'], gv['description'], gv['git_commit'], gv['created_at'])
                )
                truth_version_strings.add(gv['version'])
                migrated_versions.append(gv['version'])
    
    conn.commit()

# Create migration report
report_path = "/root/.gemini/antigravity-cli/brain/32ef6d56-c162-4a95-85d8-6889f63cdede/migration_report.md"
os.makedirs(os.path.dirname(report_path), exist_ok=True)

with open(report_path, "w") as f:
    f.write("# Database Migration Report\n\n")
    f.write("## Source of Truth\n")
    f.write(f"`{source_of_truth}`\n\n")
    f.write("## Migrated Records\n")
    if not migrated_versions:
        f.write("No new records were migrated.\n")
    else:
        for mv in migrated_versions:
            f.write(f"- Version `{mv}` successfully imported.\n")

print("Migration completed.")
