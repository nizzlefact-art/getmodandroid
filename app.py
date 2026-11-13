"""GetModAndroid - Android apps & games directory (Flask + SQLite). Run: run.bat"""
import hashlib, os, re, secrets, sqlite3, time, zipfile
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for, Response)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash

# ---------------- config ----------------
ON_RENDER = bool(os.environ.get("RENDER"))
HOST = os.environ.get("HOST", "0.0.0.0" if ON_RENDER else "127.0.0.1")   # "0.0.0.0" = phone/LAN se bhi khule
PORT = int(os.environ.get("PORT", 5000))
MAX_APK_MB, MAX_IMG_MB, PER_PAGE = 300, 3, 12
LOGIN_MAX, LOGIN_WINDOW = 5, 900     # 5 wrong tries / 15 min

BASE = Path(__file__).resolve().parent
STORE = Path(os.environ.get("DATA_DIR", BASE))   # on Render point this at the persistent disk (/var/data)
def _make_dirs(root):
    d, u, a = root / "data", root / "uploads", root / "storage" / "apks"
    for p in [d, a, *(u / x for x in ("icons", "screenshots", "articles", "site"))]:
        p.mkdir(parents=True, exist_ok=True)
    return d, u, a
try:
    DATA, UPLOADS, APKS = _make_dirs(STORE)
except OSError as _e:   # e.g. DATA_DIR set but no disk attached
    print(f"WARNING: cannot use DATA_DIR={STORE} ({_e}). Falling back to the app folder - data will NOT persist across restarts on Render. Attach a disk at that path.")
    STORE = BASE
    DATA, UPLOADS, APKS = _make_dirs(STORE)
KEYF = DATA / "secret.key"
if not KEYF.exists():
    KEYF.write_text(secrets.token_hex(32))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or KEYF.read_text().strip()
if ON_RENDER:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(MAX_CONTENT_LENGTH=(MAX_APK_MB + 20) * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=ON_RENDER, TEMPLATES_AUTO_RELOAD=False)

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,ip TEXT NOT NULL,ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT,type TEXT NOT NULL DEFAULT 'app',name TEXT NOT NULL,slug TEXT UNIQUE NOT NULL,description TEXT);
CREATE TABLE IF NOT EXISTS apps(id INTEGER PRIMARY KEY AUTOINCREMENT,type TEXT NOT NULL DEFAULT 'app',category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
 name TEXT NOT NULL,slug TEXT UNIQUE NOT NULL,developer TEXT DEFAULT '',package_name TEXT,short_description TEXT DEFAULT '',description TEXT,features TEXT,install_guide TEXT,icon TEXT,
 min_android TEXT DEFAULT '5.0',status TEXT DEFAULT 'draft',is_featured INTEGER DEFAULT 0,is_trending INTEGER DEFAULT 0,is_editor_choice INTEGER DEFAULT 0,views INTEGER DEFAULT 0,
 meta_title TEXT,meta_description TEXT,meta_keywords TEXT,canonical_url TEXT,og_image TEXT,noindex INTEGER DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_apps_ts ON apps(type,status,updated_at);
CREATE TABLE IF NOT EXISTS app_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,version TEXT NOT NULL,
 apk_file TEXT,external_url TEXT,file_size INTEGER DEFAULT 0,sha256 TEXT,min_android TEXT DEFAULT '5.0',whats_new TEXT,release_date TEXT NOT NULL,download_count INTEGER DEFAULT 0,
 is_latest INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(app_id,version));
