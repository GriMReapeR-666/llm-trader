import os
transcript_dir = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_transcripts'
files = os.listdir(transcript_dir)
print('Transcript files:', len(files))
for f in files[:3]:
    path = os.path.join(transcript_dir, f)
    with open(path, 'r', encoding='utf-8') as fh:
        content = fh.read()
    print(f'--- {f} ---')
    print(content[:500])
    print('...')
