"""Write the Arabic rulebook out of the condition register.

The register in ``services/sharia_conditions.py`` is the only place a condition is
written. This turns it into the document the product owner reads and approves, so the
two can never disagree — a rule that is not in the register cannot appear in the
document, and a rule the register carries cannot be quietly left out of it.

    .venv/Scripts/python scripts/export_sharia_conditions.py            # check only
    .venv/Scripts/python scripts/export_sharia_conditions.py --write    # write the file
    .venv/Scripts/python scripts/export_sharia_conditions.py --html out.html

``--check`` is what the invariant test runs. It fails when the document on disk is not
what the register would produce right now, which is the only way to notice that somebody
edited the document instead of the register.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_market_monitor.services.sharia_conditions import (  # noqa: E402
    CONDITIONS,
    EVIDENCE_KIND_AR,
    FAMILY_TITLE_AR,
    Agreement,
    Condition,
    Detection,
    Family,
    Status,
    decision_for,
    register_summary,
    status_of,
)

DOC = ROOT / "docs" / "SHARIA_SCREENING_CONDITIONS_AR.md"

STATUS_AR = {
    Status.APPROVED: "مطبق",
    Status.PROPOSED: "مقترح — لا يطبق",
    Status.REJECTED: "مرفوض",
}

AGREEMENT_AR = {
    Agreement.UNANIMOUS: "محل اتفاق",
    Agreement.MAJORITY: "قول الجمهور",
    Agreement.DISPUTED: "محل خلاف",
}

DETECTION_AR = {
    Detection.TEXT: "يكتشف آلياً من صفحات المشروع",
    Detection.MANUAL: "خارج مدى الفحص — يُتخطى",
    Detection.NUMERIC: "خارج مدى الفحص — يحتاج أرقاماً لا نجمعها",
}


def _preamble() -> list[str]:
    summary = register_summary()
    approved = summary["by_status"]["approved"]
    proposed = summary["by_status"]["proposed"]
    return [
        "# شروط التصفية الشرعية — للمراجعة والاعتماد",
        "",
        "> **هذا الملف مولَّد آلياً.** لا تعدله بيدك. الشروط مكتوبة في",
        "> `src/ai_market_monitor/services/sharia_conditions.py`، والقرارات في",
        "> `sharia_condition_decisions.json`. بعد أي تعديل شغّل:",
        "> `.venv/Scripts/python scripts/export_sharia_conditions.py --write`",
        "",
        "## اقرأ هذا أولاً",
        "",
        "**هذه ليست فتوى، وأنا لست مفتياً.** هذا الملف اقتراح مكتوب من بحث في المصادر،",
        "معروض عليك أنت لتقرر. الأدلة موضوعة تحت كل شرط حتى يستطيع طالب علم أو شيخ أن",
        "يراجعها ويقول: هذا صحيح، أو هذا لا يصلح.",
        "",
        "**الشرط لا يعمل إلا بعد موافقتك.** أي شرط مكتوب هنا بحالة «مقترح» لا يغير شيئاً",
        "في المنتج إطلاقاً. النظام يقرأه ويحسب ماذا كان سيفعل لو اعتُمد، ثم يتركه.",
        "الذي يُشغِّل الشرط هو إضافة سطر باسمك وتاريخك في ملف القرارات، وهذا يظهر في سجل",
        "التعديلات، فيبقى معروفاً من قرر ومتى.",
        "",
        "**العملة لا توصف بحلال ولا حرام.** النظام يقول ثلاث كلمات فقط: «شكلها نضيفة»،",
        "و«فيها مشكلة»، و«مفيش داتا كفاية». الحكم الشرعي يصدر من جهة شرعية بمراجعة بشر.",
        "",
        f"**العدد:** {summary['total']} شرطاً. **المطبق الآن:** {approved}. "
        f"**المقترح عليك:** {proposed}.",
        "",
        "## مدى الفحص، وما الذي يُتخطى",
        "",
        "**الفحص الآلي يقرأ حتى 80 صفحة من موقع المشروع نفسه.** هذا هو مداه كله.",
        "الشرط الذي لا يثبت من هذه الصفحات — مثل ربا الفضل، أو نسبة الدين إلى قيمة",
        "الشركة — **يُتخطى**، ولا يُرسل إلى أحد ليراجعه بيده.",
        "",
        f"**{summary['applied']}** شرطاً يعمل فعلاً على صفحات المشروع، "
        f"و**{summary['out_of_reach']}** شرطاً معتمداً خارج هذا المدى فيُتخطى.",
        "",
        "**التخطي ليس نجاحاً.** معناه أننا لم ننظر، لا أن العملة سليمة في هذا الباب.",
        "وهذا مكتوب للناس في صفحة المنتج نفسها، حتى لا يفهم أحد الصمت على أنه شهادة.",
        "",
        "## كيف توافق على شرط",
        "",
        "افتح `src/ai_market_monitor/services/sharia_condition_decisions.json` وأضف:",
        "",
        "```json",
        "{",
        '  "code": "RB-07",',
        '  "status": "approved",',
        '  "decided_by": "اسمك",',
        '  "decided_on": "2026-08-31",',
        '  "note": "سبب القرار"',
        "}",
        "```",
        "",
        "ثم شغّل التصدير. لو أردت رفض شرط اكتب `rejected` بدل `approved`، ويبقى مكتوباً",
        "حتى لا يقترحه أحد مرة أخرى دون أن يعرف أنك نظرت فيه.",
        "",
        "## قبل أن توافق على شرط، انظر في ثلاثة أشياء",
        "",
        "| الشيء | لماذا يهم |",
        "|---|---|",
        "| **درجة الاتفاق** | شرط محل اتفاق غير شرط محل خلاف. الثاني يحتاج فتوى خاصة. |",
        "| **كيف يثبت** | شرط لا يثبت من صفحات المشروع لن يعمل أبداً. "
        "الموافقة عليه تكتب موقفك، لكن الفحص يتخطاه. |",
        "| **ماذا سيرفض** | بعض الشروط ترفض عملات تعدها جهات كبيرة متوافقة. "
        "هذا قرارك، لكن اعرفه قبل أن تقرره. |",
        "",
    ]


def _condition_block(item: Condition) -> list[str]:
    status = status_of(item.code)
    decision = decision_for(item.code)
    lines = [
        f"### {item.code} — {item.title_ar}",
        "",
        f"**الحالة:** {STATUS_AR[status]} · "
        f"**درجة الاتفاق:** {AGREEMENT_AR[item.agreement]} · "
        f"**كيف يثبت:** {DETECTION_AR[item.detection]}",
        "",
        f"**ما الذي يمنعه:** {item.meaning_ar}",
        "",
        f"**كيف يظهر في مشروع عملات:** {item.looks_like_ar}",
        "",
        "**الأدلة:**",
        "",
    ]
    for proof in item.evidence:
        kind = EVIDENCE_KIND_AR[proof.kind]
        lines.append(f"- *{kind}* — {proof.reference}: «{proof.text}»")
    lines.append("")
    if item.note_ar:
        lines.extend([f"**ملاحظة:** {item.note_ar}", ""])
    if item.phrases:
        shown = "، ".join(f"`{phrase}`" for phrase in item.phrases)
        lines.extend([f"**الألفاظ التي يبحث عنها:** {shown}", ""])
    if decision and decision.decided_on:
        note = f" — {decision.note}" if decision.note else ""
        lines.extend(
            [f"**القرار:** {decision.decided_by}، {decision.decided_on}{note}", ""]
        )
    lines.append("---")
    lines.append("")
    return lines


def render() -> str:
    lines = _preamble()
    for family in Family:
        members = [item for item in CONDITIONS if item.family is family]
        if not members:
            continue
        lines.append(f"## {FAMILY_TITLE_AR[family]}")
        lines.append("")
        for item in members:
            lines.extend(_condition_block(item))
    return "\n".join(lines).rstrip() + "\n"


def _payload() -> dict[str, object]:
    """Everything the page draws, taken straight from the register."""

    return {
        "summary": register_summary(),
        "families": [
            {"key": family.value, "title": FAMILY_TITLE_AR[family]}
            for family in Family
            if any(item.family is family for item in CONDITIONS)
        ],
        "statusLabels": {k.value: v for k, v in STATUS_AR.items()},
        "agreementLabels": {k.value: v for k, v in AGREEMENT_AR.items()},
        "detectionLabels": {k.value: v for k, v in DETECTION_AR.items()},
        "conditions": [
            {
                **item.as_dict(),
                "evidence": [
                    {
                        "kind_ar": EVIDENCE_KIND_AR[proof.kind],
                        "reference": proof.reference,
                        "text": proof.text,
                    }
                    for proof in item.evidence
                ],
            }
            for item in CONDITIONS
        ],
    }


def render_html() -> str:
    """The register as one right-to-left page, for reading and approving.

    Generated rather than hand-written, for the same reason the markdown is: a review
    page that drifted from the register would be a document showing an owner one rule
    while the product applied another.
    """

    data = json.dumps(_payload(), ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA__", data)


HTML_TEMPLATE = """<title>ضوابط تصفية العملات</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?\
family=Amiri:ital,wght@0,400;0,700;1,400\
&family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700\
&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* A statute book, not a brochure. Cool paper, ledger blue, semantic colour kept
   separate from the accent so a status never reads as decoration. */
