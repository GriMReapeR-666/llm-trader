import json
import os
import time
import socket
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound, VideoUnavailable

VIDEOS_JSON = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_videos.json'
OUTPUT_DIR = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcripts'
MANIFEST_PATH = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcript_manifest.json'
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(VIDEOS_JSON, 'r', encoding='utf-8') as f:
    videos = json.load(f)

with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
    manifest = json.load(f)

fetched_ids = {v['id'] for v in manifest['videos']}
failed_videos = manifest.get('failed_videos', [])

# Only retry network failures, not permanent no_transcript
retry_videos = []
for v in failed_videos:
    if v['id'] not in fetched_ids:
        retry_videos.append(v)

print(f"Retrying {len(retry_videos)} failed videos")

ytt_api = YouTubeTranscriptApi()
new_results = []
new_failed = []

def fetch_transcript(video):
    vid_id = video['id']
    title = video['title']
    try:
        transcript_list = ytt_api.list(vid_id)
        transcript = transcript_list.find_generated_transcript(['en'])
        fetched = transcript.fetch()
        text = ' '.join([t.text for t in fetched])
        return {'id': vid_id, 'title': title, 'status': 'ok', 'text': text, 'url': video['url']}
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
        return {'id': vid_id, 'title': title, 'status': 'no_transcript', 'url': video['url']}
    except Exception as e:
        return {'id': vid_id, 'title': title, 'status': 'error', 'error': str(e), 'url': video['url']}

for video in retry_videos:
    res = fetch_transcript(video)
    if res['status'] == 'ok':
        new_results.append(res)
        safe_title = "".join([c for c in res['title'] if c.isalpha() or c.isdigit() or c in ' _-']).rstrip()
        safe_title = safe_title[:80]
        fname = f"{res['id']}_{safe_title}.txt"
        fpath = os.path.join(OUTPUT_DIR, fname)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(res['text'])
        print(f"[OK] {res['id']} - {res['title'][:60]}")
    elif res['status'] == 'no_transcript':
        print(f"[SKIP] {res['id']} - {res['title'][:60]}")
    else:
        new_failed.append(res)
        print(f"[FAIL] {res['id']} - {res['title'][:60]} - {res.get('error', '')}")
    time.sleep(0.3)

print(f"\nNewly fetched: {len(new_results)}")
print(f"Still failing: {len(new_failed)}")

# Update manifest
manifest['videos'].extend(new_results)
manifest['failed_videos'] = new_failed
manifest['transcripts_fetched'] = len(manifest['videos'])
manifest['failed'] = len(new_failed)

with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
