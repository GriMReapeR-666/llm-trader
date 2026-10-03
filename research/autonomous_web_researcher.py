"""Autonomous web researcher: search SSRN, ArXiv, web, and Fed RSS."""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None  # type: ignore


@dataclass
class ResearchItem:
    """Single research finding."""
    title: str = ""
    authors: str = ""
    abstract: str = ""
    url: str = ""
    source: str = ""
    published: str = ""
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract[:500],
            "url": self.url,
            "source": self.source,
            "published": self.published,
            "score": round(self.score, 3),
        }


class AutonomousWebResearcher:
    """Searches multiple academic/web sources for research on a topic."""

    def __init__(self, timeout: int = 15):
        self._timeout = timeout

    def search_ssrn(self, query: str, max_results: int = 10) -> List[ResearchItem]:
        """Search SSRN for papers matching query."""
        if requests is None:
            return []
        try:
            url = f"https://papers.ssrn.com/sol3/results.cfm?txtKey_Words={quote_plus(query)}&npage=1"
            resp = requests.get(url, timeout=self._timeout, headers={"User-Agent": "Mozilla/5.0"})
            items = []
            # Simple regex extraction from SSRN HTML
            titles = re.findall(r'class="title".*?>(.*?)</a>', resp.text, re.DOTALL)
            for t in titles[:max_results]:
                items.append(ResearchItem(
                    title=re.sub(r"<[^>]+>", "", t).strip(),
                    source="SSRN",
                    url=url,
                ))
            return items
        except Exception as exc:
            logger.debug("SSRN search failed: %s", exc)
            return []

    def search_arxiv(self, query: str, max_results: int = 10) -> List[ResearchItem]:
        """Search ArXiv via Atom API."""
        if requests is None:
            return []
        try:
            url = f"http://export.arxiv.org/api/query?search_query=all:{quote_plus(query)}&start=0&max_results={max_results}"
            resp = requests.get(url, timeout=self._timeout)
            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = []
            for entry in root.findall("atom:entry", ns):
                title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
                authors = ", ".join(a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns))
                abstract = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
                link = ""
                for l in entry.findall("atom:link", ns):
                    if l.get("type") == "text/html":
                        link = l.get("href", "")
                        break
                if not link:
                    link = (entry.findtext("atom:id", "", ns) or "")
                published = (entry.findtext("atom:published", "", ns) or "")[:10]
                items.append(ResearchItem(
                    title=title, authors=authors, abstract=abstract,
                    url=link, source="ArXiv", published=published,
                ))
            return items
        except Exception as exc:
            logger.debug("ArXiv search failed: %s", exc)
            return []

    def search_web(self, query: str, max_results: int = 10) -> List[ResearchItem]:
        """Search web via DuckDuckGo Lite (no API key needed)."""
        if requests is None:
            return []
        try:
            url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
            resp = requests.get(url, timeout=self._timeout, headers={"User-Agent": "Mozilla/5.0"})
            # Extract links from lite HTML
            links = re.findall(r'class="result-link".*?href="(.*?)"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
            items = []
            for href, title in links[:max_results]:
                items.append(ResearchItem(
                    title=re.sub(r"<[^>]+>", "", title).strip(),
                    url=href,
                    source="Web",
                ))
            return items
        except Exception as exc:
            logger.debug("Web search failed: %s", exc)
            return []

    def fetch_macro_rss(self, max_entries: int = 10) -> List[ResearchItem]:
        """Fetch Federal Reserve RSS feeds for macro research."""
        if requests is None:
            return []
        feeds = [
            "https://www.federalreserve.gov/feeds/press_all.xml",
        ]
        items = []
        for feed_url in feeds:
            try:
                resp = requests.get(feed_url, timeout=self._timeout)
                root = ET.fromstring(resp.text)
                for entry in root.findall(".//item")[:max_entries]:
                    title = (entry.findtext("title") or "").strip()
                    desc = (entry.findtext("description") or "").strip()
                    link = (entry.findtext("link") or "").strip()
                    pub = (entry.findtext("pubDate") or "").strip()
                    items.append(ResearchItem(
                        title=title, abstract=desc, url=link,
                        source="FederalReserve", published=pub,
                    ))
            except Exception as exc:
                logger.debug("Fed RSS fetch failed for %s: %s", feed_url, exc)
        return items

    def investigate(self, query: str, max_per_source: int = 5) -> Dict[str, Any]:
        """Run multi-source investigation and return combined results."""
        results: Dict[str, Any] = {}
        for source_name, method in [
            ("ssrn", self.search_ssrn),
            ("arxiv", self.search_arxiv),
            ("web", self.search_web),
            ("fed", self.fetch_macro_rss),
        ]:
            try:
                if source_name == "fed":
                    items = method(max_entries=max_per_source)
                else:
                    items = method(query, max_results=max_per_source)
                results[source_name] = [i.to_dict() for i in items]
            except Exception as exc:
                results[source_name] = {"error": str(exc)}
        results["query"] = query
        return results
