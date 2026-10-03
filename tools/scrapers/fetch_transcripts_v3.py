import json
import os
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound, VideoUnavailable

VIDEOS_JSON = os.path.join(os.path.dirname(__file__), 'educational_platform_videos.json')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'educational_platform_transcripts')
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(VIDEOS_JSON, 'r', encoding='utf-8') as f:
    videos = json.load(f)

print(f"Total videos to process: {len(videos)}")

ytt_api = YouTubeTranscriptApi()
results = []
skipped = []
failed = []

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

for video in videos:
    res = fetch_transcript(video)
    if res['status'] == 'ok':
        results.append(res)
        safe_title = "".join([c for c in res['title'] if c.isalpha() or c.isdigit() or c in ' _-']).rstrip()
        safe_title = safe_title[:80]
        fname = f"{res['id']}_{safe_title}.txt"
        fpath = os.path.join(OUTPUT_DIR, fname)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(res['text'])
        print(f"[OK] {res['id']} - {res['title'][:60]}")
    elif res['status'] == 'no_transcript':
        skipped.append(res)
        print(f"[SKIP] {res['id']} - {res['title'][:60]}")
    else:
        failed.append(res)
        print(f"[FAIL] {res['id']} - {res['title'][:60]} - {res.get('error', '')}")

print(f"\nProcessed {len(videos)} videos")
print(f"Transcripts fetched: {len(results)}")
print(f"Skipped (no transcript): {len(skipped)}")
print(f"Failed: {len(failed)}")

manifest = {
    'total_videos': len(videos),
    'transcripts_fetched': len(results),
    'skipped': len(skipped),
    'failed': len(failed),
    'videos': results,
    'skipped_videos': skipped,
    'failed_videos': failed,
}
manifest_path = os.path.join(os.path.dirname(__file__), 'educational_platform_transcript_manifest.json')
with open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
