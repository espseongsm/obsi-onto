"""Parse source spans without modifying the Markdown or interpreting its instructions."""

import hashlib
import re
from datetime import date
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from app.frontmatter import load_metadata

WIKI = re.compile(r"!?\[\[([^\[\]\n]{1,2048})\]\]")
MD_LINK = re.compile(r"!?\[[^\[\]\n]{0,2048}\]\(([^\s)]{1,2048})(?:[ \t]+[^)\n]{0,1024})?\)")
TAGS = re.compile(r"(?<![\w/#])#([\w\-/]+)")
DOMAINS = {
    "work": ("업무", "회의", "프로젝트"),
    "investment": ("투자", "매수", "매도", "주식", "기업", "ETF"),
    "personal": ("개인", "일상", "생각", "원칙"),
}


def searchable_body(text):
    return not (text.startswith("---\n") or re.fullmatch(r"(?:\s*#{1,6}\s+[^\n]+\n?)+", text))


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strings(value):
    if value is None:
        return []
    return [str(v) for v in (value if isinstance(value, list) else [value])]


def iso_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (ValueError, TypeError):
        return None


def search_tokens(text):
    """Keep words and Hangul bigrams so 조사 variants and two-syllable terms can match."""
    words = re.findall(r"[\w]+", text.lower())
    grams = [
        word[i : i + 2]
        for word in words
        if re.fullmatch(r"[가-힣]+", word)
        for i in range(len(word) - 1)
    ]
    return " ".join(words + grams)


def links_in(text, start_line):
    result = []
    for pattern, kind in ((WIKI, "wiki"), (MD_LINK, "markdown")):
        for match in pattern.finditer(text):
            raw = unquote(match.group(1).split("|")[0].strip().strip("<>"))
            scheme = urlsplit(raw).scheme
            if scheme and scheme not in {"http", "https"}:
                continue
            target, _, fragment = raw.partition("#")
            result.append(
                {
                    "raw": raw,
                    "target": target,
                    "fragment": fragment,
                    "kind": "external" if scheme else kind,
                    "line": start_line + text[: match.start()].count("\n"),
                }
            )
    return result


def parse_markdown(text, path, daily_pattern=r"^\d{4}-\d{2}-\d{2}$"):
    lines = text.splitlines()
    if any(len(line) > 16_384 for line in lines):
        raise ValueError("Each line in a note must be no longer than 16,384 characters.")
    metadata, offset = {}, 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("Frontmatter is missing its closing --- delimiter.")
        metadata = load_metadata("\n".join(lines[1:end]))
        offset = end + 1
    title = str(metadata.get("title") or PurePosixPath(path).stem)
    aliases = strings(metadata.get("aliases", metadata.get("alias")))
    tags = strings(metadata.get("tags"))
    record_date = iso_date(metadata.get("date"))
    date_source = "frontmatter.date" if record_date else None
    stem = PurePosixPath(path).stem
    if not record_date and daily_pattern and re.fullmatch(daily_pattern, stem):
        record_date, date_source = iso_date(stem), "configured_filename"
    sections, heading, pending, first, fenced = [], "", [], offset + 1, False

    def flush():
        nonlocal pending
        if not pending:
            return
        body = "\n".join(pending)
        searchable_body = re.sub(r"`[^`]*`", lambda m: re.sub(r"[^\n]", " ", m.group()), body)
        if fenced:
            searchable_body = ""
        section_tags = sorted(set(tags + TAGS.findall(searchable_body)))
        explicit_domains = strings(metadata.get("domain"))
        scope = heading + " " + " ".join(section_tags)
        domains = [
            d
            for d, terms in DOMAINS.items()
            if d in explicit_domains or any(t in scope for t in terms)
        ]
        sections.append(
            {
                "heading": heading,
                "start": first,
                "end": first + len(pending) - 1,
                "text": body,
                "hash": digest(body),
                "tags": section_tags,
                "domains": domains,
                "links": links_in(searchable_body, first),
            }
        )
        pending = []

    if offset:
        pending, first = lines[:offset], 1
        flush()
    for index in range(offset, len(lines)):
        line = lines[index]
        if line.lstrip().startswith(("```", "~~~")):
            flush()
            fenced = not fenced
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", line) if not fenced else None
        if match:
            flush()
            heading = match.group(2)
        if not line.strip():
            flush()
            continue
        if pending and sum(len(s) + 1 for s in pending) + len(line) > 600:
            flush()
        if not pending:
            first = index + 1
        pending.append(line)
    flush()
    return {
        "title": title,
        "aliases": aliases,
        "tags": tags,
        "metadata": metadata,
        "record_date": record_date,
        "date_source": date_source,
        "sections": sections,
    }
