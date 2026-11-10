import json
import os

with open(r'D:\Agents\CrazzyDocs\media\quarantine\manifest.json', 'r') as f:
    d = json.load(f)

print(f"Total quarantine manifest items: {len(d)}")
char_items = []
archived_dir = r"D:\Agents\CrazzyDocs\media\quarantine\archived_files"

for qid, it in d.items():
    rel = it.get('original_relative_path', '')
    cat = it.get('category', '')
    q_rel = it.get('quarantine_relative_path', '')
    stored_fn = os.path.basename(q_rel)
    is_char = any(k in rel.lower() for k in ['character', 'stick', 'rig', 'host', 'hybrid'])
    is_purged = it.get('purged', False)
    stored_path = os.path.join(archived_dir, stored_fn)
    exists = os.path.isfile(stored_path)
    if is_char:
        char_items.append((qid, rel, stored_fn, is_purged, exists, it.get('size_bytes', 0)))

print(f"Matching character/rig/hybrid items: {len(char_items)}")
for qid, rel, fn, purged, exists, sz in char_items:
    print(f"ID: {qid} | Exists: {exists} | Purged: {purged} | Size: {sz} | Stored: {fn} | Orig: {rel}")