:root{
  --paper:#F6F8FA; --card:#FFFFFF; --ink:#101418; --ink-2:#414A55; --ink-3:#6B7683;
  --rule:#DCE2E9; --rule-2:#EDF1F5;
  --accent:#2D4A7C; --accent-soft:#EAF0F9;
  --live:#0F5C46; --live-soft:#E4F1EC;
  --draft:#8A5A00; --draft-soft:#F8EFDC;
  --contested:#8C2F39; --contested-soft:#F8E8EA;
  --picked:#1B4E8F; --picked-soft:#E7EFFA;
  --shadow:0 1px 2px rgba(16,20,24,.05),0 8px 24px -16px rgba(16,20,24,.28);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#0E1116; --card:#151A21; --ink:#E8ECF1; --ink-2:#AEB8C4; --ink-3:#7D8794;
    --rule:#252C36; --rule-2:#1C222A;
    --accent:#8FB2E8; --accent-soft:#1A2434;
    --live:#5FCFAA; --live-soft:#122A23;
    --draft:#E0B15C; --draft-soft:#2B2213;
    --contested:#EF8C97; --contested-soft:#2E1A1D;
    --picked:#9DC0F2; --picked-soft:#16223A;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -16px rgba(0,0,0,.8);
  }
}
:root[data-theme="dark"]{
  --paper:#0E1116; --card:#151A21; --ink:#E8ECF1; --ink-2:#AEB8C4; --ink-3:#7D8794;
  --rule:#252C36; --rule-2:#1C222A;
  --accent:#8FB2E8; --accent-soft:#1A2434;
  --live:#5FCFAA; --live-soft:#122A23;
  --draft:#E0B15C; --draft-soft:#2B2213;
  --contested:#EF8C97; --contested-soft:#2E1A1D;
  --picked:#9DC0F2; --picked-soft:#16223A;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -16px rgba(0,0,0,.8);
}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink)}
.rtl{
  direction:rtl; text-align:right;
  font-family:"IBM Plex Sans Arabic",system-ui,"Segoe UI",sans-serif;
  font-weight:400; line-height:1.75; font-size:16px;
}
.wrap{max-width:940px;margin:0 auto;padding:0 20px 96px}

