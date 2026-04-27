#!/usr/bin/env python3
"""
Daily refresh for Amr Kamal's Flutter/Mobile jobs dashboard.

Sources (all free, no LLM required):
  - JSearch API (LinkedIn + Indeed + Glassdoor) — requires JSEARCH_API_KEY
      Free tier: 200 req/month  |  ~4 req/day × 30 days = 120/month → $0
  - Remotive API (remote jobs) — public, no key
  - Arbeitnow API (EU + DE)    — public, no key
  - 13 hardcoded Email-only MENA leads (always included)

Output:
  - index.html (encrypted with AES-256-GCM + PBKDF2-SHA256 x600,000, password from DASHBOARD_PASSWORD env)

Run:  python3 scripts/daily_refresh.py
Triggered by: .github/workflows/daily.yml (daily cron, 07:00 UTC = 09:00 Cairo)
"""
import base64, html, json, os, re, secrets, sys
from datetime import datetime, date, timezone
from urllib import request, parse, error

try:
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("ERROR: pip install cryptography", file=sys.stderr)
    sys.exit(1)

# ---------- Config ----------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "scripts", "lock_template.html")
OUTPUT_PATH   = os.path.join(REPO_ROOT, "index.html")

PBKDF2_ITERS = 600_000
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "@mr2026")
JSEARCH_API_KEY = os.environ.get("JSEARCH_API_KEY", "ak_hnuq0efjtde78je2ik1bhjjkfa0982xlgomtvzc53nh97b1").strip()

TODAY = date.today()
HTTP_TIMEOUT = 20
USER_AGENT = "AmrKamal-JobsDashboard/1.0 (+https://amrkamal1993.github.io/job_seeker/)"

# Flutter / mobile keyword filter (case-insensitive match against title+summary)
KEYWORD_RE = re.compile(r"\b(flutter|dart|mobile|android|ios|react\s*native|kotlin multiplatform|kmp)\b", re.I)

# ---------- Hardcoded email-only MENA leads ----------
EMAIL_LEADS = [
    ("Instabug",       "careers@instabug.com",          "Egypt",        "Hybrid",  92),
    ("Paymob",         "careers@paymob.com",            "Egypt",        "Hybrid",  93),
    ("Valu",           "careers@valu.com.eg",           "Egypt",        "Hybrid",  90),
    ("Swvl",           "careers@swvl.com",              "Cairo/Dubai",  "Remote",  86),
    ("Fawry",          "careers@fawry.com",             "Egypt",        "On-site", 88),
    ("Vodafone Egypt", "recruitment.egypt@vodafone.com","Egypt",        "Hybrid",  87),
    ("MNT-Halan",      "careers@halan.com",             "Egypt",        "Hybrid",  91),
    ("Tabby",          "careers@tabby.ai",              "Dubai/Riyadh", "Hybrid",  90),
    ("Tamara",         "careers@tamara.co",             "Riyadh/Dubai", "Hybrid",  89),
    ("Careem",         "careers@careem.com",            "Dubai",        "Hybrid",  88),
    ("Noon",           "careers@noon.com",              "Dubai/Riyadh", "On-site", 85),
    ("stc pay",        "careers@stcpay.com.sa",         "Riyadh",       "On-site", 90),
    ("Kitopi",         "careers@kitopi.com",            "Dubai",        "On-site", 82),
]


def http_get_json(url, headers=None):
    req = request.Request(url, headers={**(headers or {}), "User-Agent": USER_AGENT, "Accept": "application/json"})
    with request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_iso_date(s):
    if not s:
        return None
    try:
        # handle "2026-04-20T12:34:56Z" or "2026-04-20T12:34:56+00:00"
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s).date()
    except Exception:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def age_for(posted_date):
    if posted_date is None:
        return "غير معروف", 999
    days = (TODAY - posted_date).days
    if days <= 0:
        return "اتنشرت النهاردة ✨", 0
    if days >= 30:
        return "اتنشرت من شهر+", days
    return f"اتنشرت من {days} أيام", days


