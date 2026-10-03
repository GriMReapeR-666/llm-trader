import yt_dlp
import json
import os

channel_videos_url = 'https://www.youtube.com/channel/UC606MUq45P3zFLa4VGKbxsg/videos'
ydl_opts = {
    'extract_flat': 'in_playlist',
    'quiet': True,
    'no_warnings': True,
    'dump_single_json': True,
    'playlistend': 10000,
}
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(channel_videos_url, download=False)
    entries = info.get('entries', [])
    print(f"Total entries: {len(entries)}")
    videos = []
    for entry in entries:
        if entry and entry.get('id') and not entry['id'].startswith('UC'):
            videos.append({
                'id': entry.get('id'),
                'title': entry.get('title'),
                'url': f"https://www.youtube.com/watch?v={entry.get('id')}",
                'duration': entry.get('duration'),
            })
    print(f"Actual video count: {len(videos)}")
    output_path = os.path.join(os.path.dirname(__file__), 'educational_platform_videos.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(videos, f, indent=2, ensure_ascii=False)
    for v in videos[:10]:
        print(v)
