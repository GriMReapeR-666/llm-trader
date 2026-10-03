import yt_dlp
import json
import os
import time

VIDEOS_JSON = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_videos.json'
OUTPUT_DIR = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcripts_ytdlp'
MANIFEST_PATH = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcript_manifest_ytdlp.json'
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(VIDEOS_JSON, 'r', encoding='utf-8') as f:
    videos = json.load(f)

manifest = {
    'total_videos': len(videos),
    'transcripts_fetched': 0,
    'skipped': 0,
    'failed': 0,
    'videos': [],
    'skipped_videos': [],
    'failed_videos': [],
}

# Load existing progress if any
if os.path.exists(MANIFEST_PATH):
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

fetched_ids = {v['id'] for v in manifest['videos']}

for i, video in enumerate(videos):
    vid_id = video['id']
    title = video['title']
    if vid_id in fetched_ids:
        continue
    
    url = f"https://www.youtube.com/watch?v={vid_id}"
    ydl_opts = {
        'skip_download': True,
        'writeautomaticsub': True,
        'write_subs': False,
        'subtitleslangs': ['en'],
        'outtmpl': os.path.join(OUTPUT_DIR, f"{vid_id}.%(ext)s"),
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        
        # Look for the downloaded subtitle file
        subtitle_path = None
        for ext in ['vtt', 'srt', 'ttml', 'sbv']:
            candidate = os.path.join(OUTPUT_DIR, f"{vid_id}.en.{ext}")
            if os.path.exists(candidate):
                subtitle_path = candidate
                break
            candidate2 = os.path.join(OUTPUT_DIR, f"{vid_id}.{ext}")
            if os.path.exists(candidate2):
                subtitle_path = candidate2
                break
        
        if subtitle_path:
            with open(subtitle_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Remove VTT/SRT timestamps and metadata
            lines = content.split('\n')
            text_lines = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith('WEBVTT') or line.startswith('Kind:') or line.startswith('Language:'):
                    continue
                if '-->' in line:
                    continue
                if line.startswith('<') or line.startswith('&gt;'):
                    continue
                # Remove HTML tags
                import re
                clean = re.sub(r'<[^>]+>', '', line)
                clean = re.sub(r'&[a-z]+;', ' ', clean)
                if clean.strip():
                    text_lines.append(clean.strip())
            text = ' '.join(text_lines)
            
            # Clean up extra spaces
            text = ' '.join(text.split())
            
            manifest['videos'].append({'id': vid_id, 'title': title, 'status': 'ok', 'text': text, 'url': video['url']})
            fetched_ids.add(vid_id)
            manifest['transcripts_fetched'] += 1
            print(f"[OK] {vid_id} - {title[:60]}")
            
            # Save text file
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c in ' _-']).rstrip()[:80]
            txt_path = os.path.join(OUTPUT_DIR, f"{vid_id}_{safe_title}.txt")
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(text)
        else:
            manifest['skipped_videos'].append({'id': vid_id, 'title': title, 'url': video['url']})
            manifest['skipped'] += 1
            print(f"[SKIP] {vid_id} - {title[:60]} (no subtitle file)")
    except Exception as e:
        manifest['failed_videos'].append({'id': vid_id, 'title': title, 'error': str(e), 'url': video['url']})
        manifest['failed'] += 1
        print(f"[FAIL] {vid_id} - {title[:60]} - {str(e)[:80]}")
    
    # Save progress every 10 videos
    if (i + 1) % 10 == 0:
        with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        print(f"Progress saved: {manifest['transcripts_fetched']} fetched, {manifest['skipped']} skipped, {manifest['failed']} failed")
    
    time.sleep(0.5)

with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
print(f"\nFinal: {manifest['transcripts_fetched']} fetched, {manifest['skipped']} skipped, {manifest['failed']} failed")