/* ---- masthead ---- */
.mast{padding:56px 0 28px;border-bottom:2px solid var(--ink)}
.kicker{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:11px;
  letter-spacing:.18em; text-transform:uppercase; color:var(--accent);
  margin:0 0 14px; direction:ltr; text-align:right;
}
h1{
  font-family:Amiri,"Times New Roman",serif; font-weight:700;
  font-size:clamp(34px,6vw,54px); line-height:1.18; margin:0 0 14px;
  text-wrap:balance; letter-spacing:0;
}
.stand{font-size:17px;color:var(--ink-2);margin:0;max-width:62ch;line-height:1.8}

/* ---- the three warnings ---- */
.warn{display:grid;gap:1px;background:var(--rule);border:1px solid var(--rule);margin:28px 0 0}
@media(min-width:560px){.warn{grid-template-columns:repeat(2,1fr)}}
@media(min-width:900px){.warn{grid-template-columns:repeat(4,1fr)}}
.warn div{background:var(--card);padding:18px 20px}
.warn h2{
  font-family:"IBM Plex Sans Arabic",sans-serif; font-size:13px; font-weight:600;
  margin:0 0 6px; color:var(--accent); letter-spacing:.01em;
}
.warn p{margin:0;font-size:14px;color:var(--ink-2);line-height:1.7}

