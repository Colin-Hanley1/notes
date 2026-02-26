#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime, date

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
STAGING_DIR = REPO_ROOT / "notes_staging"
OUT_DIR = REPO_ROOT / "notes"  # generated .qmd files live here

META_LINE_RE = re.compile(r"^\s*%\s*([A-Za-z0-9_\-]+)\s*:\s*(.*?)\s*$")

@dataclass(frozen=True)
class Note:
    title: str
    date: Optional[str]
    tags: List[str]
    topic: str
    course: str
    slug: str
    src: Path
    out: Path  # .qmd path (absolute)

def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "note"

def safe_segment(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "_", s)
    return s or "unknown"

def parse_tex_metadata(tex_path: Path) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    with tex_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip() == "":
                continue
            if not line.lstrip().startswith("%"):
                break
            m = META_LINE_RE.match(line)
            if m:
                meta[m.group(1).strip().lower()] = m.group(2).strip()
    return meta

def infer_topic_course(tex_path: Path) -> Tuple[str, str]:
    rel = tex_path.relative_to(STAGING_DIR)
    parts = rel.parts
    if len(parts) < 3:
        die(f"Expected notes_staging/<topic>/<course>/<file>.tex but got: {tex_path}")
    return parts[0], parts[1]

def parse_note_date(d: Optional[str]) -> date:
    """
    Parse YYYY-MM-DD into a date. If missing/invalid, return minimal date so it sorts last.
    """
    if not d:
        return date.min
    try:
        return datetime.strptime(d.strip(), "%Y-%m-%d").date()
    except Exception:
        return date.min
    
def require_pandoc() -> None:
    try:
        subprocess.run(["pandoc", "--version"], check=True, capture_output=True, text=True)
    except Exception:
        die("pandoc not found. Install pandoc and ensure it's on PATH.")

def find_tex_files() -> List[Path]:
    if not STAGING_DIR.exists():
        die(f"Missing staging directory: {STAGING_DIR}")
    return sorted(STAGING_DIR.rglob("*.tex"))

def build_notes_index(tex_files: List[Path]) -> List[Note]:
    notes: List[Note] = []
    for p in tex_files:
        meta = parse_tex_metadata(p)
        topic_raw, course_raw = infer_topic_course(p)

        topic = safe_segment(topic_raw)
        course = safe_segment(course_raw)

        title = meta.get("title") or p.stem.replace("_", " ").replace("-", " ").title()
        date = meta.get("date")
        tags = [t.strip() for t in meta.get("tags", "").split(",") if t.strip()]

        slug = slugify(title)
        out = OUT_DIR / topic / course / f"{slug}.qmd"

        notes.append(Note(
            title=title,
            date=date,
            tags=tags,
            topic=topic,
            course=course,
            slug=slug,
            src=p,
            out=out,
        ))
    return notes

def clean_generated_output() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

def tex_to_qmd(note: Note) -> None:
    note.out.parent.mkdir(parents=True, exist_ok=True)

    tmp_md = note.out.with_suffix(".md.tmp")

    # Convert LaTeX -> Markdown while preserving TeX math ($...$, $$...$$)
    cmd = [
        "pandoc",
        str(note.src),
        "--from=latex",
        "--to=commonmark_x+tex_math_dollars",
        "--wrap=none",
        "-o", str(tmp_md),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        die(f"Pandoc failed for {note.src}\n\nSTDERR:\n{p.stderr}")

    body = tmp_md.read_text(encoding="utf-8")
    tmp_md.unlink(missing_ok=True)

    # Quarto front matter: KaTeX renders math during build (stable on GitHub Pages)
    frontmatter = {
        "title": note.title,
        "date": note.date,
        "tags": note.tags,
        "format": {"html": {"html-math-method": "katex"}},
    }

    fm = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False).strip() + "\n---\n\n"
    note.out.write_text(fm + body, encoding="utf-8")

def generate_quarto_yml(notes: List[Note]) -> None:
    tree: Dict[str, Dict[str, List[Note]]] = {}
    for n in notes:
        tree.setdefault(n.topic, {}).setdefault(n.course, []).append(n)

    for topic in tree:
        for course in tree[topic]:
            tree[topic][course].sort(
                key=lambda x: (parse_note_date(x.date), x.title.lower()),
                reverse=False
            )

        sidebar_contents = [{"text": "Home", "href": "index.qmd"}]

    for topic in sorted(tree.keys()):
        topic_section = {"section": topic.replace("_", " "), "contents": []}
        for course in sorted(tree[topic].keys()):
            course_section = {"section": course.replace("_", " "), "contents": []}
            for n in tree[topic][course]:
                rel = n.out.relative_to(REPO_ROOT).as_posix()
                course_section["contents"].append({"text": n.title, "href": rel})
            topic_section["contents"].append(course_section)
        sidebar_contents.append(topic_section)

    q = {
        "project": {"type": "website"},
        "website": {
            "title": "Personal Notes",
            "sidebar": {"style": "docked", "search": True, "contents": sidebar_contents},
            "page-navigation": True,
        },
        "format": {
            "html": {
                "theme": "cosmo",
                "css": "styles.css",
                "toc": True,
                "html-math-method": "katex",
            }
        }
    }

    (REPO_ROOT / "_quarto.yml").write_text(
        yaml.safe_dump(q, sort_keys=False, width=120),
        encoding="utf-8"
    )

def write_homepage(notes: List[Note]) -> None:
    notes_sorted = sorted(
        notes,
        key=lambda n: (parse_note_date(n.date), n.title.lower()),
        reverse=True
    )
    lines = []
    lines.append("---")
    lines.append("title: Home")
    lines.append("format:")
    lines.append("  html:")
    lines.append("    toc: false")
    lines.append("---\n")
    lines.append("# Personal Notes\n")
    lines.append("Browse using the sidebar (Topic → Class → Note).")
    lines.append("\n## Recent notes\n")

    for n in notes_sorted[:30]:
        rel = n.out.relative_to(REPO_ROOT).as_posix()
        date = f" — {n.date}" if n.date else ""
        lines.append(f"- [{n.title}]({rel}){date}")

    (REPO_ROOT / "index.qmd").write_text("\n".join(lines) + "\n", encoding="utf-8")

def copy_note_assets(note: Note) -> None:
    """
    Copies any assets folder next to the .tex file into the corresponding
    generated notes/ topic/course/ directory so Quarto can find them.
    Convention: assets live in a sibling folder named 'images' or 'assets'.
    """
    for folder_name in ("images", "assets"):
        src_dir = note.src.parent / folder_name
        if src_dir.exists() and src_dir.is_dir():
            dst_dir = note.out.parent / folder_name
            if dst_dir.exists():
                shutil.rmtree(dst_dir)
            shutil.copytree(src_dir, dst_dir)
            
def main() -> None:
    require_pandoc()
    tex_files = find_tex_files()
    if not tex_files:
        die(f"No .tex files found under {STAGING_DIR}")

    notes = build_notes_index(tex_files)
    clean_generated_output()

    for n in notes:
        tex_to_qmd(n)
        copy_note_assets(n)

    write_homepage(notes)
    generate_quarto_yml(notes)

    print(f"Generated {len(notes)} notes into {OUT_DIR}/ and wrote _quarto.yml + index.qmd")

if __name__ == "__main__":
    main()