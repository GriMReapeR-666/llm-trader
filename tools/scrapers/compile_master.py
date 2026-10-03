import os
import json

TRANSCRIPT_DIR = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcripts'
OUTPUT_PATH = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_master_transcript_raw.txt'
MANIFEST_PATH = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcript_manifest.json'

with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
    manifest = json.load(f)

videos_map = {v['id']: v for v in manifest['videos']}

files = sorted([f for f in os.listdir(TRANSCRIPT_DIR) if f.endswith('.txt')])
print(f'Compiling {len(files)} transcript files...')

with open(OUTPUT_PATH, 'w', encoding='utf-8') as out:
    out.write('=' * 80 + '\n')
    out.write('EDUCATIONAL_PLATFORM YOUTUBE CHANNEL - MASTER TRANSCRIPT COMPILATION\n')
    out.write('=' * 80 + '\n\n')
    out.write(f'Total videos on channel: 468\n')
    out.write(f'Transcripts successfully extracted: {len(files)}\n')
    out.write(f'Extraction date: 2026-08-19\n')
    out.write('NOTE: YouTube rate-limiting prevented full extraction of all 468 videos.\n')
    out.write('The following transcripts represent all publicly available transcripts that were\n')
    out.write('successfully retrieved before rate limits were encountered.\n\n')
    out.write('=' * 80 + '\n\n')
    
    for filename in files:
        vid_id = filename.split('_')[0]
        video = videos_map.get(vid_id)
        title = video['title'] if video else filename
        url = video['url'] if video else f'https://www.youtube.com/watch?v={vid_id}'
        
        filepath = os.path.join(TRANSCRIPT_DIR, filename)
        with open(filepath, 'r', encoding='utf-8') as fh:
            text = fh.read()
        
        out.write('-' * 80 + '\n')
        out.write(f'VIDEO: {title}\n')
        out.write(f'URL: {url}\n')
        out.write(f'VIDEO ID: {vid_id}\n')
        out.write('-' * 80 + '\n\n')
        out.write(text)
        out.write('\n\n')

print(f'Master transcript saved to: {OUTPUT_PATH}')

# Also create a summary manifest
summary = []
for filename in files:
    vid_id = filename.split('_')[0]
    video = videos_map.get(vid_id)
    summary.append({
        'id': vid_id,
        'title': video['title'] if video else filename,
        'url': video['url'] if video else f'https://www.youtube.com/watch?v={vid_id}',
        'source_file': filename
    })

summary_path = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcript_summary.json'
with open(summary_path, 'w', encoding='utf-8') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
print(f'Summary saved: {len(summary)} videos')