CREATE TABLE IF NOT EXISTS screenshots(id INTEGER PRIMARY KEY AUTOINCREMENT,app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,file TEXT NOT NULL,sort_order INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS article_categories(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,slug TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS articles(id INTEGER PRIMARY KEY AUTOINCREMENT,category_id INTEGER REFERENCES article_categories(id) ON DELETE SET NULL,
 author_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,title TEXT NOT NULL,slug TEXT UNIQUE NOT NULL,excerpt TEXT DEFAULT '',content TEXT,featured_image TEXT,
 is_featured INTEGER DEFAULT 0,status TEXT DEFAULT 'draft',published_at TEXT,meta_title TEXT,meta_description TEXT,meta_keywords TEXT,canonical_url TEXT,noindex INTEGER DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,name TEXT NOT NULL,rating INTEGER NOT NULL,
 comment TEXT NOT NULL,is_approved INTEGER DEFAULT 0,ip_hash TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS downloads(id INTEGER PRIMARY KEY AUTOINCREMENT,app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
 version_id INTEGER NOT NULL REFERENCES app_versions(id) ON DELETE CASCADE,ip_hash TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS website_settings(skey TEXT PRIMARY KEY,svalue TEXT);
"""
TG_URL = "https://t.me/getmodandroid_1"
DEFAULT_SETTINGS = {"site_name": "GetModAndroid", "site_tagline": "Android apps & games, details up front",
    "site_description": "We list Android apps and games with the version, file size and a checksum on every download, so you know what you are installing. New uploads and updates are posted on our Telegram channel first.",
    "contact_email": "contact@example.com", "logo": "", "favicon": "", "facebook": "", "twitter": "", "telegram": TG_URL, "ga_id": "", "ad_code": "",
    "footer_text": "GetModAndroid lists apps and games for information. Files are shared only when the owner allows it. If something should not be here, tell us and we will remove it.",
    "default_meta_title": "GetModAndroid - Android Apps & Games Download", "default_meta_description": "Download Android apps and games from GetModAndroid. Every file shows its version, size and SHA-256 so you can check it before installing.",
    "default_og_image": ""}
OLD_DEFAULTS = {"site_name": "PackNest", "site_tagline": "Safe Android apps & games",
    "site_description": "PackNest hosts Android apps and games that are free to distribute, with clear version history and file details.",
    "footer_text": "All apps are shown for information and distributed only with the owner's permission.",
    "default_meta_title": "PackNest - Android Apps & Games", "default_meta_description": "Download Android apps and games with version history and verified file details."}
SEED_CATS = [("app", n) for n in ("Entertainment", "Education", "Photography", "Productivity", "Communication", "Tools", "Music", "Video Players")] + \
            [("game", n) for n in ("Action Games", "Adventure Games", "Racing Games", "Simulation Games", "Puzzle Games")]

# ---------------- db helpers ----------------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DATA / "packnest.db")
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def query(sql, args=()): return db().execute(sql, args).fetchall()
def one(sql, args=()): return db().execute(sql, args).fetchone()
def scalar(sql, args=()):
    r = db().execute(sql, args).fetchone(); return r[0] if r else None
def run(sql, args=()):
    c = db().execute(sql, args); db().commit(); return c

def init_db():
    c = sqlite3.connect(DATA / "packnest.db"); c.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items(): c.execute("INSERT OR IGNORE INTO website_settings VALUES(?,?)", (k, v))
    if not c.execute("SELECT COUNT(*) FROM categories").fetchone()[0]:
        for t, n in SEED_CATS: c.execute("INSERT INTO categories(type,name,slug) VALUES(?,?,?)", (t, n, slugify(n)))
        for n in ("News", "Guides", "Updates"): c.execute("INSERT INTO article_categories(name,slug) VALUES(?,?)", (n, slugify(n)))
    for k, old in OLD_DEFAULTS.items():   # upgrade settings still on the old defaults
        c.execute("UPDATE website_settings SET svalue=? WHERE skey=? AND svalue=?", (DEFAULT_SETTINGS[k], k, old))
    c.execute("UPDATE website_settings SET svalue=? WHERE skey='telegram' AND (svalue IS NULL OR svalue='')", (TG_URL,))
    c.commit(); c.close()

def slugify(t):
    s = re.sub(r"[^a-z0-9]+", "-", (t or "").lower()).strip("-")
    return s or "item-" + secrets.token_hex(3)

def unique_slug(table, base, ignore=0):
    assert table in ("apps", "categories", "articles", "article_categories")
    slug = base = slugify(base); i = 2
    while scalar(f"SELECT COUNT(*) FROM {table} WHERE slug=? AND id<>?", (slug, ignore)): slug = f"{base}-{i}"; i += 1
    return slug

ITEM_SELECT = """SELECT a.*, c.name AS cat_name, c.slug AS cat_slug,
 (SELECT version FROM app_versions v WHERE v.app_id=a.id ORDER BY v.is_latest DESC, v.release_date DESC, v.id DESC LIMIT 1) AS version,
 (SELECT COALESCE(SUM(download_count),0) FROM app_versions v WHERE v.app_id=a.id) AS downloads,
 (SELECT ROUND(AVG(rating),1) FROM reviews r WHERE r.app_id=a.id AND r.is_approved=1) AS rating,
 (SELECT file FROM screenshots s WHERE s.app_id=a.id ORDER BY s.sort_order,s.id LIMIT 1) AS shot
 FROM apps a LEFT JOIN categories c ON c.id=a.category_id"""
ORDERS = {"latest": "a.created_at DESC", "updated": "a.updated_at DESC", "trending": "a.is_trending DESC, a.views DESC",
          "popular": "downloads DESC"}

def items(where="1=1", params=(), order="a.updated_at DESC", limit=12, offset=0):
    return query(f"{ITEM_SELECT} WHERE a.status='published' AND {where} ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit, offset))
def items_count(where="1=1", params=()):
    return scalar(f"SELECT COUNT(*) FROM apps a WHERE a.status='published' AND {where}", params)

# ---------------- template helpers ----------------
_s = {}
def settings():
    if "settings" not in g: g.settings = {r["skey"]: r["svalue"] or "" for r in query("SELECT * FROM website_settings")}
    return g.settings
def setting(k, d=""): return settings().get(k) or d
def item_url(a): return f"/{'game' if a['type'] == 'game' else 'app'}/{a['slug']}.html"
def img(p): return f"/uploads/{p}" if p else "/static/images/placeholder.svg"
def fmt_size(b):
    b = float(b or 0)
    if b <= 0: return "N/A"
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024 or u == "GB": return f"{round(b, 1 if u != 'B' else 0):g} {u}"
        b /= 1024
def fmt_num(n):
    n = int(n or 0)
    return f"{n/1e6:.1f}M" if n >= 1e6 else f"{n/1e3:.1f}K" if n >= 1e3 else str(n)
def ago(d):
    if not d: return ""
    try: return datetime.fromisoformat(str(d)[:19].replace("T", " ")).strftime("%b %d, %Y")
    except ValueError: return str(d)
def nl2br(s): return Markup(str(escape((s or "").replace("\r\n", "\n"))).replace("\n", "<br>\n"))
def csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_hex(32)
    return session["csrf"]
def pager(total, page, per, base):
    pages = -(-total // per)
    if pages <= 1: return Markup("")
    sep = "&" if "?" in base else "?"
    def link(p, t, rel=""):
        href = base if p == 1 else f"{base}{sep}page={p}"
        return f'<a {"rel=%s " % rel if rel else ""}href="{escape(href)}">{t}</a>'
    h = '<nav class="pager" aria-label="Pagination">'
    if page > 1: h += link(page - 1, "‹ Prev", "prev")
    for i in range(max(1, page - 2), min(pages, page + 2) + 1):
        h += f'<span class="cur">{i}</span>' if i == page else link(i, str(i))
    if page < pages: h += link(page + 1, "Next ›", "next")
    return Markup(h + "</nav>")
def page_num():
    try: return max(1, int(request.args.get("page", 1)))
    except ValueError: return 1
def abs_url(path): return request.url_root.rstrip("/") + path

app.jinja_env.globals.update(setting=setting, item_url=item_url, img=img, csrf=csrf, abs_url=abs_url)
app.jinja_env.filters.update(size=fmt_size, num=fmt_num, ago=ago, nl2br=nl2br)

@app.context_processor
def inject():
    return {"all_cats": query("SELECT name,slug FROM categories ORDER BY name"), "now_year": date.today().year}

@app.before_request
def protect():
    if request.method == "POST":
        t = request.form.get("csrf") or request.headers.get("X-CSRF", "")
        if not t or not secrets.compare_digest(t, session.get("csrf", "")): abort(403)

@app.after_request
def headers(r):
    r.headers.setdefault("X-Content-Type-Options", "nosniff"); r.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    r.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return r

@app.errorhandler(404)
def nf(_): return render_template("404.html", seo={"title": "Page not found", "noindex": True}), 404
@app.errorhandler(403)
def forbidden(_): return "Invalid or expired form token. Go back, refresh and try again.", 403
@app.errorhandler(413)
def too_big(_): return f"File too large (max {MAX_APK_MB} MB).", 413

# ---------------- uploads ----------------
def sniff_image(head):
    if head[:3] == b"\xff\xd8\xff": return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n": return "png"
    if head[:4] == b"GIF8": return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP": return "webp"
def save_image(f, folder, max_mb=MAX_IMG_MB):
    if not f or not f.filename: return None
    assert folder in ("icons", "screenshots", "articles", "site")
    head = f.stream.read(16); f.stream.seek(0, 2); size = f.stream.tell(); f.stream.seek(0)
    ext = sniff_image(head)
    if not ext: raise ValueError("Only JPG, PNG, WEBP or GIF images are allowed.")
    if size > max_mb * 1048576: raise ValueError(f"Image larger than {max_mb} MB.")
    name = f"{folder}/{secrets.token_hex(16)}.{ext}"
    f.save(UPLOADS / name); return name
def delete_upload(rel):
    if rel and re.fullmatch(r"(icons|screenshots|articles|site)/[a-f0-9]{32}\.(jpg|png|webp|gif)", rel):
        (UPLOADS / rel).unlink(missing_ok=True)
def save_apk(f):
    name = secrets.token_hex(20) + ".apk"; path = APKS / name
    f.save(path)
    try:
        if not zipfile.is_zipfile(path) or "AndroidManifest.xml" not in zipfile.ZipFile(path).namelist():
            raise ValueError("File is not a valid APK (no AndroidManifest.xml).")
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""): h.update(chunk)
        return name, path.stat().st_size, h.hexdigest()
    except Exception:
        path.unlink(missing_ok=True); raise
def delete_apk(name):
    if name and re.fullmatch(r"[a-f0-9]{40}\.apk", name): (APKS / name).unlink(missing_ok=True)
@app.route("/uploads/<path:p>")
def uploads(p): return send_from_directory(UPLOADS, p, max_age=2592000)
def ip_hash(): return hashlib.sha256(f"{request.remote_addr}|packnest".encode()).hexdigest()

# ---------------- public pages ----------------
@app.route("/")
def index():
    sections = [("Featured", items("a.is_featured=1", limit=8), None, "carousel"),
        ("Trending", items("a.is_trending=1", order=ORDERS["trending"], limit=8), "/apps/?sort=trending", "tiles"),
        ("Latest Android Games", items("a.type='game'", order=ORDERS["latest"], limit=8), "/games/", "tiles"),
        ("Latest Applications", items("a.type='app'", order=ORDERS["latest"], limit=8), "/apps/", "tiles"),
        ("Editor's Choice", items("a.is_editor_choice=1", limit=8), None, "carousel"),
        ("Recently Updated", items(limit=8), "/apps/?sort=updated", "tiles")]
    pop = query("SELECT c.name,c.slug,COUNT(a.id) n FROM categories c LEFT JOIN apps a ON a.category_id=c.id AND a.status='published' GROUP BY c.id ORDER BY n DESC,c.name LIMIT 12")
    news = query("SELECT * FROM articles WHERE status='published' AND published_at<=datetime('now') ORDER BY published_at DESC LIMIT 3")
    site = setting("site_name", "GetModAndroid")
    seo = {"title": setting("default_meta_title", site), "description": setting("default_meta_description"), "canonical": abs_url("/"),
           "schema": [{"@context": "https://schema.org", "@type": "WebSite", "name": site, "url": abs_url("/")}]}
    return render_template("index.html", sections=sections, pop=pop, news=news, seo=seo)

def listing(type_=None, cat_slug=None):
    cat = None
    if cat_slug:
        cat = one("SELECT * FROM categories WHERE slug=?", (cat_slug,))
        if not cat: abort(404)
        type_ = cat["type"]
    page, sort = page_num(), request.args.get("sort", "latest")
    where, params = "a.type=?", [type_]
    if cat: where += " AND a.category_id=?"; params.append(cat["id"])
    total = items_count(where, params)
    lst = items(where, params, ORDERS.get(sort, ORDERS["latest"]), PER_PAGE, (page - 1) * PER_PAGE)
    label = "Games" if type_ == "game" else "Apps"
    base = f"/category/{cat['slug']}/" if cat else ("/games/" if type_ == "game" else "/apps/")
    h1 = cat["name"] if cat else f"Android {label}"
    seo = {"title": f"{h1}{' - Page %d' % page if page > 1 else ''} | {setting('site_name')}",
           "description": (cat and cat["description"]) or f"Browse the latest and most popular Android {label.lower()} with version history and file details.",
           "canonical": abs_url(base + (f"?page={page}" if page > 1 else "")),
           "prev": abs_url(base + (f"?page={page-1}" if page > 2 else "")) if page > 1 else None,
           "next": abs_url(base + f"?page={page+1}") if page * PER_PAGE < total else None,
           "crumbs": [(label, "/games/" if type_ == "game" else "/apps/"), (cat["name"], None)] if cat else [(label, None)]}
    cats = query("SELECT name,slug FROM categories WHERE type=? ORDER BY name", (type_,))
    return render_template("listing.html", items=lst, h1=h1, cats=cats, type_=type_, cat=cat, sort=sort, base=base, seo=seo,
        pager=pager(total, page, PER_PAGE, base + (f"?sort={sort}" if sort != "latest" else "")), has_more=page * PER_PAGE < total, page=page)

@app.route("/apps/")
def apps_page(): return listing("app")
@app.route("/games/")
def games_page(): return listing("game")
@app.route("/category/<slug>/")
def category_page(slug): return listing(cat_slug=slug)

@app.route("/browse/")
def browse():
    cats = query("SELECT c.*,COUNT(a.id) n FROM categories c LEFT JOIN apps a ON a.category_id=c.id AND a.status='published' GROUP BY c.id ORDER BY c.name")
    seo = {"title": f"Browse categories | {setting('site_name')}", "description": "Browse all app and game categories.", "canonical": abs_url("/browse/"), "crumbs": [("Browse", None)]}
    return render_template("browse.html", app_cats=[c for c in cats if c["type"] == "app"], game_cats=[c for c in cats if c["type"] == "game"], seo=seo)

@app.route("/api/more")
def api_more():
    t = "game" if request.args.get("type") == "game" else "app"
    cid, page = request.args.get("cat", type=int) or 0, page_num()
    where, params = "a.type=?", [t]
    if cid: where += " AND a.category_id=?"; params.append(cid)
    total = items_count(where, params)
    lst = items(where, params, ORDERS.get(request.args.get("sort"), ORDERS["latest"]), PER_PAGE, (page - 1) * PER_PAGE)
    return jsonify(html=render_template("_cards.html", items=lst), more=page * PER_PAGE < total)

@app.route("/api/suggest")
def api_suggest():
    s = request.args.get("q", "").strip()
    if len(s) < 2: return jsonify([])
    like = "%" + re.sub(r"([%_\\])", r"\\\1", s) + "%"
    r = query("SELECT * FROM apps WHERE status='published' AND (name LIKE ? ESCAPE '\\' OR developer LIKE ? ESCAPE '\\') ORDER BY views DESC LIMIT 6", (like, like))
    return jsonify([{"name": a["name"], "dev": a["developer"], "url": item_url(a), "icon": img(a["icon"])} for a in r])

@app.route("/api/versions")
def api_versions():
    slugs = [s for s in request.args.get("s", "").split(",") if re.fullmatch(r"[a-z0-9-]{1,170}", s)][:12]
    if not slugs: return jsonify({})
    r = query(f"{ITEM_SELECT} WHERE a.status='published' AND a.slug IN ({','.join('?' * len(slugs))})", slugs)
    return jsonify({a["slug"]: a["version"] for a in r})

@app.route("/search")
def search():
    qs = request.args.get("q", "").strip(); page = page_num(); lst, total = [], 0
    if len(qs) >= 2:
        like = "%" + re.sub(r"([%_\\])", r"\\\1", qs) + "%"
        w = "(a.name LIKE ? ESCAPE '\\' OR a.developer LIKE ? ESCAPE '\\' OR a.short_description LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\')"
        p = [like] * 4
        total = scalar(f"SELECT COUNT(*) FROM apps a LEFT JOIN categories c ON c.id=a.category_id WHERE a.status='published' AND {w}", p)
        lst = query(f"{ITEM_SELECT} WHERE a.status='published' AND {w} ORDER BY a.updated_at DESC LIMIT ? OFFSET ?", (*p, PER_PAGE, (page - 1) * PER_PAGE))
    from urllib.parse import quote
    seo = {"title": f"Search: {qs} | {setting('site_name')}", "noindex": True, "crumbs": [("Search", None)]}
    return render_template("search.html", qs=qs, items=lst, total=total, seo=seo, pager=pager(total, page, PER_PAGE, "/search?q=" + quote(qs)))

@app.route("/app/<slug>.html", defaults={"type_": "app"})
@app.route("/game/<slug>.html", defaults={"type_": "game"})
def item(slug, type_):
    a = one(ITEM_SELECT + " WHERE a.status='published' AND a.slug=? AND a.type=?", (slug, type_))
    if not a: abort(404)
    run("UPDATE apps SET views=views+1 WHERE id=?", (a["id"],))
    versions = query("SELECT * FROM app_versions WHERE app_id=? ORDER BY is_latest DESC, release_date DESC, id DESC", (a["id"],))
    latest = versions[0] if versions else None
    shots = query("SELECT file FROM screenshots WHERE app_id=? ORDER BY sort_order,id", (a["id"],))
    reviews = query("SELECT * FROM reviews WHERE app_id=? AND is_approved=1 ORDER BY id DESC LIMIT 20", (a["id"],))
    rel_w, rel_p = "a.id<>? AND a.type=?", [a["id"], type_]
    if a["category_id"]: rel_w += " AND a.category_id=?"; rel_p.append(a["category_id"])
    related = items(rel_w, rel_p, "a.views DESC", 4)
    label = "Games" if type_ == "game" else "Apps"
    crumbs = [(label, "/games/" if type_ == "game" else "/apps/")] + ([(a["cat_name"], f"/category/{a['cat_slug']}/")] if a["cat_slug"] else []) + [(a["name"], None)]
    schema = {"@context": "https://schema.org", "@type": "VideoGame" if type_ == "game" else "MobileApplication", "name": a["name"],
              "operatingSystem": f"Android {a['min_android']}+", "applicationCategory": "GameApplication" if type_ == "game" else "MobileApplication",
              "description": a["short_description"], "image": abs_url(img(a["icon"])), "author": {"@type": "Organization", "name": a["developer"] or setting("site_name")},
              "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}
    if latest: schema["softwareVersion"] = latest["version"]
    if reviews: schema["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": a["rating"], "ratingCount": len(reviews)}
    items_ld = [{"@type": "ListItem", "position": 1, "name": "Home", "item": abs_url("/")}]
    for i, (n, u) in enumerate(crumbs, 2): items_ld.append({"@type": "ListItem", "position": i, "name": n, **({"item": abs_url(u)} if u else {})})
    seo = {"title": a["meta_title"] or f"{a['name']}{' v' + latest['version'] if latest else ''} for Android | {setting('site_name')}",
           "description": a["meta_description"] or a["short_description"], "keywords": a["meta_keywords"], "canonical": a["canonical_url"] or abs_url(item_url(a)),
           "noindex": a["noindex"], "og_image": abs_url("/uploads/" + a["og_image"]) if a["og_image"] else abs_url(img(a["icon"])), "og_type": "article",
           "schema": [schema, {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items_ld}], "crumbs": crumbs}
    return render_template("item.html", a=a, latest=latest, versions=versions, shots=shots, reviews=reviews, related=related, label=label, seo=seo)

@app.route("/api/review", methods=["POST"])
def api_review():
    f = request.form; aid = f.get("app_id", type=int) or 0
    name, comment, rating = f.get("name", "").strip()[:80], f.get("comment", "").strip()[:1500], f.get("rating", type=int) or 0
    if not scalar("SELECT COUNT(*) FROM apps WHERE id=? AND status='published'", (aid,)): return jsonify(ok=False, msg="App not found.")
    if len(name) < 2 or len(comment) < 5 or not 1 <= rating <= 5: return jsonify(ok=False, msg="Enter name, rating (1-5) and a comment of 5+ characters.")
    if f.get("website"): return jsonify(ok=True, msg="Thanks!")
    ih = ip_hash()
    if scalar("SELECT COUNT(*) FROM reviews WHERE ip_hash=? AND created_at>datetime('now','-1 hour')", (ih,)) >= 3: return jsonify(ok=False, msg="Too many reviews. Try later.")
    run("INSERT INTO reviews(app_id,name,rating,comment,ip_hash) VALUES(?,?,?,?,?)", (aid, name, rating, comment, ih))
    return jsonify(ok=True, msg="Thanks! Your review will appear after moderation.")

@app.route("/download/<int:vid>/")
def download(vid):
    v = one("SELECT v.*,a.name,a.slug,a.type,a.icon,a.developer FROM app_versions v JOIN apps a ON a.id=v.app_id WHERE v.id=? AND a.status='published'", (vid,))
    if not v or not (v["apk_file"] or v["external_url"]): abort(404)
    seo = {"title": f"Download {v['name']} {v['version']} | {setting('site_name')}", "noindex": True, "crumbs": [(v["name"], item_url(v)), ("Download", None)]}
    return render_template("download.html", v=v, seo=seo)

@app.route("/get", methods=["POST"])
def get_file():
    vid = request.form.get("id", type=int) or 0
    v = one("SELECT v.*,a.slug FROM app_versions v JOIN apps a ON a.id=v.app_id WHERE v.id=? AND a.status='published'", (vid,))
    if not v: abort(404)
    def record():
        run("UPDATE app_versions SET download_count=download_count+1 WHERE id=?", (v["id"],))
        run("INSERT INTO downloads(app_id,version_id,ip_hash) VALUES(?,?,?)", (v["app_id"], v["id"], ip_hash()))
    if not v["apk_file"]:
        if v["external_url"] and v["external_url"].lower().startswith("https://"): record(); return redirect(v["external_url"])
        abort(404)
    if not re.fullmatch(r"[a-f0-9]{40}\.apk", v["apk_file"]) or not (APKS / v["apk_file"]).is_file(): abort(404)
    record()
    fname = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{v['slug']}-{v['version']}") + ".apk"
    return send_file(APKS / v["apk_file"], mimetype="application/vnd.android.package-archive", as_attachment=True, download_name=fname)

@app.route("/news/")
def news_index(): return news_list(None)
@app.route("/news/category/<slug>/")
def news_cat(slug): return news_list(slug)
def news_list(cslug):
    page = page_num(); per = 9; cat = None; w, p = "a.status='published' AND a.published_at<=datetime('now')", []
    if cslug:
        cat = one("SELECT * FROM article_categories WHERE slug=?", (cslug,))
        if not cat: abort(404)
        w += " AND a.category_id=?"; p.append(cat["id"])
    total = scalar(f"SELECT COUNT(*) FROM articles a WHERE {w}", p)
    lst = query(f"SELECT a.* FROM articles a WHERE {w} ORDER BY a.is_featured DESC, a.published_at DESC LIMIT ? OFFSET ?", (*p, per, (page - 1) * per))
    base = f"/news/category/{cat['slug']}/" if cat else "/news/"
    seo = {"title": f"{cat['name'] + ' - ' if cat else ''}News & Articles | {setting('site_name')}", "description": "Latest news, guides and updates about Android apps and games.",
           "canonical": abs_url(base + (f"?page={page}" if page > 1 else "")), "crumbs": [("News", "/news/" if cat else None)] + ([(cat["name"], None)] if cat else [])}
    return render_template("news.html", items=lst, cat=cat, cats=query("SELECT * FROM article_categories ORDER BY name"), pager=pager(total, page, per, base), seo=seo)

@app.route("/news/<slug>.html")
def article(slug):
    n = one("SELECT a.*,c.name cat_name,c.slug cat_slug FROM articles a LEFT JOIN article_categories c ON c.id=a.category_id WHERE a.slug=? AND a.status='published' AND a.published_at<=datetime('now')", (slug,))
    if not n: abort(404)
    rel = query("SELECT title,slug FROM articles WHERE status='published' AND id<>? ORDER BY (category_id IS ?) DESC, published_at DESC LIMIT 3", (n["id"], n["category_id"]))
    u = abs_url(f"/news/{n['slug']}.html")
    seo = {"title": n["meta_title"] or f"{n['title']} | {setting('site_name')}", "description": n["meta_description"] or n["excerpt"], "keywords": n["meta_keywords"],
           "canonical": n["canonical_url"] or u, "noindex": n["noindex"], "og_type": "article", "og_image": abs_url("/uploads/" + n["featured_image"]) if n["featured_image"] else None,
           "schema": [{"@context": "https://schema.org", "@type": "Article", "headline": n["title"], "datePublished": n["published_at"], "dateModified": n["updated_at"],
                       "author": {"@type": "Organization", "name": setting("site_name") + " Team"}, "mainEntityOfPage": u}],
           "crumbs": [("News", "/news/"), (n["title"], None)]}
    return render_template("article.html", n=n, rel=rel, seo=seo)

@app.route("/page/<slug>.html")
def static_page(slug):
    s, m = escape(setting("site_name", "GetModAndroid")), escape(setting("contact_email"))
    P = {"about-us": ("About Us", f"<p>{s} is a small Android site run by one person who got tired of download pages that hide the version, the size and what the file actually is. So every listing here shows those details up front, and every download comes with a SHA-256 checksum you can compare.</p><p>We add new apps and games when we can check them, and post updates on <a href=\"{escape(setting('telegram', TG_URL))}\" rel=\"nofollow noopener\" target=\"_blank\">our Telegram channel</a>. We only share files that the owner allows us to share. Found something that should not be here? Tell us and it goes.</p>"),
     "contact-us": ("Contact Us", f'<p>Fastest way to reach us is Telegram: <a href="{escape(setting("telegram", TG_URL))}" rel="nofollow noopener" target="_blank">{escape(setting("telegram", TG_URL))}</a>. You can also email <a href="mailto:{m}">{m}</a>.</p><p>Copyright complaints should follow the steps on the <a href="/page/dmca.html">DMCA page</a> so we can act on them quickly.</p>'),
     "privacy-policy": ("Privacy Policy", f'<p>We keep very little about you. For download stats and spam control we store a hashed version of your IP address, never the address itself. If you post a review we store the name and comment you typed. The apps you viewed recently are saved only in your own browser.</p><p>If analytics or ads are switched on, those services may set their own cookies under their own policies. We do not sell your data. To remove a review you posted, email <a href="mailto:{m}">{m}</a>.</p>'),
     "terms-and-conditions": ("Terms and Conditions", f"<p>Use {s} at your own risk. Files are provided as they are, without any warranty, and every app or game belongs to its owner. Only install things you trust and use them lawfully.</p><p>Do not use this site to share material you have no right to share. We can remove any listing or review at any time.</p>"),
     "dmca": ("DMCA / Copyright Policy", f'<p>We take copyright seriously and remove reported content quickly.</p><h2>Send a takedown notice</h2><p>Email <a href="mailto:{m}">{m}</a> (or message us on Telegram) with: your name and contact details; the exact link(s) on this site; what the copyrighted work is; a statement that you believe in good faith the use is not authorised; a statement, under penalty of perjury, that your notice is accurate and you own the work or act for the owner; and your signature.</p><h2>What happens next</h2><p>Once the notice is complete we take the content down and may tell the person who uploaded it. If you think something was removed by mistake, send a counter-notice to the same address.</p>')}
    if slug not in P: abort(404)
    t, body = P[slug]
    return render_template("page.html", title=t, body=Markup(body), seo={"title": f"{t} | {setting('site_name')}", "crumbs": [(t, None)]})

@app.route("/sitemap.xml")
def sitemap():
    u = [("/", None)] + [(p, None) for p in ("/apps/", "/games/", "/news/", "/browse/")] + [(f"/page/{p}.html", None) for p in ("about-us", "contact-us", "privacy-policy", "terms-and-conditions", "dmca")]
    u += [(f"/category/{c['slug']}/", None) for c in query("SELECT slug FROM categories")]
    u += [(item_url(a), a["updated_at"]) for a in query("SELECT slug,type,updated_at FROM apps WHERE status='published' AND noindex=0")]
    u += [(f"/news/{a['slug']}.html", a["updated_at"]) for a in query("SELECT slug,updated_at FROM articles WHERE status='published' AND noindex=0")]
    x = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for p, m in u: x += f"<url><loc>{escape(abs_url(p))}</loc>{'<lastmod>' + str(m)[:10] + '</lastmod>' if m else ''}</url>\n"
    return Response(x + "</urlset>", mimetype="application/xml")

@app.route("/robots.txt")
def robots():
    return Response(f"User-agent: *\nDisallow: /admin/\nDisallow: /api/\nDisallow: /get\nDisallow: /search\nAllow: /\n\nSitemap: {abs_url('/sitemap.xml')}\n", mimetype="text/plain")

from admin import bp as admin_bp  # noqa: E402
app.register_blueprint(admin_bp)
init_db()
