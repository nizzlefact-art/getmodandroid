from datetime import date, datetime
from functools import wraps
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import time

from app import (MAX_APK_MB, MAX_IMG_MB, LOGIN_MAX, LOGIN_WINDOW, db, query, one, scalar, run, slugify, unique_slug,
                 save_image, delete_upload, save_apk, delete_apk, setting)

bp = Blueprint("admin", __name__, url_prefix="/admin", template_folder="templates")

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin_id") or not one("SELECT 1 FROM admins WHERE id=?", (session["admin_id"],)):
            return redirect(url_for("admin.login"))
        return f(*a, **k)
    return w

def chk(k): return 1 if request.form.get(k) else 0
def fv(k, n=0):
    v = (request.form.get(k) or "").strip(); return v[:n] if n else v
def back(endpoint="admin.dashboard", **kw): return redirect(url_for(endpoint, **kw))

@bp.app_context_processor
def _ctx(): return {"MAX_APK_MB": MAX_APK_MB, "MAX_IMG_MB": MAX_IMG_MB}

# ---- auth ----
@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if scalar("SELECT COUNT(*) FROM admins"): return back("admin.login")
    if request.method == "POST":
        em, pw = fv("email").lower(), request.form.get("password", "")
        if "@" not in em or len(pw) < 10: flash("Valid email and password of 10+ characters required.", "err")
        else:
            run("INSERT INTO admins(email,password_hash) VALUES(?,?)", (em, generate_password_hash(pw)))
            flash("Admin ban gaya. Ab login karo.")
            return back("admin.login")
    return render_template("admin/login.html", setup=True)

@bp.route("/login", methods=["GET", "POST"])
def login():
    if not scalar("SELECT COUNT(*) FROM admins"): return back("admin.setup")
    if request.method == "POST":
        ip = request.remote_addr or ""
        run("DELETE FROM login_attempts WHERE ts<?", (time.time() - LOGIN_WINDOW,))
        if scalar("SELECT COUNT(*) FROM login_attempts WHERE ip=?", (ip,)) >= LOGIN_MAX:
            flash("Too many failed attempts. Try again in 15 minutes.", "err")
        else:
            a = one("SELECT * FROM admins WHERE email=?", (fv("email").lower(),))
            if a and check_password_hash(a["password_hash"], request.form.get("password", "")):
                csrf_keep = session.get("csrf"); session.clear(); session["csrf"] = csrf_keep; session["admin_id"] = a["id"]
                run("DELETE FROM login_attempts WHERE ip=?", (ip,))
                return back()
            run("INSERT INTO login_attempts(ip,ts) VALUES(?,?)", (ip, time.time()))
            flash("Invalid email or password.", "err")
    return render_template("admin/login.html", setup=False)

@bp.route("/logout", methods=["POST"])
def logout():
    session.clear(); return back("admin.login")

# ---- dashboard ----
@bp.route("/")
@login_required
def dashboard():
    st = {"Applications": scalar("SELECT COUNT(*) FROM apps WHERE type='app'"), "Games": scalar("SELECT COUNT(*) FROM apps WHERE type='game'"),
          "Total downloads": scalar("SELECT COALESCE(SUM(download_count),0) FROM app_versions"), "Categories": scalar("SELECT COUNT(*) FROM categories"),
          "Articles": scalar("SELECT COUNT(*) FROM articles"), "Reviews": scalar("SELECT COUNT(*) FROM reviews"), "Pending reviews": scalar("SELECT COUNT(*) FROM reviews WHERE is_approved=0")}
    return render_template("admin/dashboard.html", st=st, recent=query("SELECT id,name,type,status,created_at FROM apps ORDER BY id DESC LIMIT 8"))

# ---- apps & games ----
@bp.route("/items/<type_>", methods=["GET"])
@login_required
def items(type_):
    if type_ not in ("app", "game"): abort(404)
    s = request.args.get("s", "").strip()
    rows = query("""SELECT a.id,a.name,a.status,c.name cat,(SELECT version FROM app_versions v WHERE v.app_id=a.id ORDER BY is_latest DESC,id DESC LIMIT 1) ver,
      (SELECT COALESCE(SUM(download_count),0) FROM app_versions v WHERE v.app_id=a.id) dl FROM apps a LEFT JOIN categories c ON c.id=a.category_id
      WHERE a.type=? AND a.name LIKE ? ORDER BY a.id DESC LIMIT 200""", (type_, f"%{s}%"))
    return render_template("admin/items.html", rows=rows, type_=type_, s=s)