/* ---- counts ---- */
.counts{display:flex;flex-wrap:wrap;gap:26px;padding:26px 0 0}
.count b{
  display:block; font-family:"IBM Plex Mono",monospace; font-size:30px;
  font-weight:500; line-height:1; font-variant-numeric:tabular-nums;
}
.count span{font-size:12.5px;color:var(--ink-3)}
.count.live b{color:var(--live)} .count.draft b{color:var(--draft)}

/* ---- filter bar ---- */
.bar{
  position:sticky; top:0; z-index:5; background:var(--paper);
  border-bottom:1px solid var(--rule); padding:12px 0; margin:32px 0 0;
  display:flex; flex-wrap:wrap; gap:8px; align-items:center;
}
.chip{
  font:inherit; font-size:13px; padding:5px 13px; border-radius:999px; cursor:pointer;
  border:1px solid var(--rule); background:var(--card); color:var(--ink-2);
}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.chip[aria-pressed="true"]{background:var(--ink);border-color:var(--ink);color:var(--paper)}
.chip:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.spacer{flex:1 1 auto}
.hits{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--ink-3)}

/* ---- family heading ---- */
.fam{
  display:flex; align-items:baseline; gap:12px;
  margin:52px 0 18px; padding-bottom:10px; border-bottom:1px solid var(--rule);
}
.fam h2{font-family:Amiri,serif;font-size:30px;font-weight:700;margin:0}
.fam span{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--ink-3)}

/* ---- one condition ---- */
.c{
  background:var(--card); border:1px solid var(--rule); border-radius:2px;
  padding:22px 24px; margin:0 0 14px; box-shadow:var(--shadow);
  border-right:3px solid var(--draft);
}
.c[data-status="approved"]{border-right-color:var(--live)}
.c[data-picked="1"]{border-right-color:var(--picked);background:var(--picked-soft)}
.head{display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap}
.code{
  font-family:"IBM Plex Mono",monospace; font-size:12px; font-weight:500;
  color:var(--accent); background:var(--accent-soft); padding:3px 8px;
  border-radius:2px; direction:ltr; margin-top:5px;
}
.c h3{font-family:Amiri,serif;font-size:25px;font-weight:700;margin:0;
  flex:1 1 220px;line-height:1.35}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 14px}
.t{font-size:11.5px;padding:3px 9px;border-radius:2px;white-space:nowrap}
.s-approved{background:var(--live-soft);color:var(--live)}
.s-proposed{background:var(--draft-soft);color:var(--draft)}
.s-rejected{background:var(--rule-2);color:var(--ink-3)}
.a-disputed{background:var(--contested-soft);color:var(--contested)}
.a-majority,.a-unanimous{background:var(--rule-2);color:var(--ink-2)}
.d{background:var(--rule-2);color:var(--ink-3)}
.c p{margin:0 0 10px;font-size:15px;line-height:1.8}
.lk{color:var(--ink-2);font-size:14.5px}
.lk b{color:var(--ink);font-weight:600}