def match_score(title, summary=""):
    """Rough heuristic: boost for flutter/dart > mobile > android/ios > rn. Caps at 93."""
    t = (title + " " + (summary or "")).lower()
    score = 75
    if "flutter" in t or "dart" in t: score += 15
    if "senior" in t or "lead" in t or "staff" in t or "principal" in t: score += 3
    if "mobile" in t: score += 2
    if "android" in t or " ios" in t: score += 1
    if "react native" in t: score -= 2
    return min(93, max(70, score))


# ---------- Source: JSearch (LinkedIn + Indeed + Glassdoor) ----------
# Free tier: 200 req/month — 4 queries/day × 30 days = 120/month → $0 cost
def fetch_jsearch(query, date_posted="month", remote_only=False, num_results=10):
    """Fetch jobs from JSearch API (aggregates LinkedIn, Indeed, Glassdoor)."""
    if not JSEARCH_API_KEY:
        print("[jsearch] No API key — skipping.", file=sys.stderr)
        return []
    params = {
        "query": query,
        "page": "1",
        "num_pages": "1",
        "date_posted": date_posted,
    }
    if remote_only:
        params["remote_jobs_only"] = "true"
    url = "https://jsearch.p.rapidapi.com/search?" + parse.urlencode(params)
    req = request.Request(
        url,
        headers={
            "X-RapidAPI-Key": JSEARCH_API_KEY,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
            "User-Agent": USER_AGENT,
        }
    )
    try:
        with request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[jsearch '{query}'] {e}", file=sys.stderr)
        return []
    out = []
    for j in (data.get("data") or [])[:num_results]:
        title = (j.get("job_title") or "").strip()
        desc  = j.get("job_description") or ""
        if not KEYWORD_RE.search(title + " " + desc):
            continue
        posted = parse_iso_date(j.get("job_posted_at_datetime_utc"))
        if posted is None or (TODAY - posted).days > 30:
            continue
        # Build location string
        city    = j.get("job_city") or ""
        country = j.get("job_country") or ""
        loc = ", ".join(filter(None, [city, country])) or "Remote"
        is_remote = j.get("job_is_remote") or "remote" in (title + " " + loc).lower()
        mode = "Remote" if is_remote else "On-site"
        # Determine region tag from country code
        region_map = {"US": "US", "GB": "GB", "DE": "DE", "IN": "IN"}
        region = region_map.get((j.get("job_country") or "").upper(), "Global")
        if is_remote:
            region = "Remote"
        # Salary
        sal_min = j.get("job_min_salary")
        sal_max = j.get("job_max_salary")
        cur     = j.get("job_salary_currency") or ""
        salary  = None
        if sal_min and sal_max:
            salary = f"{int(sal_min):,}–{int(sal_max):,} {cur}/yr".strip()
        # Source badge — prefer employer_logo source hint, fall back to "jsearch"
        src_hint = (j.get("job_publisher") or "jsearch").lower()
        if "linkedin" in src_hint:
            src = "linkedin"
        elif "indeed" in src_hint:
            src = "indeed"
        else:
            src = "jsearch"
        out.append({
            "src": src,
            "title": title,
            "company": j.get("employer_name") or "—",
            "loc": loc, "region": region, "mode": mode,
            "posted": posted.isoformat(),
            "match": match_score(title, desc),
            "salary": salary,
            "apply": j.get("job_apply_link"),
        })
    return out


# ---------- Source: Remotive ----------
def fetch_remotive(search="flutter"):
    url = f"https://remotive.com/api/remote-jobs?search={parse.quote(search)}&limit=30"
    try:
        data = http_get_json(url)
    except Exception as e:
        print(f"[remotive] {e}", file=sys.stderr)
        return []
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "").strip()
        desc = j.get("description", "")
        if not KEYWORD_RE.search(title + " " + desc):
            continue
        posted = parse_iso_date(j.get("publication_date"))
        if posted is None or (TODAY - posted).days > 30:
            continue
        out.append({
            "src": "remotive", "title": title,
            "company": j.get("company_name", "—"),
            "loc": j.get("candidate_required_location") or "Remote",
            "region": "Remote", "mode": "Remote",
            "posted": posted.isoformat(), "match": match_score(title, desc),
            "salary": j.get("salary") or None,
            "apply": j.get("url"),
        })
    return out


