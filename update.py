#!/usr/bin/env python3
"""
Iran Conflict Tracker — Daily Update Script
Runs via GitHub Actions daily at 5am MT (12:00 UTC).
Uses Claude API with web search to refresh the page content.
"""

import os
import re
import json
import html as html_module
import shutil
from datetime import datetime, timezone, timedelta

import anthropic

# ── Config ──────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(REPO_ROOT, "index.html")
HISTORY_DIR = os.path.join(REPO_ROOT, "history")
HISTORY_INDEX = os.path.join(HISTORY_DIR, "history_entries.json")

MT = timezone(timedelta(hours=-7))  # Mountain Time (MDT, UTC-7)
today = datetime.now(MT).strftime("%Y-%m-%d")
today_display = datetime.now(MT).strftime("%B %d, %Y")

# ── Security: sanitize HTML from LLM before injecting into pages ─────────────
def sanitize_html(content: str) -> str:
    """Remove dangerous HTML that could enable XSS attacks."""
    if not isinstance(content, str):
        return ""
    # Strip <script>...</script> blocks (including multi-line)
    content = re.sub(r'<script[\s\S]*?</script>', '', content, flags=re.IGNORECASE)
    # Strip inline event handlers (onclick, onload, onerror, etc.)
    content = re.sub(r'\s+on\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]*)', '', content, flags=re.IGNORECASE)
    # Strip javascript: and data: URIs in href/src attributes
    content = re.sub(r'(href|src)\s*=\s*["\']?\s*(javascript|data):[^"\'>\s]*["\']?', '', content, flags=re.IGNORECASE)
    # Strip <iframe>, <object>, <embed>, <form> tags entirely
    content = re.sub(r'<(iframe|object|embed|form|base|meta|link)[^>]*>[\s\S]*?</\1>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'<(iframe|object|embed|form|base|meta|link)[^>]*/?>',  '', content, flags=re.IGNORECASE)
    return content

# ── Claude call ──────────────────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM = """You are a non-partisan research assistant updating a balanced, factual HTML document
about the US-Israel conflict with Iran. You have access to web search.

RULES:
- Present facts and attributed quotes only — no personal opinions
- Always represent both supporting and opposing viewpoints fairly
- Update sections only when there are new, credible developments
- Keep HTML structure and CSS class names exactly as-is
- Return ONLY the JSON object described — no extra text
"""

PROMPT = f"""Today is {today_display}. Search for the latest news on the US-Israel-Iran conflict
(Operation Epic Fury / Operation Roaring Lion, started February 28 2026).

Search for:
1. Latest battlefield/diplomatic developments since yesterday
2. Any new international government reactions (supportive OR critical)
3. Has the expert/international consensus shifted?
4. Any new significant pros or cons of the war being debated by analysts

Return a JSON object with these keys (only include keys where you have new information):

{{
  "background_update": "<updated <p> tags for the background section, or null if no change>",
  "pros_update": "<updated point divs for the pros card, or null if no change>",
  "cons_update": "<updated point divs for the cons card, or null if no change>",
  "reactions_update": "<updated reactions-grid div contents, or null if no change>",
  "consensus_position": <integer 0-100 where 0=fully supported, 100=fully opposed, or null if no change>,
  "consensus_verdict": "<short verdict string like 'Leaning Opposed (~70%)', or null>",
  "consensus_verdict_sub": "<one sentence explanation, or null>",
  "consensus_breakdown": "<updated breakdown chips HTML, or null>",
  "uncertain_update": "<updated <p> tags for the uncertain section, or null if no change>",
  "sources_update": "<updated <a> tags for sources, or null if no change>",
  "summary_of_changes": "<1-2 sentence plain English summary of what changed today>"
}}
"""

print("Calling Claude with web search...")
response = client.messages.create(
    model="claude-opus-4-5-20251101",
    max_tokens=4096,
    system=SYSTEM,
    tools=[{"type": "web_search_20250305", "name": "web_search"}],
    messages=[{"role": "user", "content": PROMPT}],
)

# Extract text content from response
raw = ""
for block in response.content:
    if hasattr(block, "text"):
        raw += block.text

# Parse JSON (handle possible markdown fences)
json_match = re.search(r'\{[\s\S]*\}', raw)
if not json_match:
    print("ERROR: Could not find JSON in Claude response")
    print("Raw response:", raw[:500])
    exit(1)

updates = json.loads(json_match.group())
print(f"Changes: {updates.get('summary_of_changes', 'None reported')}")

# ── Load current HTML ────────────────────────────────────────────────────────
with open(INDEX_HTML, "r", encoding="utf-8") as f:
    html = f.read()

def replace_between(html, begin_tag, end_tag, new_content):
    pattern = rf'({re.escape(begin_tag)})([\s\S]*?)({re.escape(end_tag)})'
    replacement = rf'\1\n{new_content}\n\3'
    return re.sub(pattern, replacement, html)

# Apply updates (all LLM-sourced HTML is sanitized before injection)
if updates.get("background_update"):
    html = replace_between(html, "<!-- BEGIN_BACKGROUND -->", "<!-- END_BACKGROUND -->", sanitize_html(updates["background_update"]))

if updates.get("pros_update"):
    html = replace_between(html, "<!-- BEGIN_PROS -->", "<!-- END_PROS -->", sanitize_html(updates["pros_update"]))

if updates.get("cons_update"):
    html = replace_between(html, "<!-- BEGIN_CONS -->", "<!-- END_CONS -->", sanitize_html(updates["cons_update"]))

if updates.get("reactions_update"):
    html = replace_between(html, "<!-- BEGIN_REACTIONS -->", "<!-- END_REACTIONS -->", sanitize_html(updates["reactions_update"]))

if updates.get("uncertain_update"):
    html = replace_between(html, "<!-- BEGIN_UNCERTAIN -->", "<!-- END_UNCERTAIN -->", sanitize_html(updates["uncertain_update"]))

if updates.get("sources_update"):
    html = replace_between(html, "<!-- BEGIN_SOURCES -->", "<!-- END_SOURCES -->", sanitize_html(updates["sources_update"]))

if updates.get("consensus_position") is not None:
    raw_pos = updates["consensus_position"]
    # Validate it's an integer in 0-100 range before injecting into CSS
    if isinstance(raw_pos, (int, float)) and 0 <= int(raw_pos) <= 100:
        pos = int(raw_pos)
        html = re.sub(r'<!-- METER_POSITION: \d+% -->', f'<!-- METER_POSITION: {pos}% -->', html)
        html = re.sub(r'style="left: \d+%;"', f'style="left: {pos}%;"', html)
    else:
        print(f"WARNING: Skipping invalid consensus_position value: {raw_pos!r}")

if updates.get("consensus_verdict"):
    verdict_html = f'''<div class="meter-value-label">
      <div class="verdict">{html_module.escape(str(updates["consensus_verdict"]))}</div>
      <div class="verdict-sub">{html_module.escape(str(updates.get("consensus_verdict_sub", "")))}</div>
    </div>'''
    html = replace_between(html, "<!-- BEGIN_CONSENSUS_VERDICT -->", "<!-- END_CONSENSUS_VERDICT -->", verdict_html)

if updates.get("consensus_breakdown"):
    html = replace_between(html, "<!-- BEGIN_CONSENSUS_BREAKDOWN -->", "<!-- END_CONSENSUS_BREAKDOWN -->", sanitize_html(updates["consensus_breakdown"]))

# Update last-updated timestamp
html = html.replace("<!-- LAST_UPDATED -->", today_display)
html = re.sub(r'Last updated: <span id="last-updated-date">[^<]*</span>',
              f'Last updated: <span id="last-updated-date">{today_display}</span>', html)

# ── Save main index.html ─────────────────────────────────────────────────────
with open(INDEX_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"Updated index.html")

# ── Save dated snapshot ──────────────────────────────────────────────────────
snapshot_path = os.path.join(HISTORY_DIR, f"{today}.html")
shutil.copy(INDEX_HTML, snapshot_path)
print(f"Saved snapshot: history/{today}.html")

# ── Update history index ─────────────────────────────────────────────────────
entries = []
if os.path.exists(HISTORY_INDEX):
    with open(HISTORY_INDEX, "r") as f:
        entries = json.load(f)

# Add today if not already present
if not any(e["date"] == today for e in entries):
    entries.insert(0, {
        "date": today,
        "display": today_display,
        "summary": updates.get("summary_of_changes", "Daily update")
    })

with open(HISTORY_INDEX, "w") as f:
    json.dump(entries, f, indent=2)

# Rebuild history/index.html
snapshot_items = ""
for entry in entries:
    # Sanitize fields: date used in href (must be safe path), display/summary rendered as text
    safe_date = re.sub(r'[^0-9\-]', '', str(entry["date"]))  # only digits and hyphens
    safe_display = html_module.escape(str(entry["display"]))
    safe_summary = html_module.escape(str(entry["summary"]))
    snapshot_items += f'''    <li>
      <a href="{safe_date}.html">
        <div>
          <div class="snap-date">{safe_display}</div>
          <div class="snap-label">{safe_summary}</div>
        </div>
        <span class="snap-arrow">→</span>
      </a>
    </li>\n'''

history_html_path = os.path.join(HISTORY_DIR, "index.html")
with open(history_html_path, "r", encoding="utf-8") as f:
    history_html = f.read()

history_html = re.sub(
    r'<!-- SNAPSHOT_ENTRIES -->[\s\S]*?(?=\s*</ul>)',
    f'<!-- SNAPSHOT_ENTRIES -->\n{snapshot_items}',
    history_html
)

with open(history_html_path, "w", encoding="utf-8") as f:
    f.write(history_html)

print(f"History index updated with {len(entries)} entries.")
print("Done!")