/* ---- evidence: the one place the naskh face does real work ---- */
.ev{list-style:none;margin:16px 0 0;padding:0 16px 0 0;border-right:2px solid var(--rule)}
.ev li{margin:0 0 12px}
.ev .meta{
  font-size:11.5px; color:var(--ink-3); margin:0 0 3px;
  display:flex; gap:8px; flex-wrap:wrap;
}
.ev .kind{color:var(--accent);font-weight:600}
.ev q{
  display:block; font-family:Amiri,serif; font-size:19px; line-height:1.95;
  color:var(--ink); quotes:"«" "»";
}
.note{
  margin:14px 0 0; padding:11px 14px; background:var(--rule-2);
  font-size:13.5px; color:var(--ink-2); line-height:1.75; border-radius:2px;
}
.ph{margin:14px 0 0;display:flex;flex-wrap:wrap;gap:5px;direction:ltr;justify-content:flex-end}
.ph code{
  font-family:"IBM Plex Mono",monospace; font-size:11px; color:var(--ink-3);
  border:1px solid var(--rule); padding:2px 6px; border-radius:2px;
}
.pick{
  margin-top:16px; padding-top:14px; border-top:1px dashed var(--rule);
  display:flex; align-items:center; gap:9px; font-size:14px; cursor:pointer;
  color:var(--ink-2); user-select:none; width:fit-content;
}
.pick input{width:17px;height:17px;accent-color:var(--picked);cursor:pointer}
.c[data-status="approved"] .pick{display:none}

/* ---- the basket ---- */
.basket{
  position:fixed; inset-inline:0; bottom:0; z-index:9; background:var(--card);
  border-top:2px solid var(--ink); padding:14px 20px; box-shadow:var(--shadow);
}
.basket .inner{max-width:940px;margin:0 auto;display:flex;gap:14px;
  align-items:center;flex-wrap:wrap}