@bp.route("/items/<type_>/new")
@bp.route("/items/<type_>/<int:aid>/edit")
@login_required
def item_form(type_, aid=None):
    if type_ not in ("app", "game"): abort(404)
    a = one("SELECT * FROM apps WHERE id=? AND type=?", (aid, type_)) if aid else None
    if aid and not a: abort(404)
    shots = query("SELECT id,file FROM screenshots WHERE app_id=? ORDER BY sort_order", (aid,)) if aid else []
    return render_template("admin/item_form.html", a=a or {}, type_=type_, shots=shots, cats=query("SELECT id,name FROM categories WHERE type=? ORDER BY name", (type_,)))

@bp.route("/items/<type_>/save", methods=["POST"])
@login_required
def item_save(type_):
    if type_ not in ("app", "game"): abort(404)
    aid = request.form.get("id", type=int) or 0
    try:
        name = fv("name", 150)
        if not name: raise ValueError("Name is required.")
        slug = unique_slug("apps", fv("slug") or name, aid)
        vals = [request.form.get("category_id", type=int) or None, name, slug, fv("developer", 150), fv("package_name", 190) or None, fv("short_description", 300),
                fv("description"), fv("features"), fv("install_guide"), fv("min_android", 30) or "5.0", "published" if fv("status") == "published" else "draft",
                chk("is_featured"), chk("is_trending"), chk("is_editor_choice"), fv("meta_title", 190) or None, fv("meta_description", 300) or None,
                fv("meta_keywords", 300) or None, fv("canonical_url", 300) or None, chk("noindex")]
        icon, og = save_image(request.files.get("icon"), "icons"), save_image(request.files.get("og_image"), "articles")
        cols = "category_id,name,slug,developer,package_name,short_description,description,features,install_guide,min_android,status,is_featured,is_trending,is_editor_choice,meta_title,meta_description,meta_keywords,canonical_url,noindex".split(",")
        if aid:
            old = one("SELECT icon,og_image FROM apps WHERE id=?", (aid,))
            run("UPDATE apps SET " + ",".join(c + "=?" for c in cols) + ",updated_at=CURRENT_TIMESTAMP WHERE id=?", (*vals, aid))
            if icon: delete_upload(old["icon"]); run("UPDATE apps SET icon=? WHERE id=?", (icon, aid))
            if og: delete_upload(old["og_image"]); run("UPDATE apps SET og_image=? WHERE id=?", (og, aid))
        else:
            aid = run("INSERT INTO apps(" + ",".join(cols) + ",type,icon,og_image) VALUES(" + ",".join("?" * (len(cols) + 3)) + ")", (*vals, type_, icon, og)).lastrowid
        n = scalar("SELECT COALESCE(MAX(sort_order),0) FROM screenshots WHERE app_id=?", (aid,))
        for f in request.files.getlist("shots"):
            p = save_image(f, "screenshots")
            if p: n += 1; run("INSERT INTO screenshots(app_id,file,sort_order) VALUES(?,?,?)", (aid, p, n))
        flash("Saved. Ab 'Versions' se APK upload karo.")
    except Exception as e:
        flash(str(e), "err")
        return back("admin.item_form", type_=type_, aid=aid) if aid else back("admin.item_form", type_=type_)
    return back("admin.item_form", type_=type_, aid=aid)

@bp.route("/items/<type_>/<int:aid>/delete", methods=["POST"])
@login_required
def item_delete(type_, aid):
    a = one("SELECT icon,og_image FROM apps WHERE id=?", (aid,))
    if a:
        for s in query("SELECT file FROM screenshots WHERE app_id=?", (aid,)): delete_upload(s["file"])
        for v in query("SELECT apk_file FROM app_versions WHERE app_id=?", (aid,)): delete_apk(v["apk_file"])
        delete_upload(a["icon"]); delete_upload(a["og_image"]); run("DELETE FROM apps WHERE id=?", (aid,))
    flash("Deleted."); return back("admin.items", type_=type_)

@bp.route("/shots/<int:sid>/delete", methods=["POST"])
@login_required
def shot_delete(sid):
    s = one("SELECT s.*,a.type FROM screenshots s JOIN apps a ON a.id=s.app_id WHERE s.id=?", (sid,))
    if not s: abort(404)
    delete_upload(s["file"]); run("DELETE FROM screenshots WHERE id=?", (sid,))
    flash("Screenshot removed."); return back("admin.item_form", type_=s["type"], aid=s["app_id"])

# ---- versions ----
@bp.route("/versions/<int:aid>")
@login_required
def versions(aid):
    a = one("SELECT id,name,type FROM apps WHERE id=?", (aid,))
    if not a: abort(404)
    e = one("SELECT * FROM app_versions WHERE id=? AND app_id=?", (request.args.get("edit", type=int), aid)) if request.args.get("edit") else None
    return render_template("admin/versions.html", a=a, e=e or {}, rows=query("SELECT * FROM app_versions WHERE app_id=? ORDER BY is_latest DESC, release_date DESC, id DESC", (aid,)), today=date.today().isoformat())

