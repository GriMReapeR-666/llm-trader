import json
manifest_path = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcript_manifest.json'
with open(manifest_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
print('Total fetched:', len(data['videos']))
print('Failed:', len(data['failed_videos']))
print('Sample fetched:')
for v in data['videos'][:5]:
    print(' ', v['id'], '-', v['title'][:50])
print('Sample failed:')
for v in data['failed_videos'][:5]:
    print(' ', v['id'], '-', v['title'][:50])