.basket p{margin:0;font-size:14px;flex:1 1 200px}
.basket b{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}
.btn{
  font:inherit; font-size:14px; padding:8px 18px; border-radius:2px; cursor:pointer;
  border:1px solid var(--ink); background:var(--ink); color:var(--paper);
}
.btn.ghost{background:transparent;color:var(--ink)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
pre{
  direction:ltr; text-align:left; background:var(--rule-2); border:1px solid var(--rule);
  padding:14px; overflow-x:auto; font-family:"IBM Plex Mono",monospace; font-size:12px;
  line-height:1.6; margin:12px 0 0; max-height:34vh;
}
.empty{padding:60px 0;text-align:center;color:var(--ink-3)}
@media (prefers-reduced-motion:no-preference){.c{transition:border-color .12s,background .12s}}
</style>

<div class="rtl">
<div class="wrap">
  <header class="mast">
    <p class="kicker">Hilal Markets &middot; Screening Register</p>
    <h1>ضوابط تصفية العملات</h1>
    <p class="stand">
      هذه قائمة الشروط التي يُفحص بها المشروع قبل أن يُقترح. تحتها أدلتها، وحالتها:
      أيّها يعمل على الصفحات، وأيّها معتمد لكن خارج مدى الفحص فيُتخطى، وأيّها ينتظر
      قرارك. لا شيء هنا فتوى، ولا يوصف مشروع بحلال ولا حرام.
    </p>
    <div class="warn">
      <div>
        <h2>هذا اقتراح، لا فتوى</h2>
        <p>الأدلة موضوعة تحت كل شرط ليراجعها أهل العلم. من كتب هذا ليس مفتياً.</p>
      </div>
      <div>
        <h2>الشرط لا يعمل حتى توافق</h2>
        <p>الشرط المقترح يُقرأ ويُحسب أثره، ثم يُترك. موافقتك وحدها هي التي تشغّله.</p>
      </div>
      <div>
        <h2>ثلاث إجابات فقط</h2>
        <p>«شكلها نضيفة»، «فيها مشكلة»، «مفيش داتا». الحكم الشرعي يصدر من جهة شرعية.</p>
      </div>
      <div>
        <h2>مداه 80 صفحة، وما بعدها يُتخطى</h2>
        <p>الفحص يقرأ موقع المشروع نفسه. ما لا يثبت منه يُتخطى، والتخطي ليس نجاحاً.</p>
      </div>
    </div>
    <div class="counts" id="counts"></div>
  </header>

  <nav class="bar" id="bar" aria-label="تصفية الشروط"></nav>
  <main id="list"></main>
</div>

<div class="basket" id="basket" hidden>
  <div class="inner">
    <p>اخترت <b id="n">0</b> شرطاً للاعتماد.</p>
    <button class="btn" id="show">اعرض ما تضيفه لملف القرارات</button>
    <button class="btn ghost" id="clear">امسح الاختيار</button>
  </div>
  <div class="inner"><pre id="out" hidden></pre></div>
</div>
</div>

<script>
const DATA = __DATA__;
const KEY = "hm-sharia-picks";
let picks = new Set();
try { picks = new Set(JSON.parse(localStorage.getItem(KEY) || "[]")); }
catch (e) { picks = new Set(); }

let filter = { family: "all", status: "all" };

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function drawCounts() {
  const s = DATA.summary;
  document.getElementById("counts").innerHTML = [
    ["", s.total, "شرطاً في السجل"],
    ["live", s.by_status.approved, "معتمد"],
    ["live", s.applied, "يعمل فعلاً على الصفحات"],
    ["draft", s.out_of_reach, "معتمد لكن يُتخطى"],
    ["draft", s.by_status.proposed, "ينتظر قرارك"],
  ].map(([cls, n, label]) =>
    `<div class="count ${cls}"><b>${n}</b><span>${esc(label)}</span></div>`).join("");
}

function drawBar() {
  const fams = [{ key: "all", title: "الكل" }].concat(DATA.families);
  const parts = fams.map((f) =>
    `<button class="chip" data-kind="family" data-key="${f.key}"
      aria-pressed="${filter.family === f.key}">${esc(f.title)}</button>`);
  parts.push('<span class="spacer"></span>');
  parts.push(...[["all", "كل الحالات"], ["approved", "يعمل"], ["proposed", "مقترح"]].map(([k, t]) =>
    `<button class="chip" data-kind="status" data-key="${k}"
      aria-pressed="${filter.status === k}">${esc(t)}</button>`));
  parts.push('<span class="hits" id="hits"></span>');
  document.getElementById("bar").innerHTML = parts.join("");
}

function visible() {
  return DATA.conditions.filter((c) =>
    (filter.family === "all" || c.family === filter.family) &&
    (filter.status === "all" || c.status === filter.status));
}

function card(c) {
  const proofs = c.evidence.map((e) =>
    `<li><p class="meta"><span class="kind">${esc(e.kind_ar)}</span>
      <span>${esc(e.reference)}</span></p><q>${esc(e.text)}</q></li>`).join("");
  const note = c.note_ar ? `<p class="note">${esc(c.note_ar)}</p>` : "";
  const phrases = c.phrases.length
    ? `<p class="ph">${c.phrases.map((p) => `<code>${esc(p)}</code>`).join("")}</p>` : "";
  const on = picks.has(c.code);
  return `<article class="c" data-status="${c.status}" data-picked="${on ? 1 : 0}"
      data-code="${c.code}">
    <div class="head"><span class="code">${c.code}</span><h3>${esc(c.title_ar)}</h3></div>
    <p class="tags">
      <span class="t s-${c.status}">${esc(DATA.statusLabels[c.status])}</span>
      <span class="t a-${c.agreement}">${esc(DATA.agreementLabels[c.agreement])}</span>
      <span class="t d">${esc(DATA.detectionLabels[c.detection])}</span>
    </p>
    <p>${esc(c.meaning_ar)}</p>
    <p class="lk"><b>في مشروع عملات:</b> ${esc(c.looks_like_ar)}</p>
    <ul class="ev">${proofs}</ul>${note}${phrases}
    <label class="pick"><input type="checkbox" ${on ? "checked" : ""}
      data-code="${c.code}"> أوافق على هذا الشرط</label>
  </article>`;
}

function draw() {
  const rows = visible();
  document.getElementById("hits").textContent = `${rows.length} / ${DATA.conditions.length}`;
  if (!rows.length) {
    document.getElementById("list").innerHTML =
      '<p class="empty">لا يوجد شرط بهذا الوصف.</p>';
    return;
  }
  let html = "";
  for (const fam of DATA.families) {
    const mine = rows.filter((c) => c.family === fam.key);
    if (!mine.length) continue;
    html += `<section><div class="fam"><h2>${esc(fam.title)}</h2>
      <span>${mine.length}</span></div>${mine.map(card).join("")}</section>`;
  }
  document.getElementById("list").innerHTML = html;
  drawBasket();
}

function drawBasket() {
  const basket = document.getElementById("basket");
  basket.hidden = picks.size === 0;
  document.getElementById("n").textContent = picks.size;
  document.body.style.paddingBottom = picks.size ? "120px" : "0";
}

document.getElementById("bar").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-kind]");
  if (!b) return;
  filter[b.dataset.kind] = b.dataset.key;
  drawBar();
  draw();
});