@bp.route("/versions/<int:aid>/save", methods=["POST"])
@login_required
def version_save(aid):
    vid = request.form.get("vid", type=int) or 0
    if not scalar("SELECT COUNT(*) FROM apps WHERE id=?", (aid,)): abort(404)
    new_apk = None
    try:
        ver = fv("version", 40)
        if not ver: raise ValueError("Version is required.")
        d = fv("release_date") or date.today().isoformat(); datetime.strptime(d, "%Y-%m-%d")
        ext = fv("external_url", 500)
        if ext and not ext.lower().startswith("https://"): raise ValueError("External URL must start with https://")
        old = one("SELECT * FROM app_versions WHERE id=? AND app_id=?", (vid, aid)) if vid else None
        f = request.files.get("apk")
        file, size, sha = (old["apk_file"], old["file_size"], old["sha256"]) if old else (None, 0, None)
        if f and f.filename:
            file, size, sha = save_apk(f); new_apk = file
        elif fv("size_mb"): size = int(float(fv("size_mb")) * 1048576)
        if not file and not ext: raise ValueError("Upload an APK or give an external https URL.")
        others = scalar("SELECT COUNT(*) FROM app_versions WHERE app_id=? AND id<>?", (aid, vid))
        latest = 1 if (chk("is_latest") or not others) else (old["is_latest"] if old else 0)
        if latest: run("UPDATE app_versions SET is_latest=0 WHERE app_id=?", (aid,))
        args = (ver, file, ext or None, size, sha, fv("min_android", 30) or "5.0", fv("whats_new"), d, latest)
        if old:
            run("UPDATE app_versions SET version=?,apk_file=?,external_url=?,file_size=?,sha256=?,min_android=?,whats_new=?,release_date=?,is_latest=? WHERE id=?", (*args, vid))
            if new_apk and old["apk_file"]: delete_apk(old["apk_file"])
        else:
            run("INSERT INTO app_versions(version,apk_file,external_url,file_size,sha256,min_android,whats_new,release_date,is_latest,app_id) VALUES(?,?,?,?,?,?,?,?,?,?)", (*args, aid))
        run("UPDATE apps SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (aid,))
        flash("Version saved.")
    except Exception as ex:
        if new_apk: delete_apk(new_apk)
        flash("That version number already exists for this app." if "UNIQUE" in str(ex) else str(ex), "err")
    return back("admin.versions", aid=aid)

@bp.route("/versions/<int:aid>/<int:vid>/delete", methods=["POST"])
@login_required
def version_delete(aid, vid):
    v = one("SELECT apk_file FROM app_versions WHERE id=? AND app_id=?", (vid, aid))
    if v:
        delete_apk(v["apk_file"]); run("DELETE FROM app_versions WHERE id=?", (vid,))
        if not scalar("SELECT COUNT(*) FROM app_versions WHERE app_id=? AND is_latest=1", (aid,)):
            run("UPDATE app_versions SET is_latest=1 WHERE id=(SELECT id FROM app_versions WHERE app_id=? ORDER BY release_date DESC,id DESC LIMIT 1)", (aid,))
    flash("Version deleted."); return back("admin.versions", aid=aid)

# ---- categories ----
@bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    if request.method == "POST":
        act, cid = fv("action"), request.form.get("id", type=int) or 0
        if act == "delete": run("DELETE FROM categories WHERE id=?", (cid,)); flash("Category deleted.")
        elif fv("name"):
            t = "game" if fv("type") == "game" else "app"; slug = unique_slug("categories", fv("slug") or fv("name"), cid)
            if cid: run("UPDATE categories SET name=?,type=?,slug=?,description=? WHERE id=?", (fv("name", 100), t, slug, fv("description", 300), cid))
            else: run("INSERT INTO categories(name,type,slug,description) VALUES(?,?,?,?)", (fv("name", 100), t, slug, fv("description", 300)))
            flash("Category saved.")
        return back("admin.categories")
    e = one("SELECT * FROM categories WHERE id=?", (request.args.get("edit", type=int),)) if request.args.get("edit") else None
    return render_template("admin/categories.html", e=e or {}, rows=query("SELECT c.*,(SELECT COUNT(*) FROM apps a WHERE a.category_id=c.id) n FROM categories c ORDER BY type,name"))

# ---- reviews ----
@bp.route("/reviews", methods=["GET", "POST"])
@login_required
def reviews():
    if request.method == "POST":
        rid, act = request.form.get("id", type=int) or 0, fv("action")
        if act == "approve": run("UPDATE reviews SET is_approved=1 WHERE id=?", (rid,))
        elif act == "unapprove": run("UPDATE reviews SET is_approved=0 WHERE id=?", (rid,))
        elif act == "delete": run("DELETE FROM reviews WHERE id=?", (rid,))
        return back("admin.reviews")
    return render_template("admin/reviews.html", rows=query("SELECT r.*,a.name app FROM reviews r JOIN apps a ON a.id=r.app_id ORDER BY r.is_approved, r.id DESC LIMIT 200"))

# ---- articles ----
@bp.route("/articles", methods=["GET", "POST"])
@login_required
def articles():
    if request.method == "POST":
        act = fv("action")
        if act == "delete":
            a = one("SELECT featured_image FROM articles WHERE id=?", (request.form.get("id", type=int),))
            if a: delete_upload(a["featured_image"]); run("DELETE FROM articles WHERE id=?", (request.form.get("id", type=int),))
            flash("Article deleted.")
        elif act == "add_cat" and fv("name"): run("INSERT INTO article_categories(name,slug) VALUES(?,?)", (fv("name", 100), unique_slug("article_categories", fv("name")))); flash("Category added.")
        return back("admin.articles")
    return render_template("admin/articles.html", rows=query("SELECT id,title,status,published_at FROM articles ORDER BY id DESC LIMIT 200"))

@bp.route("/articles/new")
@bp.route("/articles/<int:nid>/edit")
@login_required
def article_form(nid=None):
    a = one("SELECT * FROM articles WHERE id=?", (nid,)) if nid else None
    if nid and not a: abort(404)
    pub = (a["published_at"] or "")[:16].replace(" ", "T") if a else ""
    return render_template("admin/article_form.html", a=a or {}, pub=pub, cats=query("SELECT id,name FROM article_categories ORDER BY name"))

@bp.route("/articles/save", methods=["POST"])
@login_required
def article_save():
    nid = request.form.get("id", type=int) or 0
    try:
        title = fv("title", 190)
        if not title: raise ValueError("Title is required.")
        status = "published" if fv("status") == "published" else "draft"
        old = one("SELECT * FROM articles WHERE id=?", (nid,)) if nid else None
        pub = fv("published_at").replace("T", " ") or (old["published_at"] if old else None)
        if pub: pub = pub[:16] + ":00" if len(pub) == 16 else pub
        if status == "published" and not pub: pub = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        img = save_image(request.files.get("image"), "articles")
        vals = (request.form.get("category_id", type=int) or None, title, unique_slug("articles", fv("slug") or title, nid), fv("excerpt", 300), fv("content"), chk("is_featured"),
                status, pub, fv("meta_title", 190) or None, fv("meta_description", 300) or None, fv("meta_keywords", 300) or None, fv("canonical_url", 300) or None, chk("noindex"))
        cols = "category_id,title,slug,excerpt,content,is_featured,status,published_at,meta_title,meta_description,meta_keywords,canonical_url,noindex".split(",")
        if nid:
            run("UPDATE articles SET " + ",".join(c + "=?" for c in cols) + ",updated_at=CURRENT_TIMESTAMP WHERE id=?", (*vals, nid))
            if img: delete_upload(old["featured_image"]); run("UPDATE articles SET featured_image=? WHERE id=?", (img, nid))
        else:
            run("INSERT INTO articles(" + ",".join(cols) + ",featured_image,author_id) VALUES(" + ",".join("?" * (len(cols) + 2)) + ")", (*vals, img, session["admin_id"]))
        flash("Article published." if status == "published" else "Draft saved."); return back("admin.articles")
    except Exception as e:
        flash(str(e), "err"); return back("admin.article_form", nid=nid) if nid else back("admin.article_form")

# ---- settings ----
TEXT_KEYS = ["site_name", "site_tagline", "site_description", "contact_email", "facebook", "twitter", "telegram", "ga_id", "ad_code", "footer_text", "default_meta_title", "default_meta_description"]
@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    if request.method == "POST":
        try:
            for k in TEXT_KEYS: run("INSERT INTO website_settings VALUES(?,?) ON CONFLICT(skey) DO UPDATE SET svalue=excluded.svalue", (k, fv(k)))
            for k in ("logo", "favicon", "default_og_image"):
                p = save_image(request.files.get(k), "site")
                if p: delete_upload(setting(k)); run("INSERT INTO website_settings VALUES(?,?) ON CONFLICT(skey) DO UPDATE SET svalue=excluded.svalue", (k, p))
            flash("Settings saved.")
        except Exception as e: flash(str(e), "err")
        return back("admin.settings_page")
    labels = {"site_name": "Website name", "site_tagline": "Tagline", "contact_email": "Contact email", "facebook": "Facebook URL", "twitter": "X/Twitter URL", "telegram": "Telegram URL",
              "ga_id": "Google Analytics ID (G-XXXX)", "default_meta_title": "Default meta title"}
    big = {"site_description": "Website description", "footer_text": "Footer text", "default_meta_description": "Default meta description", "ad_code": "Advertisement code (HTML/JS)"}
    return render_template("admin/settings.html", labels=labels, big=big)