# ---------- Source: Arbeitnow ----------
def fetch_arbeitnow():
    url = "https://www.arbeitnow.com/api/job-board-api"
    try:
        data = http_get_json(url)
    except Exception as e:
        print(f"[arbeitnow] {e}", file=sys.stderr)
        return []
    out = []
    for j in data.get("data", []):
        title = j.get("title", "").strip()
        desc  = j.get("description", "")
        if not KEYWORD_RE.search(title + " " + desc):
            continue
        ts = j.get("created_at")
        posted = date.fromtimestamp(ts) if isinstance(ts, (int, float)) else None
        if posted is None or (TODAY - posted).days > 30:
            continue
        tags = [t.lower() for t in (j.get("tags") or [])]
        is_remote = j.get("remote") or any("remote" in t for t in tags)
        out.append({
            "src": "arbeitnow", "title": title,
            "company": j.get("company_name", "—"),
            "loc": j.get("location") or ("Remote" if is_remote else "Germany"),
            "region": "DE" if not is_remote else "Remote",
            "mode": "Remote" if is_remote else "On-site",
            "posted": posted.isoformat(), "match": match_score(title, desc),
            "salary": None, "apply": j.get("url"),
        })
    return out


# ---------- Pipeline ----------
def dedupe(cards):
    seen, out = set(), []
    for c in cards:
        k = (c["company"].strip().lower(), c["title"].strip().lower()[:60])
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def gather_jobs():
    jobs = []
    # ── JSearch (LinkedIn + Indeed + Glassdoor) ─────────────────────────
    # 4 queries/day × 30 days = 120 req/month  →  within free 200/month limit
    jobs += fetch_jsearch("flutter developer",        date_posted="week")
    jobs += fetch_jsearch("flutter developer remote", date_posted="week",  remote_only=True)
    jobs += fetch_jsearch("flutter mobile developer", date_posted="month")
    jobs += fetch_jsearch("senior flutter engineer",  date_posted="month")
    # ── Remotive (free, no key) ──────────────────────────────────────────
    jobs += fetch_remotive("flutter")
    jobs += fetch_remotive("mobile developer")
    # ── Arbeitnow (free, EU/DE focused) ─────────────────────────────────
    jobs += fetch_arbeitnow()
    jobs = dedupe(jobs)
    # Cap to 40 live jobs, sorted freshest first
    jobs.sort(key=lambda c: c["posted"], reverse=True)
    return jobs[:40]


# ---------- HTML build ----------
def build_plaintext_html(live_jobs):
    cards = []
    for j in live_jobs:
        lbl, days = age_for(parse_iso_date(j["posted"]))
        cards.append({**j, "age_label": lbl, "age_days": days})
    for (name, email, loc, mode, match) in EMAIL_LEADS:
        cards.append({
            "src": "email",
            "title": f"Flutter / Mobile Engineer Lead — {name}",
            "company": name, "loc": loc, "region": "Email",
            "mode": mode, "posted": None, "match": match, "salary": None,
            "apply": None, "email": email,
            "age_label": "Email فقط — Cold outreach", "age_days": -1,
        })

    total = len(cards)
    today_n  = sum(1 for c in cards if c["age_days"] == 0)
    fresh48  = sum(1 for c in cards if 0 <= c["age_days"] <= 2)
    src_counts = {}
    for c in cards:
        src_counts[c["src"]] = src_counts.get(c["src"], 0) + 1

    data_json = json.dumps(cards, ensure_ascii=False)

    # Source-badge color mapping matching the original spec
    return r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flutter Jobs — Amr Kamal</title>
