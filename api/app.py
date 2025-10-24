from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import os, json, html, qrcode, io, hashlib, time

app = FastAPI(title="Soomei Card API v2")

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data.json")
WEB  = os.path.join(BASE, "..", "web")
app.mount("/static", StaticFiles(directory=WEB), name="static")

# Base pública para gerar URLs (QR/vCard). Defina PUBLIC_BASE_URL para ambiente.
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "https://soomei.cc").rstrip("/")

# --- util json "db" (MVP: troque por Postgres depois) ---
def load():
    if os.path.exists(DATA):
        with open(DATA,"r",encoding="utf-8") as f: return json.load(f)
    return {"users":{}, "cards":{}, "profiles":{}}

def save(db):
    with open(DATA,"w",encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False, indent=2)

def h(p): return hashlib.scrypt(p.encode(), salt=b"soomei", n=2**14, r=8, p=1).hex()

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
    db = load()
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
    save(db)
    return RedirectResponse("/u/"+(card.get("vanity", uid)), status_code=303)

@app.post("/auth/login")
def do_login(uid: str = Form(""), email: str = Form(...), password: str = Form(...)):
    db = load()
    u = db["users"].get(email)
    if not u or u["pwd"] != h(password):
        return RedirectResponse(f"/login?uid={uid}&error=Credenciais%20inv%C3%A1lidas", status_code=303)
    # redireciona para o primeiro card do usuário ou o UID recebido
    target = None
    if uid and uid in db["cards"]: target = db["cards"][uid].get("vanity", uid)
    else:
        for k,v in db["cards"].items():
            if v.get("user")==email: target = v.get("vanity", k); break
    return RedirectResponse(f"/u/{target}", status_code=303)

def find_card_by_slug(slug: str):
    db = load()
    for uid, c in db["cards"].items():
        if c.get("vanity")==slug: return db, uid, c
    if slug in db["cards"]: return db, slug, db["cards"][slug]
    return db, None, None

@app.get("/u/{slug}", response_class=HTMLResponse)
def public_card(slug: str, request: Request):
    db, uid, card = find_card_by_slug(slug)
    if not card: raise HTTPException(404, "Cartão não encontrado")
    prof = db["profiles"].get(card.get("user",""), {})
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
