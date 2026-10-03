import json
import os

manifest_path = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcript_manifest.json'
with open(manifest_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
for v in data['videos'][:10]:
    print(v['id'], '-', v['title'][:60])
print('Total fetched:', len(data['videos']))
