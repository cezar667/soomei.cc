from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
import os, json, html, qrcode, io, hashlib, time, secrets

app = FastAPI(title="Soomei Card API v2")

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data.json")
WEB  = os.path.join(BASE, "..", "web")
app.mount("/static", StaticFiles(directory=WEB), name="static")

# Base pública para gerar URLs (QR/vCard). Defina PUBLIC_BASE_URL para ambiente.
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "https://soomei.cc").rstrip("/")

# Diretório para uploads simples (servidos por /static)
UPLOADS = os.path.join(WEB, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

# --- util json "db" (MVP: troque por Postgres depois) ---
def load():
    if os.path.exists(DATA):
        with open(DATA,"r",encoding="utf-8") as f: return json.load(f)
    return {"users":{}, "cards":{}, "profiles":{}, "sessions":{}, "verify_tokens":{}}

def save(db):
    with open(DATA,"w",encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False, indent=2)

def h(p): return hashlib.scrypt(p.encode(), salt=b"soomei", n=2**14, r=8, p=1).hex()

def db_defaults(db):
    db.setdefault("users", {})
    db.setdefault("cards", {})
    db.setdefault("profiles", {})
    db.setdefault("sessions", {})
    db.setdefault("verify_tokens", {})
    return db

def issue_session(db, email: str):
    token = secrets.token_urlsafe(32)
    db["sessions"][token] = {"email": email, "ts": int(time.time())}
    save(db)
    return token

def current_user_email(request: Request):
    token = request.cookies.get("session")
    if not token: return None
    db = load(); db_defaults(db)
    s = db["sessions"].get(token)
    return s.get("email") if s else None

# --- onboarding (primeiro acesso com PIN da carta) ---
@app.get("/onboard/{uid}", response_class=HTMLResponse)
def onboard(uid: str):
    return HTMLResponse(f"""
    <!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='/static/card.css'><title>Ativar cartão</title></head>
    <body><main class='wrap'>
      <h1>Ativar cartão</h1>
      <p>UID: <b>{html.escape(uid)}</b></p>
      <form method='post' action='/auth/register'>
        <input type='hidden' name='uid' value='{html.escape(uid)}'>
        <label>Email</label><input name='email' type='email' required>
        <label>PIN da carta</label><input name='pin' type='password' required>
        <label>Nova senha</label><input name='password' type='password' required>
        <label>Slug (opcional)</label><input name='vanity' placeholder='seu-nome'>
        <label><input type='checkbox' name='lgpd' required> Concordo com os Termos e LGPD</label>
        <button class='btn'>Criar conta</button>
      </form>
      <p class='muted'>Já tem conta? <a href='/login?uid={html.escape(uid)}'>Entrar</a></p>
    </main></body></html>
    """)

@app.get("/login", response_class=HTMLResponse)
def login(uid: str = "", error: str = ""):
    return HTMLResponse(f"""
    <!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
    <link rel='stylesheet' href='/static/card.css'><title>Entrar</title></head>
    <body><main class='wrap'>
      <h1>Entrar</h1>
      <form method='post' action='/auth/login'>
        <input type='hidden' name='uid' value='{html.escape(uid)}'>
        <label>Email</label><input name='email' type='email' required>
        <label>Senha</label><input name='password' type='password' required>
        <button class='btn'>Entrar</button>
      </form>
      <p style='color:#f88'>{html.escape(error)}</p>
    </main></body></html>
    """)

@app.post("/auth/register")
def register(uid: str = Form(...), email: str = Form(...), pin: str = Form(...),
            password: str = Form(...), vanity: str = Form(""), lgpd: str = Form(None)):
    if not lgpd: raise HTTPException(400, "É necessário aceitar os termos")
    db = db_defaults(load())
    card = db["cards"].get(uid) or {"uid":uid, "status":"pending", "pin":"123456"}
    if pin != card.get("pin","123456"):
        return RedirectResponse(f"/onboard/{uid}", status_code=303)
    if email in db["users"]:
        return RedirectResponse(f"/login?uid={uid}&error=Conta%20já%20existe", status_code=303)
    # cria user
    db["users"][email] = {"email":email, "pwd":h(password), "email_verified_at":None}
    # define vanity (se disponível)
    if vanity and any(c.get("vanity")==vanity for c in db["cards"].values()):
        return HTMLResponse("Slug indisponível, tente outro.", status_code=400)
    # ativa card
    card.update({"status":"active", "billing_status":"ok", "user":email})
    if vanity: card["vanity"] = vanity
    db["cards"][uid] = card
    # perfil básico
    db["profiles"][email] = {"full_name":"Seu Nome", "title":"Cargo", "links":[], "whatsapp":"", "pix_key":"", "email_public":""}
    # token de verificação de email
    token = secrets.token_urlsafe(24)
    db["verify_tokens"][token] = {"email": email, "created_at": int(time.time())}
    save(db)
    verify_url = f"/auth/verify?token={html.escape(token)}"
    dest = card.get("vanity", uid)
    return HTMLResponse(f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='/static/card.css'><title>Confirme seu email</title>
    </head><body><main class='wrap'>
      <h1>Confirme seu email</h1>
      <p>Enviamos um link de verificação para <b>{html.escape(email)}</b>.</p>
      <p class='muted'>Ambiente de desenvolvimento: você pode clicar aqui para confirmar agora:</p>
      <p><a class='btn' href='{verify_url}'>Confirmar email</a></p>
      <p>Depois de confirmar, você será direcionado ao cartão <code>/{html.escape(dest)}</code>.</p>
    </main></body></html>
    """)

@app.post("/auth/login")
def do_login(uid: str = Form(""), email: str = Form(...), password: str = Form(...)):
    db = db_defaults(load())
    u = db["users"].get(email)
    if not u or u["pwd"] != h(password):
        return RedirectResponse(f"/login?uid={uid}&error=Credenciais%20inv%C3%A1lidas", status_code=303)
    if not u.get("email_verified_at"):
        token = None
        for t, meta in db.get("verify_tokens", {}).items():
            if meta.get("email") == email:
                token = t; break
        link = f"/auth/verify?token={token}" if token else f"/login?uid={uid}&error=Email%20n%C3%A3o%20verificado"
        return RedirectResponse(link, status_code=303)
    # redireciona para o primeiro card do usuário ou o UID recebido
    target = None
    if uid and uid in db["cards"]: target = db["cards"][uid].get("vanity", uid)
    else:
        for k,v in db["cards"].items():
            if v.get("user")==email: target = v.get("vanity", k); break
    token = issue_session(db, email)
    resp = RedirectResponse(f"/{target}", status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax")
    return resp

@app.get("/auth/verify")
def verify_email(token: str):
    db = db_defaults(load())
    meta = db["verify_tokens"].pop(token, None)
    if not meta:
        return HTMLResponse("Token inválido ou expirado.", status_code=400)
    email = meta.get("email")
    if email in db["users"]:
        db["users"][email]["email_verified_at"] = int(time.time())
        save(db)
        # encontra primeiro card do usuário
        dest = None
        for k,v in db["cards"].items():
            if v.get("user")==email:
                dest = v.get("vanity", k); break
        token_sess = issue_session(db, email)
        resp = RedirectResponse(f"/{dest}", status_code=303)
        resp.set_cookie("session", token_sess, httponly=True, samesite="lax")
        return resp
    return HTMLResponse("Usuário não encontrado para este token.", status_code=400)

@app.post("/auth/logout")
def logout(request: Request):
    db = db_defaults(load())
    token = request.cookies.get("session")
    if token and token in db["sessions"]:
        db["sessions"].pop(token, None); save(db)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp

def find_card_by_slug(slug: str):
    db = load()
    for uid, c in db["cards"].items():
        if c.get("vanity")==slug: return db, uid, c
    if slug in db["cards"]: return db, slug, db["cards"][slug]
    return db, None, None

@app.get("/u/{slug}", response_class=HTMLResponse)
def profile_complete(prof: dict) -> bool:
    if not prof: return False
    has_name = bool(prof.get("full_name") and prof.get("full_name") != "Seu Nome")
    has_contact = bool(prof.get("whatsapp") or prof.get("email_public") or (prof.get("links") and len(prof.get("links"))>0))
    return has_name and has_contact

def public_card(slug: str, request: Request):
    db, uid, card = find_card_by_slug(slug)
    if not card: raise HTTPException(404, "Cartão não encontrado")
    prof = db["profiles"].get(card.get("user",""), {})
    photo = html.escape(prof.get("photo_url","")) if prof else ""
    links = "".join([f"<li><a href='{html.escape(l.get('href',''))}' target='_blank'>{html.escape(l.get('label',''))}</a></li>" for l in prof.get("links",[])])
    banner = ""
    if card.get("billing_status") == "late":
        banner = "<div class='banner'>Sua associação está em atraso. Regularize para manter seu cartão ativo.</div>"
    html_doc = f"""<!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>{html.escape(prof.get('full_name','Card'))} — Soomei</title>
    <link rel='stylesheet' href='/static/card.css'></head><body>
    <main class='wrap'>
      {banner}
      <header>
        {f"<img src='{photo}' alt='foto' style='width:96px;height:96px;border-radius:50%;object-fit:cover;margin-bottom:12px'>" if photo else ""}
        <h1>{html.escape(prof.get('full_name',''))}</h1>
        <p class='bio'>{html.escape(prof.get('title',''))}</p>
      </header>
      <ul class='links'>{links}</ul>
      <div class='actions'><a class='btn' href='/v/{html.escape(slug)}.vcf'>Salvar na Agenda</a></div>
      <footer><a href='/login' class='muted'>Entrar</a> · <a class='muted' href='/onboard/{html.escape(uid)}'>Criar Conta</a></footer>
    </main></body></html>"""
    return HTMLResponse(html_doc)

@app.get("/q/{slug}.png")
def qr(slug: str):
    img = qrcode.make(f"{PUBLIC_BASE}/{slug}")
    buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.get("/v/{slug}.vcf")
def vcard(slug: str):
    db, uid, card = find_card_by_slug(slug)
    if not card: raise HTTPException(404, "Cartão não encontrado")
    prof = db["profiles"].get(card.get("user",""), {})
    name = prof.get("full_name",""); tel = prof.get("whatsapp",""); email = prof.get("email_public","")
    url = f"{PUBLIC_BASE}/{slug}"
    vcf = f"BEGIN:VCARD\nVERSION:3.0\nN:{name};;;;\nFN:{name}\nORG:Soomei\nTITLE:{prof.get('title','')}\nTEL;TYPE=CELL:{tel}\nEMAIL;TYPE=INTERNET:{email}\nURL:{url}\nEND:VCARD\n"
    return PlainTextResponse(vcf, media_type="text/vcard")

# Webhook do TheMembers → governa billing/status
@app.post("/hooks/themembers")
def hook(payload: dict):
    # payload: {"uid":"abc123","status":"ok|late|delinquent|blocked"}
    db = load()
    uid = payload.get("uid")
    if not uid or uid not in db["cards"]: return {"ok": True}
    st = payload.get("status","ok")
    c = db["cards"][uid]; c["billing_status"] = st
    if st in ("blocked","delinquent"): c["status"]="blocked"
    elif st=="ok": c["status"]="active"
    db["cards"][uid] = c; save(db)
    return {"ok": True}

# --- Edição do cartão (dono) ---
@app.get("/edit/{slug}", response_class=HTMLResponse)
def edit_card(slug: str, request: Request):
    db, uid, card = find_card_by_slug(slug)
    if not card: raise HTTPException(404, "Cartão não encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner: return RedirectResponse(f"/{slug}", status_code=303)
    prof = load()["profiles"].get(owner, {})
    links = prof.get("links", [])
    while len(links) < 3: links.append({"label":"","href":""})
    html_form = f"""
    <!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='/static/card.css'><title>Editar cartão</title></head>
    <body><main class='wrap'>
      <h1>Editar cartão</h1>
      <form method='post' action='/edit/{html.escape(slug)}' enctype='multipart/form-data'>
        <label>Nome completo</label><input name='full_name' value='{html.escape(prof.get('full_name',''))}' required>
        <label>Cargo</label><input name='title' value='{html.escape(prof.get('title',''))}'>
        <label>WhatsApp</label><input name='whatsapp' value='{html.escape(prof.get('whatsapp',''))}'>
        <label>Email público</label><input name='email_public' type='email' value='{html.escape(prof.get('email_public',''))}'>
        <label>Foto (jpg/png)</label><input type='file' name='photo'>
        <h3>Links</h3>
        <label>Rótulo 1</label><input name='label1' value='{html.escape(links[0].get('label',''))}'>
        <label>URL 1</label><input name='href1' value='{html.escape(links[0].get('href',''))}'>
        <label>Rótulo 2</label><input name='label2' value='{html.escape(links[1].get('label',''))}'>
        <label>URL 2</label><input name='href2' value='{html.escape(links[1].get('href',''))}'>
        <label>Rótulo 3</label><input name='label3' value='{html.escape(links[2].get('label',''))}'>
        <label>URL 3</label><input name='href3' value='{html.escape(links[2].get('href',''))}'>
        <button class='btn'>Salvar</button>
      </form>
      <p><a class='muted' href='/{html.escape(slug)}'>Voltar</a> · <a class='muted' href='/auth/logout'>Sair</a></p>
    </main></body></html>
    """
    return HTMLResponse(html_form)

@app.post("/edit/{slug}")
async def save_edit(slug: str, request: Request, full_name: str = Form(""), title: str = Form(""),
               whatsapp: str = Form(""), email_public: str = Form(""),
               label1: str = Form(""), href1: str = Form(""),
               label2: str = Form(""), href2: str = Form(""),
               label3: str = Form(""), href3: str = Form(""),
               photo: UploadFile | None = File(None)):
    db, uid, card = find_card_by_slug(slug)
    if not card: raise HTTPException(404, "Cartão não encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner: return RedirectResponse(f"/{slug}", status_code=303)
    db2 = db_defaults(load())
    prof = db2["profiles"].get(owner, {})
    prof.update({
        "full_name": full_name.strip(),
        "title": title.strip(),
        "whatsapp": whatsapp.strip(),
        "email_public": email_public.strip(),
    })
    links = []
    for (lbl, href) in [(label1, href1), (label2, href2), (label3, href3)]:
        if lbl.strip() and href.strip(): links.append({"label": lbl.strip(), "href": href.strip()})
    prof["links"] = links
    if photo and photo.filename:
        ct = (photo.content_type or "").lower()
        if ct not in ("image/jpeg", "image/png"):
            return HTMLResponse("Formato de imagem não suportado (use JPEG ou PNG)", status_code=400)
        ext = ".jpg" if ct == "image/jpeg" else ".png"
        filename = f"{uid}{ext}"
        dest_path = os.path.join(UPLOADS, filename)
        data = await photo.read()
        with open(dest_path, "wb") as f:
            f.write(data)
        prof["photo_url"] = f"/static/uploads/{filename}"
    db2["profiles"][owner] = prof
    save(db2)
    return RedirectResponse(f"/{slug}", status_code=303)

@app.get("/blocked", response_class=HTMLResponse)
def blocked():
    return HTMLResponse("""
    <!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='/static/card.css'><title>Cartão bloqueado</title></head>
    <body><main class='wrap'>
      <h1>Cartão bloqueado</h1>
      <p>Entre em contato com o suporte para mais informações.</p>
    </main></body></html>
    """)

@app.get("/{slug}", response_class=HTMLResponse)
def root_slug(slug: str, request: Request):
    db, uid, card = find_card_by_slug(slug)
    if not card or not card.get("status") or card.get("status") == "pending":
        return RedirectResponse(f"/onboard/{html.escape(uid or slug)}", status_code=302)
    if card.get("status") == "blocked":
        return RedirectResponse("/blocked", status_code=302)
    owner = card.get("user", "")
    prof = load()["profiles"].get(owner, {})
    who = current_user_email(request)
    if who == owner and not profile_complete(prof):
        return RedirectResponse(f"/edit/{html.escape(slug)}", status_code=302)
    if who != owner:
        if not profile_complete(prof):
            return HTMLResponse("""
            <!doctype html><html lang='pt-br'><head>
            <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
            <link rel='stylesheet' href='/static/card.css'><title>Em construção</title></head>
            <body><main class='wrap'>
              <h1>Cartão digital em construção</h1>
              <p>O proprietário ainda não finalizou o preenchimento deste cartão.</p>
              <p class='muted'><a href='/login'>Sou o dono? Entrar</a></p>
            </main></body></html>
            """)
        return public_card(slug, request)
    links = "".join([f"<li><a href='{html.escape(l.get('href',''))}' target='_blank'>{html.escape(l.get('label',''))}</a></li>" for l in prof.get("links",[])])
    photo = html.escape(prof.get("photo_url","")) if prof else ""
    html_doc = f"""<!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='/static/card.css'><title>Meu cartão</title></head><body>
    <main class='wrap'>
      <h1>Seu cartão</h1>
      {f"<img src='{photo}' alt='foto' style='width:96px;height:96px;border-radius:50%;object-fit:cover;margin-bottom:12px'>" if photo else ""}
      <p><b>Nome:</b> {html.escape(prof.get('full_name',''))}</p>
      <p><b>Cargo:</b> {html.escape(prof.get('title',''))}</p>
      <p><b>WhatsApp:</b> {html.escape(prof.get('whatsapp',''))}</p>
      <p><b>Email público:</b> {html.escape(prof.get('email_public',''))}</p>
      <h3>Links</h3>
      <ul class='links'>{links}</ul>
      <div class='actions'>
        <a class='btn' href='/edit/{html.escape(slug)}'>Editar</a>
        <a class='btn' href='/v/{html.escape(slug)}.vcf'>Baixar vCard</a>
      </div>
      <p class='muted'><a href='/auth/logout'>Sair</a> · URL pública: <code>/{html.escape(slug)}</code></p>
    </main></body></html>"""
    return HTMLResponse(html_doc)
