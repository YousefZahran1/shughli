"""Import a CV file -> structured profile.

1. Pull raw text from PDF / DOCX / TXT.
2. Ask the configured AI backend to structure it into the profile schema.
Nothing is invented: the model is told to extract only what's present.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import engine, profile as profile_mod


def extract_text(path: str | Path) -> str:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".txt" or ext == ".md":
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".docx":
        from docx import Document
        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError("pypdf not installed — run: pip install pypdf") from e
        reader = PdfReader(str(path))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    raise ValueError(f"Unsupported CV format: {ext}. Use PDF, DOCX, or TXT.")


_PROMPT = """Extract this CV into structured JSON. Use ONLY information present in the
CV — do not invent skills, employers, metrics, or dates. If a field is absent,
use an empty string or empty list.

Return ONLY this JSON shape:
{{
  "personal": {{"name":"","email":"","phone":"","location":"","links":[]}},
  "nationality": "",
  "summary": "",
  "skills": [{{"category":"","items":""}}],
  "experience": [{{"title":"","org":"","location":"","dates":"","bullets":[]}}],
  "projects": [{{"name":"","stack":"","dates":"","bullets":[]}}],
  "education": [{{"degree":"","institution":"","dates":"","detail":""}}],
  "certifications": [],
  "languages": []
}}

CV text:
<cv>
{cv}
</cv>"""


def structure_cv(raw_text: str, cfg: dict | None = None) -> dict:
    if not raw_text.strip():
        raise RuntimeError("No text could be read from that file.")
    data = engine.generate_json(_PROMPT.format(cv=raw_text[:16000]), cfg=cfg, task="fast")
    if not isinstance(data, dict):
        raise engine.EngineError("CV import returned an unexpected shape.")
    # Merge into a full, valid profile; keep preferences/work_auth defaults.
    base = json.loads(json.dumps(profile_mod.EMPTY_PROFILE))
    for key in ("personal", "nationality", "summary", "skills", "experience",
                "projects", "education", "certifications", "languages"):
        if key in data and data[key]:
            base[key] = data[key]
    base["meta"]["imported"] = True
    return profile_mod.normalize_profile(base)


def import_cv_file(path: str | Path, cfg: dict | None = None) -> dict:
    raw = extract_text(path)
    prof = structure_cv(raw, cfg=cfg)
    prof["meta"]["source_cv"] = str(Path(path).name)
    return prof
