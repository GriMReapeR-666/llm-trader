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
    print(json.dumps(info, indent=2)[:3000])