<style>
  :root{
    --bg:#0b1020;--card:#121a33;--card2:#0e1530;--ink:#eef2ff;--mute:#9aa3c7;--line:#243159;
    --accent:#7c9cff;--ok:#22c55e;--warn:#f59e0b;--err:#ef4444;
    --jsearch:#f59e0b;--linkedin:#0a66c2;--indeed:#2557a7;--remotive:#16a34a;--arbeitnow:#e11d48;--email:#7c3aed;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Cairo",Arial,sans-serif;line-height:1.5}
  header{padding:22px 18px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#0e1634,#0b1020)}
  header h1{margin:0;font-size:22px}
  header p{margin:4px 0 0;color:var(--mute);font-size:13px}
  .container{max-width:1200px;margin:0 auto;padding:18px}
  .stats{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
  .chip{padding:6px 10px;border:1px solid var(--line);border-radius:999px;font-size:12px;background:var(--card)}
  .chip b{color:var(--accent)}
  .controls{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px;padding:12px;background:var(--card);border:1px solid var(--line);border-radius:12px}
  .controls label{font-size:12px;color:var(--mute);display:flex;flex-direction:column;gap:4px}
  .controls select{padding:8px 10px;background:var(--card2);color:var(--ink);border:1px solid var(--line);border-radius:8px;min-width:130px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
  .job{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;position:relative;display:flex;flex-direction:column;gap:8px}
  .job .src{position:absolute;top:12px;left:12px;padding:3px 8px;font-size:11px;border-radius:999px;font-weight:700;color:#fff}
  .src.jsearch{background:var(--jsearch)}
  .src.linkedin{background:var(--linkedin)}
  .src.indeed{background:var(--indeed)}
  .src.remotive{background:var(--remotive)}
  .src.arbeitnow{background:var(--arbeitnow)}
  .src.email{background:var(--email)}
  .job h3{margin:0;font-size:15.5px;padding-left:82px;min-height:22px}
  .job .co{font-size:13px;color:var(--mute)}
  .badges{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
  .badge{padding:3px 9px;font-size:11px;border-radius:999px;border:1px solid var(--line);background:var(--card2);color:var(--mute)}
  .badge.match{font-weight:700;color:#052}
  .badge.match.ok{background:#dcfce7;border-color:#16a34a}
  .badge.match.warn{background:#fef3c7;border-color:#d97706;color:#713f12}
  .salary{font-size:12.5px;color:var(--ok)}
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:9px 14px;border-radius:10px;font-size:13px;font-weight:700;text-decoration:none;margin-top:8px;cursor:pointer;border:none}
  .btn.adzuna{background:var(--adzuna);color:#fff}
  .btn.remotive{background:var(--remotive);color:#fff}
  .btn.arbeitnow{background:var(--arbeitnow);color:#fff}
  .btn.ghost{background:var(--card2);color:var(--ink);border:1px solid var(--line)}
  .email-block{background:var(--card2);border:1px dashed var(--line);border-radius:10px;padding:10px;margin-top:6px;font-size:12.5px}
  .email-block code{display:block;font-size:13px;color:var(--accent);direction:ltr;padding:6px 0}
  .email-row{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
  .preview{display:none;margin-top:8px;padding:10px;border-radius:8px;background:#0b1224;border:1px solid var(--line);font-size:12px;white-space:pre-wrap;direction:ltr;text-align:left;max-height:200px;overflow:auto}
  .preview.ar{direction:rtl;text-align:right}
  footer{text-align:center;color:var(--mute);font-size:12px;padding:20px}
</style>
</head>
<body>
  <header>
    <h1>🔥 Flutter / Mobile Jobs — Amr Kamal</h1>
    <p>تحديث يومي تلقائي · Adzuna + Remotive + Arbeitnow + Email Leads · """ + TODAY.strftime("%Y-%m-%d") + r"""</p>
  </header>
  <div class="container">
    <div class="stats">
      <span class="chip">إجمالي: <b>""" + str(total) + r"""</b></span>
      <span class="chip">النهاردة: <b>""" + str(today_n) + r"""</b></span>
      <span class="chip">آخر 48 ساعة: <b>""" + str(fresh48) + r"""</b></span>
      <span class="chip">LinkedIn: <b>""" + str(src_counts.get("linkedin", 0)) + r"""</b></span>
      <span class="chip">Indeed: <b>""" + str(src_counts.get("indeed", 0)) + r"""</b></span>
      <span class="chip">JSearch: <b>""" + str(src_counts.get("jsearch", 0)) + r"""</b></span>
      <span class="chip">Remotive: <b>""" + str(src_counts.get("remotive", 0)) + r"""</b></span>
      <span class="chip">Arbeitnow: <b>""" + str(src_counts.get("arbeitnow", 0)) + r"""</b></span>
      <span class="chip">Email: <b>""" + str(src_counts.get("email", 0)) + r"""</b></span>
      <span class="chip">معروض: <b id="visibleCount">""" + str(total) + r"""</b></span>
    </div>
    <div class="controls">
      <label>Mode
        <select id="fMode"><option value="">الكل</option><option>Remote</option><option>Hybrid</option><option>On-site</option></select>
      </label>
      <label>Region
        <select id="fRegion"><option value="">الكل</option><option>DE</option><option>US</option><option>GB</option><option>IN</option><option>Remote</option><option>Email</option></select>
      </label>
      <label>Source
        <select id="fSrc"><option value="">الكل</option><option>linkedin</option><option>indeed</option><option>jsearch</option><option>remotive</option><option>arbeitnow</option><option>email</option></select>
      </label>
      <label>Sort
        <select id="fSort"><option value="newest">Newest</option><option value="match">Match %</option></select>
      </label>
    </div>
    <div class="grid" id="grid"></div>
    <footer>🔒 AES-256-GCM · تحديث أوتوماتيكي يومي الساعة 9 الصبح · Amr Kamal Ali · 8+ yrs Flutter</footer>
  </div>

<script id="jobdata" type="application/json">""" + data_json + r"""</script>
<script>
  (function(){
    const all = JSON.parse(document.getElementById("jobdata").textContent);
    const grid = document.getElementById("grid");
    const vc = document.getElementById("visibleCount");
    const fMode = document.getElementById("fMode");
    const fRegion = document.getElementById("fRegion");
    const fSrc = document.getElementById("fSrc");
    const fSort = document.getElementById("fSort");
    const esc = s=>(s||"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const srcLabel = s => ({linkedin:"LinkedIn",indeed:"Indeed",jsearch:"JSearch",remotive:"Remotive",arbeitnow:"Arbeitnow",email:"Email فقط"}[s]||s);
    const applyBtn = j=>{
      if (!j.apply) return "";
      const cls = ["linkedin","indeed","jsearch","remotive","arbeitnow"].includes(j.src) ? j.src : "ghost";
      return `<a class="btn ${cls}" target="_blank" rel="noopener" href="${esc(j.apply)}">↗ Apply on ${srcLabel(j.src)}</a>`;
    };
    const emailBlock = j=>{
      const subj = encodeURIComponent(`${j.title} — Amr Kamal Ali (8+ yrs Flutter, ex-KIB & Tawuniya)`);
      const bodyAR = `السلام عليكم،\n\nأنا عمرو كمال، Flutter Developer عندي 8+ سنين خبرة في بناء تطبيقات موبايل على Android/iOS باستخدام Flutter/Dart، MVVM، Clean Architecture، BLoC، Firebase، CI/CD، و Unit/Widget testing. اشتغلت قبل كدة مع KIB و شركة التعاونية.\n\nمهتم بشدة بفرص Flutter عندكم. السيرة الذاتية مرفقة.\n\nشكراً،\nعمرو كمال علي\namr2010kamal@gmail.com`;
      const bodyEN = `Hello,\n\nI'm Amr Kamal, a Flutter Developer with 8+ years of experience building production mobile apps on Android/iOS using Flutter/Dart, MVVM, Clean Architecture, BLoC, Firebase, CI/CD, and unit/widget testing. Previously at KIB and Tawuniya.\n\nI'd love to be considered for Flutter roles at ${j.company}. CV attached.\n\nThanks,\nAmr Kamal Ali\namr2010kamal@gmail.com`;
      const body = encodeURIComponent(bodyEN + "\n\n---\n\n" + bodyAR);
      const id = "p" + Math.random().toString(36).slice(2,9);
      return `<div class="email-block">
        <code>${esc(j.email)}</code>
        <div class="email-row">
          <a class="btn ghost" href="mailto:${esc(j.email)}?subject=${subj}&body=${body}">✉️ Open mail</a>
          <button class="btn ghost" onclick="navigator.clipboard.writeText('${esc(j.email)}');this.innerText='✅ Copied'">📋 Copy</button>
          <button class="btn ghost" onclick="var e=document.getElementById('${id}-en');e.style.display=e.style.display==='block'?'none':'block'">📝 EN</button>
          <button class="btn ghost" onclick="var e=document.getElementById('${id}-ar');e.style.display=e.style.display==='block'?'none':'block'">📝 AR</button>
        </div>
        <div id="${id}-en" class="preview">${esc(bodyEN)}</div>
        <div id="${id}-ar" class="preview ar">${esc(bodyAR)}</div>
      </div>`;
    };
    function render(){
      let list = all.filter(j=>
        (!fMode.value || j.mode===fMode.value) &&
        (!fRegion.value || j.region===fRegion.value) &&
        (!fSrc.value || j.src===fSrc.value)
      );
      list.sort((a,b)=>{
        if (fSort.value==="match") return b.match - a.match;
        const av=a.age_days<0?99999:a.age_days, bv=b.age_days<0?99999:b.age_days;
        return av - bv;
      });
      vc.textContent = list.length;
      grid.innerHTML = list.map(j=>{
        const mCls = j.match>=88?"ok":(j.match>=75?"warn":"");
        const salary = j.salary ? `<div class="salary">💰 ${esc(j.salary)}</div>` : "";
        const apply = j.src==="email" ? emailBlock(j) : applyBtn(j);
        return `<div class="job">
          <span class="src ${j.src}">${srcLabel(j.src)}</span>
          <h3>${esc(j.title)}</h3>
          <div class="co">${esc(j.company)} · ${esc(j.loc)}</div>
          <div class="badges">
            <span class="badge">${esc(j.mode)}</span>
            <span class="badge">${esc(j.age_label)}</span>
            <span class="badge match ${mCls}">Match ${j.match}%</span>
          </div>
          ${salary}${apply}
        </div>`;
      }).join("");
    }
    [fMode,fRegion,fSrc,fSort].forEach(el=>el.addEventListener("change", render));
    render();
  })();
</script>
</body>
</html>"""


# ---------- Encryption ----------
def encrypt_and_package(plaintext_html):
    salt = secrets.token_bytes(16)
    iv   = secrets.token_bytes(12)
    key  = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                      iterations=PBKDF2_ITERS).derive(PASSWORD.encode())
    ct   = AESGCM(key).encrypt(iv, plaintext_html.encode("utf-8"), None)
    payload = {
        "v": 1, "kdf": "PBKDF2-SHA256", "iter": PBKDF2_ITERS,
        "salt": base64.b64encode(salt).decode(),
        "iv":   base64.b64encode(iv).decode(),
        "ct":   base64.b64encode(ct).decode(),
    }
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        tpl = f.read()
    if "__PAYLOAD__" not in tpl:
        raise RuntimeError(f"Template missing __PAYLOAD__ marker: {TEMPLATE_PATH}")
    return tpl.replace("__PAYLOAD__", json.dumps(payload))


# ---------- Main ----------
def main():
    print(f"→ daily_refresh  date={TODAY}  password_env_set={bool(PASSWORD)}  jsearch_set={bool(JSEARCH_API_KEY)}")
    live = gather_jobs()
    print(f"→ live jobs collected: {len(live)}")
    if not live and not EMAIL_LEADS:
        print("⚠️ No jobs from any source — aborting to avoid empty dashboard.", file=sys.stderr)
        sys.exit(2)

    plain = build_plaintext_html(live)
    final_html = encrypt_and_package(plain)

    # Sanity: encrypted output must NOT contain any job's apply URL / company name
    for j in live[:5]:
        if j.get("apply") and j["apply"] in final_html:
            raise RuntimeError(f"Plaintext leak detected: {j['apply']}")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(final_html)
    print(f"✓ Wrote {OUTPUT_PATH} ({len(final_html):,} bytes, plaintext {len(plain):,} → encrypted)")


if __name__ == "__main__":
    main()
