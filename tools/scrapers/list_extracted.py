import json

manifest_path = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcript_manifest.json'
with open(manifest_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"Successfully extracted transcripts: {len(data['videos'])}")
print("\nVideo titles and topics:")
for i, v in enumerate(data['videos'], 1):
    print(f"{i:2d}. {v['title']}")
    print(f"    URL: {v['url']}")

print(f"\nFailed videos: {len(data['failed_videos'])}")
