#!/usr/bin/env python3
"""
Quant Knowledge Framework CLI
==============================
Run the isolated knowledge pipeline from the command line.
"""

import argparse
import sys
import logging
from pathlib import Path
from typing import Any, Dict

from quant_framework.config import Config
from quant_framework.agent import QuantAgent
from quant_framework.pipeline import KnowledgePipeline


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def cmd_download(args: argparse.Namespace) -> int:
    from quant_framework.downloader import YouTubeDownloader

    config = Config(args.config)
    downloader = YouTubeDownloader(config)

    if args.channel:
        print(f"Downloading from channel: {args.channel}")
        videos = downloader.download_channel(args.channel, limit=args.limit)
        print(f"Downloaded {len(videos)} videos")
        return 0

    channels = [ch["url"] for ch in config.channels if ch.get("enabled", True)]
    total = 0
    for url in channels:
        print(f"Downloading from: {url}")
        videos = downloader.download_channel(url, limit=args.limit)
        total += len(videos)
    print(f"Downloaded {total} videos total")
    return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    # Route through the pipeline stage: it reuses the downloader's
    # transcript.txt output, parses only missing/stale subtitles, and only
    # loads a whisper model when real audio/video files are present.
    pipeline = KnowledgePipeline(args.config)
    result = pipeline._run_transcribe_stage(input_dir=args.input)
    print(f"Transcribed {result.items_processed} files")
    if result.errors:
        for error in result.errors:
            print(f"  ERROR: {error}")
    return 0 if result.success else 1


def cmd_classify(args: argparse.Namespace) -> int:
    config = Config(args.config)
    from quant_framework.storage import KnowledgeClassifier

    classifier = KnowledgeClassifier(config)
    input_dir = args.input or config.get("paths.transcriptions", "data/transcriptions")
    items = classifier.classify_directory(input_dir)

    for item in items:
        print(f"{item.title} -> {item.topic} ({item.confidence:.2f})")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    config = Config(args.config)
    from quant_framework.storage import KnowledgeBase

    kb = KnowledgeBase(config)
    results = kb.search(args.query)

    print(f"Found {len(results)} results for: {args.query}")
    for r in results:
        print(f"  - {r}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    config = Config(args.config)
    from quant_framework.storage import KnowledgeBase

    kb = KnowledgeBase(config)
    kb.build_index()

    print("Knowledge Base Statistics")
    print("=" * 50)
    total = 0
    for topic, files in sorted(kb.index.items()):
        print(f"  {topic}: {len(files)} files")
        total += len(files)
    print("-" * 50)
    print(f"  Total: {total} files")
    return 0


def cmd_reclassify(args: argparse.Namespace) -> int:
    config = Config(args.config)
    from quant_framework.storage import KnowledgeClassifier, KnowledgeBase

    classifier = KnowledgeClassifier(config)
    kb = KnowledgeBase(config)
    kb.build_index()

    archive_files = kb.index.get("99_Archive_Unsorted", [])
    print(f"Reclassifying {len(archive_files)} archive files...")

    moved = 0
    for file_path in archive_files:
        path = Path(file_path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            topic, confidence, keywords = classifier.classify_text(text, path.stem)
            if topic != "99_Archive_Unsorted" and confidence >= 0.3:
                dest_dir = kb.base_path / topic
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / path.name
                path.rename(dest_file)
                moved += 1
                print(f"  Moved {path.name} -> {topic}")
        except Exception as e:
            print(f"  Error: {path.name}: {e}")

    kb.build_index()
    print(f"\nReclassified {moved} files")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    pipeline = KnowledgePipeline(args.config)
    results = pipeline.run_full_pipeline(channels=args.channels)

    for stage, result in results.items():
        print(f"{stage}: processed={result.items_processed}, success={result.success}")
        for error in result.errors:
            print(f"  ERROR: {error}")
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    agent = QuantAgent(args.config)
    print("Quant Knowledge Framework - Agent Shell")
    print("=" * 50)
    print("Available commands:")
    print("  tools [category]       - Discover tools")
    print("  exec <tool> <params>   - Execute tool")
    print("  chain <json>           - Chain tools")
    print("  exit                   - Exit shell")
    print("=" * 50)

    while True:
        try:
            user_input = input("\nquant> ").strip()
            if not user_input:
                continue
            if user_input == "exit":
                break

            parts = user_input.split(maxsplit=2)
            command = parts[0].lower()

            if command == "tools":
                category = parts[1] if len(parts) > 1 else None
                tools = agent.discover_tools(category=category)
                print(f"\nFound {len(tools)} tools:")
                for tool in tools:
                    print(f"  - {tool['name']}: {tool['description']}")

            elif command == "exec":
                if len(parts) < 2:
                    print("Usage: exec <tool_name> [params_json]")
                    continue
                tool_name = parts[1]
                params: Dict[str, Any] = {}
                if len(parts) > 2:
                    import json
                    try:
                        params = json.loads(parts[2])
                    except json.JSONDecodeError:
                        if "=" in parts[2]:
                            for kv in parts[2].split():
                                if "=" in kv:
                                    k, v = kv.split("=", 1)
                                    params[k] = v
                        else:
                            params = {"query": parts[2]}
                result = agent.execute_tool(tool_name, **params)
                print(f"\nResult: {result}")

            elif command == "chain":
                if len(parts) < 2:
                    print("Usage: chain <pipeline_json>")
                    continue
                import json
                pipeline = json.loads(parts[1])
                results = agent.chain_tools(pipeline)
                print(f"\nPipeline results: {results}")

            else:
                print(f"Unknown command: {command}")

        except KeyboardInterrupt:
            print("\nUse 'exit' to quit.")
        except Exception as e:
            print(f"Error: {e}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Quant Knowledge Framework")
    parser.add_argument("--config", default="config/sources.yaml", help="Path to configuration file")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    download_parser = subparsers.add_parser("download", help="Download videos from YouTube channels")
    download_parser.add_argument("--channel", help="Specific channel to download")
    download_parser.add_argument("--limit", type=int, help="Max videos to download")

    transcribe_parser = subparsers.add_parser("transcribe", help="Transcribe audio/video files")
    transcribe_parser.add_argument("--input", help="Input directory")

    classify_parser = subparsers.add_parser("classify", help="Classify transcriptions into topics")
    classify_parser.add_argument("--input", help="Input directory")

    search_parser = subparsers.add_parser("search", help="Search the knowledge base")
    search_parser.add_argument("query", help="Search query")

    stats_parser = subparsers.add_parser("stats", help="Show knowledge base statistics")

    reclassify_parser = subparsers.add_parser("reclassify", help="Reclassify archive files")

    build_parser = subparsers.add_parser("build", help="Build complete knowledge base")
    build_parser.add_argument("--channels", nargs="*", help="Specific channels to process")

    subparsers.add_parser("agent", help="Launch interactive agent shell")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    setup_logging(args.verbose)

    commands = {
        "download": cmd_download,
        "transcribe": cmd_transcribe,
        "classify": cmd_classify,
        "search": cmd_search,
        "stats": cmd_stats,
        "reclassify": cmd_reclassify,
        "build": cmd_build,
        "agent": cmd_agent,
    }

    try:
        return commands[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
