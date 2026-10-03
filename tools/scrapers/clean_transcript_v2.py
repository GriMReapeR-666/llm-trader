import re
import os

INPUT_PATH = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_master_transcript_raw.txt'
OUTPUT_PATH = r'C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity\educational_platform_master_transcript_cleaned.txt'

with open(INPUT_PATH, 'r', encoding='utf-8') as f:
    text = f.read()

# Fix video title metadata - remove filename artifacts
text = re.sub(r'VIDEO: ([a-zA-Z0-9_-]+)_(.+?)\.txt', r'VIDEO TITLE: \2', text)

# Remove speaker markers and non-verbal cues
text = re.sub(r'>>\s*', '', text)
text = re.sub(r'\[snorts?\]', '', text, flags=re.IGNORECASE)
text = re.sub(r'\[clears?\s+throat\]', '', text, flags=re.IGNORECASE)
text = re.sub(r'\[music\]', '', text, flags=re.IGNORECASE)

# Fix obvious ASR errors (conservative list)
conservative_fixes = {
    'AIdriven': 'AI-driven',
    'AIdriven': 'AI-driven',
    'Quantia': 'Educational Platform',
    'Quantovian': 'Educational Platform',
    'Quontopian': 'Educational Platform',
    'Quantitivo': 'quantitative',
    'predict now.AI': 'predict.now.ai',
    'predict.ai AI': 'predict.ai',
    'QTS capital management': 'QTS Capital Management',
    'oneten': 'one-tenth',
    'onetenth': 'one-tenth',
}

for old, new in conservative_fixes.items():
    text = text.replace(old, new)

# Remove excessive word repetitions (3+ identical consecutive words)
text = re.sub(r'\b(\w+)(\s+\1){2,}\b', r'\1', text)

# Normalize whitespace
text = re.sub(r'\n{3,}', '\n\n', text)
text = re.sub(r'[ \t]+', ' ', text)

# Fix sentence fragments that got split across lines at end of file
lines = text.split('\n')
cleaned_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    stripped = line.strip()
    
    # Skip empty lines (but preserve single blank lines between sections)
    if not stripped:
        if cleaned_lines and cleaned_lines[-1].strip():
            cleaned_lines.append('')
        i += 1
        continue
    
    # If line ends with ... and next line exists and looks like continuation
    if stripped.endswith('...') and i + 1 < len(lines):
        next_line = lines[i + 1].strip()
        if next_line and not next_line.startswith('-') and not next_line.startswith('=') and not next_line.startswith('VIDEO') and len(next_line) > 50:
            cleaned_lines.append(stripped.rstrip('.').rstrip() + ' ' + next_line)
            i += 2
            continue
    
    cleaned_lines.append(line)
    i += 1

text = '\n'.join(cleaned_lines)
text = re.sub(r'\n{3,}', '\n\n', text)
text = re.sub(r'[ \t]+', ' ', text)

with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
    f.write(text)

print(f'Cleaned transcript saved to: {OUTPUT_PATH}')
print(f'Size: {os.path.getsize(OUTPUT_PATH):,} bytes')
