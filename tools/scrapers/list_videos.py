import yt_dlp
import json
import os

channel_url = 'https://www.youtube.com/channel/UC606MUq45P3zFLa4VGKbxsg'
ydl_opts = {
    'extract_flat': 'in_playlist',
    'quiet': True,
    'no_warnings': True,
    'dump_single_json': True,
    'playlistend': 10000,
}
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(channel_url, download=False)
    if 'entries' in info:
        videos = []
        for entry in info['entries']:
            if entry:
                videos.append({
                    'id': entry.get('id'),
                    'title': entry.get('title'),
                    'url': f"https://www.youtube.com/watch?v={entry.get('id')}" if entry.get('id') else None,
                    'duration': entry.get('duration'),
                })
        print(f'Found {len(videos)} videos')
        output_path = os.path.join(os.path.dirname(__file__), 'educational_platform_videos.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(videos, f, indent=2, ensure_ascii=False)
        for v in videos[:5]:
            print(v)
    else:
        print('No entries found')
