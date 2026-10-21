"""Build a side-by-side review sheet from a K-7 report: each answer next to what was expected."""

import html
import json
import re
import sys
from pathlib import Path

report_path, cases_path, out_path = map(Path, sys.argv[1:4])
report = json.loads(report_path.read_text())
spec = {c["id"]: c for c in json.loads(cases_path.read_text())["cases"]}
FENCE = re.compile(r"^```[ \t]*([\w+-]*)[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)
REVIEW_CHECKS = {"toolCallsWithinCaseLimit", "answerMustNotMatch"}


def esc(text: object) -> str:
    return html.escape(str(text or ""))


def asked(case_id: str) -> str:
    req = spec[case_id]["request"]
    parts = []
    if req.get("subject"):
        parts.append(f"<p><b>Subject:</b> <code>{esc(req['subject'])}</code></p>")
    msg = req.get("userMessage") or "(no message)"
    parts.append(f"<p class=msg>{esc(msg)}</p>")
    for att in req.get("attachments") or []:
        content = att.get("content")
        if isinstance(content, list):
            content = "\n".join(content)
        parts.append(
            f"<details><summary>{esc(att.get('category'))}: {esc(att.get('name'))}</summary>"
            f"<pre>{esc(content)}</pre></details>"
        )
    return "".join(parts)


cards, index = [], []
for c in report["cases"]:
    if not (c.get("review") or {}).get("required"):
        continue
    cid = c["id"]
    checks = (c.get("automated") or {}).get("checks", [])
    flags = [ch for ch in checks if not ch["passed"]]
    flag_html = "".join(
        f"<li class={'warn' if ch['name'] in REVIEW_CHECKS else 'bad'}>"
        f"<b>{esc(ch['name'])}</b> {esc(ch['detail'])}</li>"
        for ch in flags
    )
    gates = c.get("gates") or []
    gate_tags = "".join(f"<span class='tag {esc(g)}'>{esc(g)}</span>" for g in gates) or (
        "<span class=tag>no gate</span>"
    )
    rubric = "".join(f"<li>{esc(r)}</li>" for r in c.get("rubric") or [])
    tools = c.get("toolCalls") or []
    tools_html = ""
    if tools:
        rows = "".join(
            f"<tr><td>{n}</td><td><code>{esc(t['operation'])}</code></td><td>{esc(t['status'])}"
            f"{' ' + esc(t['errorCode']) if t.get('errorCode') else ''}</td>"
            f"<td>{esc(t.get('rowCount'))}</td><td>{esc(t.get('why'))}</td></tr>"
            for n, t in enumerate(tools, 1)
        )
        tools_html = (
            f"<details><summary>Lookups ({len(tools)})</summary><div class=scroll><table>"
            "<tr><th>#</th><th>Operation</th><th>Status</th><th>Rows</th><th>Why</th></tr>"
            f"{rows}</table></div></details>"
        )
    proposal = c.get("proposal")
    prop_html = ""
    if proposal:
        captured = (proposal.get("proposedText") or "").strip()
        blocks = [m.group(2).strip() for m in FENCE.finditer(c.get("answer") or "")]
        note = (
            "<p class=bad-note>The harness captured this from <b>outside</b> a code block "
            "(a proposal-parsing bug, not the model). Judge the code in the answer instead.</p>"
            if captured not in blocks
            else "<p class=meta>This is the first SQL/PL/SQL block in the answer.</p>"
            if len(blocks) > 1
            else ""
        )
        prop_html = (
            "<h4>Proposal as captured by the harness</h4>"
            f"{note}<pre class=code>{esc(captured)}</pre>"
        )
    seconds = round((c.get("latencyMs") or 0) / 1000)
    index.append(f"<a href='#{esc(cid)}' class='{'flag' if flags else ''}'>{esc(cid)}</a>")
    cards.append(f"""
<section class=case id="{esc(cid)}" data-gates="{esc(" ".join(gates) or "none")}">
  <header>
    <h2>{esc(cid)}</h2>
    <span class=meta>{esc(c.get("category"))} · {esc(c.get("action"))} · {seconds}s</span>
    {gate_tags}
  </header>
  <div class=cols>
    <div class=expect>
      <h3>Asked</h3>{asked(cid)}
      <h3>Expected behaviour</h3><p>{esc(c.get("expectedBehavior"))}</p>
      <h3>Rubric</h3><ul class=rubric>{rubric}</ul>
      {"<h3>Checks for the reviewer</h3><ul class=flags>" + flag_html + "</ul>" if flags else ""}
      {tools_html}
    </div>
    <div class=answer>
      <h3>Answer</h3>
      <pre class=ans>{esc(c.get("answer"))}</pre>
      {prop_html}
    </div>
  </div>
</section>""")

run = report.get("harness", {})
case_set = report["caseSet"]
if not isinstance(case_set, str):
    case_set = case_set.get("version")
page = f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>K-7 Review Sheet</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --fg:#1d1d1b; --muted:#6b6b66; --line:#e2e1dc;
  --code:#f1f0ec; --safety:#b42318; --correct:#175cd3; --warn:#b54708; --bad:#b42318; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#161615; --card:#1f1f1d; --fg:#ecebe6; --muted:#a19f97; --line:#34332f;
  --code:#2a2926; --safety:#f97066; --correct:#84adff; --warn:#fdb022; --bad:#f97066; }} }}
:root[data-theme="dark"] {{ --bg:#161615; --card:#1f1f1d; --fg:#ecebe6; --muted:#a19f97;
  --line:#34332f; --code:#2a2926; --safety:#f97066; --correct:#84adff;
  --warn:#fdb022; --bad:#f97066; }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif }}
main {{ max-width:1400px; margin:0 auto; padding:16px }}
h1 {{ font-size:22px; margin:8px 0 4px }}
.sub {{ color:var(--muted); margin:0 0 12px }}
nav {{ position:sticky; top:0; z-index:2; background:var(--bg); padding:8px 0;
  border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap; gap:6px; align-items:center }}
nav a {{ font:12px ui-monospace,monospace; color:var(--fg); text-decoration:none;
  padding:2px 6px; border:1px solid var(--line); border-radius:4px }}
nav a.flag {{ border-color:var(--warn); color:var(--warn) }}
nav label {{ font-size:13px; margin-right:8px; color:var(--muted) }}
.case {{ background:var(--card); border:1px solid var(--line); border-radius:8px;
  margin:16px 0; padding:16px; scroll-margin-top:110px }}
.case header {{ display:flex; flex-wrap:wrap; gap:8px; align-items:baseline }}
.case h2 {{ margin:0; font:600 18px ui-monospace,monospace }}
.meta {{ color:var(--muted); font-size:13px }}
.tag {{ font-size:12px; padding:1px 8px; border-radius:10px; border:1px solid var(--line) }}
.tag.safety {{ color:var(--safety); border-color:var(--safety) }}
.tag.correctness {{ color:var(--correct); border-color:var(--correct) }}
.cols {{ display:grid; grid-template-columns:minmax(0,2fr) minmax(0,3fr);
  gap:20px; margin-top:8px }}
@media (max-width:860px) {{ .cols {{ grid-template-columns:minmax(0,1fr) }} }}
h3 {{ font-size:13px; text-transform:uppercase; letter-spacing:.04em;
  color:var(--muted); margin:14px 0 4px }}
h4 {{ font-size:14px; margin:14px 0 4px }}
.msg {{ font-style:italic }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:var(--code); padding:10px;
  border-radius:6px; font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; margin:4px 0 }}
pre.ans {{ max-height:70vh; overflow:auto }}
ul {{ padding-left:20px; margin:4px 0 }}
.flags li.warn {{ color:var(--warn) }} .flags li.bad {{ color:var(--bad) }}
details {{ margin:6px 0 }} summary {{ cursor:pointer; color:var(--muted); font-size:13px }}
.scroll {{ overflow-x:auto }}
table {{ border-collapse:collapse; font-size:12px; width:100% }}
td,th {{ border-bottom:1px solid var(--line); padding:3px 6px;
  text-align:left; vertical-align:top }}
code {{ font-family:ui-monospace,monospace; font-size:.92em }}
.hidden {{ display:none }}
.bad-note {{ color:var(--bad); font-size:13px; margin:4px 0 }}
</style></head><body><main>
<h1>K-7 review sheet</h1>
<p class=sub>{esc(report_path.name)} · case set {esc(case_set)}
· commit {esc(str(run.get("commit", ""))[:7])} ·
{len(cards)} cases need a review.
Verdicts go in the report's <code>review</code> fields, not here.</p>
<nav>
  <label><input type=radio name=g value=all checked> all</label>
  <label><input type=radio name=g value=correctness> correctness</label>
  <label><input type=radio name=g value=safety> safety</label>
  <label><input type=radio name=g value=none> no gate</label>
  {"".join(index)}
</nav>
{"".join(cards)}
</main>
<script>
document.querySelectorAll('input[name=g]').forEach(r => r.addEventListener('change', () => {{
  const g = r.value;
  document.querySelectorAll('.case').forEach(s => s.classList.toggle('hidden',
    g !== 'all' && !s.dataset.gates.split(' ').includes(g)));
}}));
</script>
</body></html>"""
out_path.write_text(page)
print(out_path, len(cards), "cases", len(page) // 1024, "KB")