document.getElementById("list").addEventListener("change", (e) => {
  const box = e.target.closest("input[data-code]");
  if (!box) return;
  box.checked ? picks.add(box.dataset.code) : picks.delete(box.dataset.code);
  try { localStorage.setItem(KEY, JSON.stringify([...picks])); }
  catch (err) { /* a private window refuses storage */ }
  const art = box.closest(".c");
  if (art) art.dataset.picked = box.checked ? "1" : "0";
  document.getElementById("out").hidden = true;
  drawBasket();
});

document.getElementById("show").addEventListener("click", () => {
  const today = new Date().toISOString().slice(0, 10);
  const body = [...picks].sort().map((code) => ({
    code, status: "approved", decided_by: "", decided_on: today, note: "",
  }));
  const out = document.getElementById("out");
  out.textContent = JSON.stringify(body, null, 2);
  out.hidden = false;
});

document.getElementById("clear").addEventListener("click", () => {
  picks.clear();
  try { localStorage.removeItem(KEY); } catch (err) { /* private window */ }
  document.getElementById("out").hidden = true;
  draw();
});

drawCounts();
drawBar();
draw();
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the markdown file")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the file on disk is not what the register produces",
    )
    parser.add_argument("--html", type=Path, help="also write a right-to-left HTML page")
    args = parser.parse_args()

    produced = render()
    if args.html:
        args.html.write_text(render_html(), encoding="utf-8")
        print(f"wrote {args.html}")

    if args.write:
        DOC.parent.mkdir(parents=True, exist_ok=True)
        DOC.write_text(produced, encoding="utf-8")
        print(f"wrote {DOC} ({len(CONDITIONS)} conditions)")
        return 0

    on_disk = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    if on_disk == produced:
        print(f"{DOC.name} is in step with the register ({len(CONDITIONS)} conditions)")
        return 0
    print(
        f"{DOC.name} does not match the register. Somebody edited the document instead "
        "of sharia_conditions.py, or the register changed and this was not re-run.\n"
        "Fix it with: .venv/Scripts/python scripts/export_sharia_conditions.py --write",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
