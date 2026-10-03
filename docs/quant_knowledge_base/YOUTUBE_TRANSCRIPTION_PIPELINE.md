# YouTube Quant Channel Transcription Pipeline

## IMPORTANT: Legal & Terms of Service Notice

**Before using this pipeline, you must understand:**

1. **YouTube Terms of Service**: YouTube's TOS prohibit downloading videos without permission from the content owner, except for personal, non-commercial use of videos you have the right to access. Bulk downloading entire channels likely violates TOS Section 5.B.

2. **Copyright Law**: Most YouTube videos are copyrighted by their creators. Downloading, transcribing, and storing transcriptions without permission may constitute copyright infringement.

3. **Recommended Approach**:
   - Only transcribe content you own or have explicit permission to use
   - Consider reaching out to creators for permission
   - Use YouTube's official API for metadata and subtitle extraction where available
   - For educational/research purposes, consider fair use/fair dealing doctrines in your jurisdiction

## Pipeline Architecture (For Legitimate Use Cases Only)

```
[YouTube Channel] → [yt-dlp] → [Audio/Subtitle Files] → [Whisper] → [Transcription Text]
                                                                      ↓
                                                            [Topic Classifier]
                                                                      ↓
                                                    [Regime_Detection | Strategy_Dev |
                                                     Portfolio_Mgmt | Order_Exec |
                                                     Quant_Utilities]
```

## Prerequisites

```bash
# Install yt-dlp (downloads audio/subtitles)
pip install yt-dlp

# Install Whisper (transcription)
pip install openai-whisper

# Optional: faster-whisper (optimized)
pip install faster-whisper
```

## Step 1: Channel Discovery

Identify quant finance channels. Examples (verify rights before downloading):

| Channel | Primary Topics | Notes |
| :--- | :--- | :--- |
| Unbiased Trading | Metrics, EV, risk management | Source of retail quant model.txt |
| Robustness Playbook | Statistical arbitrage, cointegration | Academic/educational |
| QuantInsti | Broad quant education | Check TOS |
| AlgoTrading101 | Strategy development | Check TOS |
| Trading 212 | Retail trading education | Check TOS |
| Lucas Liew (Algorithmic Trading) | Strategy design | Check TOS |

## Step 2: Download Audio/Subtitles

```bash
# Download audio only (for transcription)
yt-dlp -x --audio-format mp3 --audio-quality 0 -o "downloads/%(channel)s/%(title)s.%(ext)s" <CHANNEL_URL>

# Download auto-generated subtitles (if available)
yt-dlp --write-auto-sub --sub-lang en --convert-subs srt -o "downloads/%(channel)s/%(title)s.%(ext)s" <CHANNEL_URL>

# Download all videos from a channel
yt-dlp -x --audio-format mp3 -o "downloads/%(channel)s/%(title)s.%(ext)s" <CHANNEL_URL>
```

## Step 3: Transcription with Whisper

```python
import whisper
import os

model = whisper.load_model("base")  # Options: tiny, base, small, medium, large

def transcribe_audio(audio_path: str) -> str:
    result = model.transcribe(audio_path)
    return result["text"]

def batch_transcribe(directory: str):
    for filename in os.listdir(directory):
        if filename.endswith(".mp3"):
            audio_path = os.path.join(directory, filename)
            text = transcribe_audio(audio_path)
            # Save transcription
            output_path = audio_path.replace(".mp3", ".txt")
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(text)
```

## Step 4: Topic Classification

After transcription, classify each video into the appropriate topic folder:

```python
TOPIC_KEYWORDS = {
    "01_Regime_Detection": ["hurst", "hmm", "regime", "adx", "atr", "volatility", "bocpd", "changepoint"],
    "02_Strategy_Development": ["strategy", "backtest", "walk-forward", "dsr", "cpcv", "alpha", "signal", "expectancy"],
    "03_Portfolio_Management": ["portfolio", "kelly", "risk parity", "hrp", "correlation", "allocation", "heat"],
    "04_Order_Execution": ["execution", "slippage", "spread", "market impact", "almgren", "tca", "order routing"],
    "05_Quant_Utilities": ["python", "pandas", "numpy", "data", "feature", "pipeline", "utility"]
}

def classify_topic(text: str) -> str:
    text_lower = text.lower()
    scores = {}
    for topic, keywords in TOPIC_KEYWORDS.items():
        scores[topic] = sum(1 for kw in keywords if kw in text_lower)
    return max(scores, key=scores.get) if max(scores.values()) > 0 else "99_Archive_Unsorted"
```

## Step 5: Sort into Topic Folders

```python
import shutil

def sort_transcription(source_path: str, topic: str):
    base_dir = "docs/quant_knowledge_base/"
    dest_dir = os.path.join(base_dir, topic, "transcriptions")
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, os.path.basename(source_path))
    shutil.move(source_path, dest_path)
```

## Current Status

| Step | Status | Notes |
| :--- | :--- | :--- |
| Folder Structure | ✅ Complete | 6 topic folders created |
| Source Survey | ✅ Complete | 7 source documents indexed |
| Transcription Extraction | ⏸️ Pending | Awaiting legitimate video sources |
| Code Snippet Extraction | ⏸️ Pending | Will follow transcription analysis |

## Next Actions Required

1. **You provide video sources**: Share specific YouTube URLs or channel names you have rights to download
2. **Legal clearance**: Ensure you have permission or legal right to download/transcribe
3. **Pipeline execution**: I can then run the download → transcribe → sort pipeline

## Alternative: Manual Ingestion

If you prefer not to use automated downloading:
1. Download videos/audio manually
2. Place files in `docs/quant_knowledge_base/99_Archive_Unsorted/`
3. I will transcribe and sort them automatically
