"""
pokemon_server.py – Pokémon Shiny Sprite Stream Overlay
────────────────────────────────────────────────────────
Routes:
  /        → OBS Browser Source overlay (transparent bg, shiny sprite + name)
  /config  → Web config panel (password protected)
  /api/state  → GET current state (JSON)
  /api/config → POST new settings

Requirements:  pip install flask requests
Run:           python pokemon_server.py

Set password:  OVERLAY_PASSWORD=yourpass python pokemon_server.py
Default pass:  admin123

Sprites come from the free PokéAPI / PokeSprites (no API key needed).
"""

from flask import Flask, jsonify, render_template_string, request, session, redirect, url_for
import requests as req
import threading
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pkmn-secret-change-me")

ADMIN_PASSWORD = os.environ.get("OVERLAY_PASSWORD", "admin123")
PORT = int(os.environ.get("PORT", os.environ.get("OVERLAY_PORT", 5051)))

# ── Shared state ──────────────────────────────────────────────────────────────
state = {
    "pokemon":        "charizard",
    "display_name":   "Charizard",
    "sprite_url":     "",
    "name_color":     "#f5c518",
    "glow_color":     "#f5c518",
    "bg_color":       "#0a0a0a",
    "show_name":      True,
    "show_shiny_tag": True,
    "name_size":      28,
    "sprite_size":    160,
    "glow_strength":  12,
    "error":          "",
}
state_lock = threading.Lock()

POKEAPI = "https://pokeapi.co/api/v2/pokemon/{}"

def fetch_pokemon(name: str):
    """Fetch shiny sprite URL + proper display name from PokéAPI.
    Retries up to 4 times on transient network errors."""
    import time
    name = name.strip().lower().replace(" ", "-")
    last_err = ""
    for attempt in range(4):
        try:
            r = req.get(POKEAPI.format(name), timeout=10)
            r.raise_for_status()
            data = r.json()
            sprite = (
                data["sprites"].get("other", {})
                               .get("official-artwork", {})
                               .get("front_shiny")
                or data["sprites"].get("front_shiny")
                or data["sprites"].get("front_default")
                or ""
            )
            display = data["name"].replace("-", " ").title()
            return sprite, display, ""
        except req.HTTPError as e:
            if e.response.status_code == 404:
                return "", "", f"Pokémon '{name}' not found."
            last_err = f"HTTP {e.response.status_code}"
        except Exception as e:
            last_err = str(e)
        if attempt < 3:
            time.sleep(2 ** attempt)
    return "", "", last_err

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def overlay():
    return render_template_string(OVERLAY_HTML)

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify(dict(state))

@app.route("/api/config", methods=["POST"])
def api_config():
    if not session.get("authed"):
        if request.headers.get("X-Overlay-Password") != ADMIN_PASSWORD:
            return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(force=True)

    new_pokemon = data.get("pokemon", "").strip().lower()
    with state_lock:
        old_pokemon = state["pokemon"]

    # Fetch sprite if pokemon changed
    sprite, display, err = "", "", ""
    if new_pokemon and new_pokemon != old_pokemon:
        sprite, display, err = fetch_pokemon(new_pokemon)

    with state_lock:
        if new_pokemon:
            state["pokemon"] = new_pokemon
        if sprite:
            state["sprite_url"]   = sprite
            state["display_name"] = display
        if err:
            state["error"] = err
        elif new_pokemon:
            state["error"] = ""

        for key in ("name_color","glow_color","bg_color","show_name",
                    "show_shiny_tag","name_size","sprite_size","glow_strength"):
            if key in data:
                state[key] = data[key]

    return jsonify({"ok": True, "error": err, "display_name": display, "sprite_url": sprite})

@app.route("/config", methods=["GET","POST"])
def config_panel():
    error = ""
    if not session.get("authed"):
        if request.method == "POST" and "password" in request.form:
            if request.form["password"] == ADMIN_PASSWORD:
                session["authed"] = True
                return redirect(url_for("config_panel"))
            error = "Wrong password."
        return render_template_string(LOGIN_HTML, error=error)
    with state_lock:
        s = dict(state)
    return render_template_string(CONFIG_HTML, state=s)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("config_panel"))

# ── Preload default pokemon on startup (with retry) ──────────────────────────
def preload():
    import time
    for attempt in range(5):
        sprite, display, err = fetch_pokemon("charizard")
        if sprite:
            with state_lock:
                state["sprite_url"]   = sprite
                state["display_name"] = display
                state["error"]        = ""
            return
        time.sleep(2 ** (attempt + 1))
    with state_lock:
        state["error"] = err or "Failed to load default Pokémon after retries."

threading.Thread(target=preload, daemon=True).start()

# ── Login HTML ────────────────────────────────────────────────────────────────
LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Pokémon Overlay – Login</title>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=DM+Sans:wght@400;500&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  background:#0d0f1a;min-height:100vh;display:flex;align-items:center;justify-content:center;
  font-family:'DM Sans',sans-serif;
  background-image:radial-gradient(ellipse at 30% 40%,#f5c51815 0%,transparent 55%);
}
.card{
  background:#12151f;border:1px solid #222840;border-radius:20px;
  padding:48px 40px;width:100%;max-width:380px;
  box-shadow:0 20px 60px rgba(0,0,0,.6);text-align:center;
}
.pokeball{font-size:40px;margin-bottom:16px;display:block;animation:spin 4s linear infinite}
@keyframes spin{0%,100%{transform:rotate(0deg)}50%{transform:rotate(180deg)}}
h1{font-family:'Press Start 2P',monospace;font-size:11px;color:#f5c518;
  margin-bottom:6px;line-height:1.6}
.sub{font-size:12px;color:#4b6070;margin-bottom:32px}
label{display:block;font-size:11px;font-weight:500;color:#6b8090;margin-bottom:6px;text-align:left}
input[type=password]{
  width:100%;padding:12px 16px;background:#1a1e2e;border:1px solid #222840;
  border-radius:10px;color:#fff;font-size:14px;outline:none;
  transition:border-color .2s;margin-bottom:4px;
}
input[type=password]:focus{border-color:#f5c518}
.error{background:#ff2d5515;border:1px solid #ff2d5540;border-radius:8px;
  padding:10px 14px;font-size:12px;color:#ff6b80;margin:10px 0;text-align:left}
button{
  width:100%;margin-top:16px;padding:13px;background:#f5c518;border:none;
  border-radius:10px;color:#0d0f1a;font-family:'Press Start 2P',monospace;
  font-size:9px;cursor:pointer;transition:background .2s,transform .1s;
}
button:hover{background:#e6b800}
button:active{transform:scale(.98)}
</style>
</head>
<body>
<div class="card">
  <span class="pokeball">⬤</span>
  <h1>POKEMON OVERLAY</h1>
  <p class="sub">Enter admin password</p>
  <form method="POST">
    <label>PASSWORD</label>
    <input type="password" name="password" autofocus placeholder="••••••••"/>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <button type="submit">START →</button>
  </form>
</div>
</body>
</html>"""

# ── Config panel HTML ─────────────────────────────────────────────────────────
CONFIG_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Pokémon Overlay Config</title>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0d0f1a;--card:#12151f;--border:#222840;--text:#e2eaf0;--muted:#4b6070;--accent:#f5c518}
body{background:var(--bg);min-height:100vh;font-family:'DM Sans',sans-serif;color:var(--text);
  padding:24px;
  background-image:radial-gradient(ellipse at 80% 10%,#f5c51810 0%,transparent 45%),
                   radial-gradient(ellipse at 10% 80%,#f5c51808 0%,transparent 40%)}

.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;max-width:1000px;margin-left:auto;margin-right:auto}
.brand{font-family:'Press Start 2P',monospace;font-size:9px;color:var(--accent);letter-spacing:.05em}
.logout{font-size:12px;color:var(--muted);text-decoration:none;
  border:1px solid var(--border);padding:6px 14px;border-radius:8px;transition:all .2s}
.logout:hover{color:var(--text);border-color:#f5c51860}

.grid{display:grid;grid-template-columns:1fr 360px;gap:20px;max-width:1000px;margin:0 auto}

.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px;margin-bottom:16px}
.card:last-child{margin-bottom:0}
.card-title{font-family:'Press Start 2P',monospace;font-size:8px;color:var(--accent);
  letter-spacing:.1em;margin-bottom:18px}

.field{margin-bottom:14px}
.field label{display:block;font-size:11px;font-weight:500;color:var(--muted);
  margin-bottom:6px;letter-spacing:.04em}

.search-wrap{display:flex;gap:8px}
input[type=text],input[type=number]{
  width:100%;padding:10px 14px;background:#181c2a;border:1px solid var(--border);
  border-radius:9px;color:var(--text);font-size:13px;outline:none;
  transition:border-color .2s;font-family:'DM Sans',sans-serif;
}
input:focus{border-color:var(--accent)}
.search-btn{
  padding:10px 18px;background:var(--accent);border:none;border-radius:9px;
  color:#0d0f1a;font-family:'Press Start 2P',monospace;font-size:8px;
  cursor:pointer;white-space:nowrap;transition:background .2s;flex-shrink:0;
}
.search-btn:hover{background:#e6b800}
.search-result{font-size:12px;margin-top:8px;min-height:18px}
.search-result.ok{color:#34d399}
.search-result.err{color:#ff6b80}

.color-field{display:flex;align-items:center;gap:10px}
.color-field input[type=color]{
  width:40px;height:36px;padding:2px;background:#181c2a;
  border:1px solid var(--border);border-radius:8px;cursor:pointer;flex-shrink:0;
}
.color-field input[type=text]{flex:1}

.range-row{display:flex;align-items:center;gap:10px}
input[type=range]{width:100%;accent-color:var(--accent);cursor:pointer}
.range-val{font-family:'Press Start 2P',monospace;font-size:9px;color:var(--accent);min-width:36px;text-align:right}

.toggle-row{display:flex;align-items:center;justify-content:space-between;
  padding:10px 0;border-bottom:1px solid var(--border)}
.toggle-row:last-child{border-bottom:none}
.toggle-row span{font-size:13px}
.toggle{position:relative;width:40px;height:22px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;inset:0;background:#1e2d40;border-radius:99px;cursor:pointer;transition:.3s}
.slider:before{content:'';position:absolute;width:16px;height:16px;left:3px;top:3px;
  background:#fff;border-radius:50%;transition:.3s}
input:checked+.slider{background:var(--accent)}
input:checked+.slider:before{transform:translateX(18px)}

.apply-btn{
  width:100%;padding:14px;background:var(--accent);border:none;border-radius:11px;
  color:#0d0f1a;font-family:'Press Start 2P',monospace;font-size:9px;
  cursor:pointer;transition:background .2s,transform .1s;margin-top:4px;
}
.apply-btn:hover{background:#e6b800}
.apply-btn:active{transform:scale(.98)}
.toast{display:none;text-align:center;font-size:12px;color:#34d399;
  padding:10px;border-radius:8px;background:#34d39915;border:1px solid #34d39930;margin-top:10px}

/* ── Right: preview ── */
.preview-panel{position:sticky;top:24px}
.preview-wrap{
  border-radius:12px;overflow:hidden;
  padding:32px 16px;
  background:repeating-conic-gradient(#141824 0% 25%,#11151f 0% 50%) 0 0/20px 20px;
  display:flex;align-items:center;justify-content:center;min-height:220px;
  margin-bottom:14px;
}
/* The actual overlay preview */
.ov{
  display:inline-flex;flex-direction:column;align-items:center;gap:6px;
  padding:16px 24px 20px;border-radius:14px;
  background:var(--ov-bg,transparent);
  transition:background .3s;
}
.ov-sprite{
  image-rendering:auto;
  filter:drop-shadow(0 0 12px var(--ov-glow,#f5c518));
  transition:filter .3s;
  animation:float 3s ease-in-out infinite;
}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
.ov-tag{
  font-family:'Press Start 2P',monospace;font-size:7px;
  color:var(--ov-glow,#f5c518);letter-spacing:.12em;opacity:.8;
  background:rgba(0,0,0,.3);padding:3px 8px;border-radius:4px;
}
.ov-name{
  font-family:'Press Start 2P',monospace;
  color:var(--ov-name,#f5c518);
  text-shadow:0 0 10px var(--ov-glow,#f5c518);
  text-transform:uppercase;letter-spacing:.08em;line-height:1.4;
  text-align:center;
}

.url-box{background:#181c2a;border:1px solid var(--border);border-radius:10px;padding:14px}
.url-label{font-size:11px;color:var(--muted);margin-bottom:6px;font-weight:500}
.url-row{display:flex;align-items:center;gap:8px}
.url-text{font-family:'Courier New',monospace;font-size:11px;color:#34d399;
  flex:1;word-break:break-all;line-height:1.4}
.copy-btn{padding:6px 12px;background:#1e2d40;border:1px solid var(--border);
  border-radius:7px;color:var(--text);font-size:11px;cursor:pointer;
  white-space:nowrap;transition:all .2s}
.copy-btn:hover{border-color:var(--accent)}

.status{font-size:12px;color:var(--muted);margin-top:12px;padding-top:12px;
  border-top:1px solid var(--border);display:flex;align-items:center;gap:6px}
.dot{width:7px;height:7px;border-radius:50%;background:#ef4444;flex-shrink:0;
  box-shadow:0 0 6px #ef4444}
.dot.ok{background:#34d399;box-shadow:0 0 6px #34d399}
</style>
</head>
<body>

<div class="topbar">
  <div class="brand">⬤ POKÉMON OVERLAY</div>
  <a href="/logout" class="logout">Log out</a>
</div>

<div class="grid">

  <!-- Left -->
  <div>

    <div class="card">
      <div class="card-title">POKÉMON</div>
      <div class="field">
        <label>SEARCH POKÉMON NAME</label>
        <div class="search-wrap">
          <input type="text" id="pokemon_input" value="{{ state.pokemon }}" placeholder="e.g. pikachu, mewtwo…" onkeydown="if(event.key==='Enter')searchPokemon()"/>
          <button class="search-btn" onclick="searchPokemon()">FIND</button>
        </div>
        <div class="search-result" id="search_result"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">COLORS</div>
      <div class="field">
        <label>NAME COLOR</label>
        <div class="color-field">
          <input type="color" id="name_color_pick" value="{{ state.name_color }}" oninput="syncColor('name_color','name_color_pick')"/>
          <input type="text"  id="name_color"      value="{{ state.name_color }}" oninput="syncColorText('name_color','name_color_pick')"/>
        </div>
      </div>
      <div class="field">
        <label>GLOW COLOR</label>
        <div class="color-field">
          <input type="color" id="glow_color_pick" value="{{ state.glow_color }}" oninput="syncColor('glow_color','glow_color_pick')"/>
          <input type="text"  id="glow_color"      value="{{ state.glow_color }}" oninput="syncColorText('glow_color','glow_color_pick')"/>
        </div>
      </div>
      <div class="field">
        <label>BACKGROUND (use #00000000 for transparent)</label>
        <div class="color-field">
          <input type="color" id="bg_color_pick" value="{{ state.bg_color }}" oninput="syncColor('bg_color','bg_color_pick')"/>
          <input type="text"  id="bg_color"      value="{{ state.bg_color }}" oninput="syncColorText('bg_color','bg_color_pick')"/>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">SIZE & GLOW</div>
      <div class="field">
        <label>SPRITE SIZE (px)</label>
        <div class="range-row">
          <input type="range" id="sprite_size" min="80" max="300" value="{{ state.sprite_size }}"
            oninput="document.getElementById('ss_val').textContent=this.value+'px';updatePreview()"/>
          <span class="range-val" id="ss_val">{{ state.sprite_size }}px</span>
        </div>
      </div>
      <div class="field">
        <label>NAME SIZE (px)</label>
        <div class="range-row">
          <input type="range" id="name_size" min="10" max="52" value="{{ state.name_size }}"
            oninput="document.getElementById('ns_val').textContent=this.value+'px';updatePreview()"/>
          <span class="range-val" id="ns_val">{{ state.name_size }}px</span>
        </div>
      </div>
      <div class="field">
        <label>GLOW STRENGTH (px)</label>
        <div class="range-row">
          <input type="range" id="glow_strength" min="0" max="40" value="{{ state.glow_strength }}"
            oninput="document.getElementById('gs_val').textContent=this.value+'px';updatePreview()"/>
          <span class="range-val" id="gs_val">{{ state.glow_strength }}px</span>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">VISIBILITY</div>
      <div class="toggle-row">
        <span>Show Pokémon name</span>
        <label class="toggle"><input type="checkbox" id="show_name" {% if state.show_name %}checked{% endif %} onchange="updatePreview()"/><span class="slider"></span></label>
      </div>
      <div class="toggle-row">
        <span>Show ✨ SHINY tag</span>
        <label class="toggle"><input type="checkbox" id="show_shiny_tag" {% if state.show_shiny_tag %}checked{% endif %} onchange="updatePreview()"/><span class="slider"></span></label>
      </div>
    </div>

    <button class="apply-btn" onclick="applySettings()">▶ APPLY SETTINGS</button>
    <div class="toast" id="toast">✓ Settings applied!</div>

  </div>

  <!-- Right: Preview -->
  <div class="preview-panel">
    <div class="card">
      <div class="card-title">PREVIEW</div>
      <div class="preview-wrap">
        <div class="ov" id="ov_box">
          <div class="ov-tag" id="ov_tag">✨ SHINY</div>
          <img class="ov-sprite" id="ov_sprite"
            src="{{ state.sprite_url }}"
            width="{{ state.sprite_size }}" height="{{ state.sprite_size }}"
            style="object-fit:contain"/>
          <div class="ov-name" id="ov_name"
            style="font-size:{{ state.name_size }}px">{{ state.display_name }}</div>
        </div>
      </div>

      <div class="url-box">
        <div class="url-label">OBS BROWSER SOURCE URL</div>
        <div class="url-row">
          <span class="url-text" id="overlay_url">{{ request.host_url }}</span>
          <button class="copy-btn" onclick="copyUrl()">Copy</button>
        </div>
      </div>

      <div class="status">
        <div class="dot ok" id="status_dot"></div>
        <span id="status_text">Connected</span>
      </div>
    </div>
  </div>

</div>

<script>
// ── Color sync ────────────────────────────────────────────────────────────
function syncColor(textId, pickId) {
  document.getElementById(textId).value = document.getElementById(pickId).value;
  updatePreview();
}
function syncColorText(textId, pickId) {
  const v = document.getElementById(textId).value;
  if (/^#[0-9a-fA-F]{6}$/.test(v)) {
    document.getElementById(pickId).value = v;
    updatePreview();
  }
}

// ── Live preview ──────────────────────────────────────────────────────────
function updatePreview() {
  const nameColor = document.getElementById('name_color').value;
  const glowColor = document.getElementById('glow_color').value;
  const bgColor   = document.getElementById('bg_color').value;
  const spriteSize = document.getElementById('sprite_size').value;
  const nameSize   = document.getElementById('name_size').value;
  const glowStr    = document.getElementById('glow_strength').value;
  const showName   = document.getElementById('show_name').checked;
  const showTag    = document.getElementById('show_shiny_tag').checked;

  const box = document.getElementById('ov_box');
  box.style.setProperty('--ov-name', nameColor);
  box.style.setProperty('--ov-glow', glowColor);
  box.style.background = bgColor;

  const sprite = document.getElementById('ov_sprite');
  sprite.width  = spriteSize;
  sprite.height = spriteSize;
  sprite.style.filter = `drop-shadow(0 0 ${glowStr}px ${glowColor})`;

  const nameEl = document.getElementById('ov_name');
  nameEl.style.display    = showName ? '' : 'none';
  nameEl.style.fontSize   = nameSize + 'px';
  nameEl.style.color      = nameColor;
  nameEl.style.textShadow = `0 0 ${glowStr}px ${glowColor}`;

  document.getElementById('ov_tag').style.display = showTag ? '' : 'none';
  document.getElementById('ov_tag').style.color   = glowColor;
}

// ── Search pokemon ────────────────────────────────────────────────────────
let pendingPokemon = null;

async function searchPokemon() {
  const name = document.getElementById('pokemon_input').value.trim();
  if (!name) return;
  const res = document.getElementById('search_result');
  res.className = 'search-result';
  res.textContent = 'Searching…';

  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ pokemon: name })
    });
    const data = await r.json();
    if (data.error) {
      res.className = 'search-result err';
      res.textContent = '✗ ' + data.error;
    } else {
      res.className = 'search-result ok';
      res.textContent = '✓ Found: ' + data.display_name;
      document.getElementById('ov_sprite').src = data.sprite_url;
      document.getElementById('ov_name').textContent = data.display_name;
      pendingPokemon = name;
    }
  } catch(e) {
    res.className = 'search-result err';
    res.textContent = '✗ ' + e;
  }
}

// ── Apply settings ────────────────────────────────────────────────────────
async function applySettings() {
  const payload = {
    pokemon:       pendingPokemon || document.getElementById('pokemon_input').value.trim(),
    name_color:    document.getElementById('name_color').value,
    glow_color:    document.getElementById('glow_color').value,
    bg_color:      document.getElementById('bg_color').value,
    show_name:     document.getElementById('show_name').checked,
    show_shiny_tag:document.getElementById('show_shiny_tag').checked,
    name_size:     parseInt(document.getElementById('name_size').value),
    sprite_size:   parseInt(document.getElementById('sprite_size').value),
    glow_strength: parseInt(document.getElementById('glow_strength').value),
  };
  try {
    const r = await fetch('/api/config', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    if (r.ok) {
      const t = document.getElementById('toast');
      t.style.display = 'block';
      setTimeout(() => t.style.display='none', 2500);
    }
  } catch(e) { alert('Error: '+e); }
}

// ── Copy URL ──────────────────────────────────────────────────────────────
function copyUrl() {
  const url = document.getElementById('overlay_url').textContent;
  navigator.clipboard.writeText(url).catch(() => {
    const el = document.createElement('textarea');
    el.value = url; document.body.appendChild(el);
    el.select(); document.execCommand('copy'); document.body.removeChild(el);
  });
  const btn = document.querySelector('.copy-btn');
  btn.textContent='Copied!'; btn.style.color='#34d399';
  setTimeout(()=>{btn.textContent='Copy';btn.style.color=''},1500);
}

// ── Status poller — retries until sprite is ready ────────────────────────
async function pollStatus() {
  try {
    const s = await fetch('/api/state', {cache: 'no-store'});
    if (!s.ok) throw new Error('bad');
    const data = await s.json();

    if (!data.sprite_url) {
      // PokéAPI still loading — show spinner and retry quickly
      document.getElementById('status_dot').className = 'dot';
      document.getElementById('status_text').textContent = '⏳ Loading sprite…';
      setTimeout(pollStatus, 2000);
      return;
    }

    // Update status bar
    document.getElementById('status_dot').className = 'dot ok';
    document.getElementById('status_text').textContent =
      data.error ? '⚠ '+data.error : '✓ '+data.display_name+' loaded';

    // Update preview sprite + name if they changed
    const sprite = document.getElementById('ov_sprite');
    if (data.sprite_url && sprite.src !== data.sprite_url) {
      sprite.style.opacity = '0';
      sprite.src = data.sprite_url;
      sprite.onload = () => { sprite.style.transition='opacity .4s'; sprite.style.opacity='1'; };
    }
    document.getElementById('ov_name').textContent = data.display_name || '';

    setTimeout(pollStatus, 5000);
  } catch(_) {
    document.getElementById('status_dot').className = 'dot';
    document.getElementById('status_text').textContent = 'Disconnected';
    setTimeout(pollStatus, 3000);
  }
}

updatePreview();
pollStatus();
</script>
</body>
</html>"""

# ── Overlay HTML (the transparent OBS page) ───────────────────────────────────
OVERLAY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Pokémon Shiny Overlay</title>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{background:transparent;overflow:hidden;width:100%;height:100%}

.ov{
  display:inline-flex;flex-direction:column;align-items:center;gap:6px;
  padding:16px 28px 22px;
  border-radius:14px;
  background:var(--bg,transparent);
  animation:appear .7s cubic-bezier(.34,1.56,.64,1) both;
}
@keyframes appear{from{opacity:0;transform:scale(.7) translateY(20px)}to{opacity:1;transform:none}}

.ov-tag{
  font-family:'Press Start 2P',monospace;font-size:7px;
  color:var(--glow,#f5c518);letter-spacing:.14em;
  background:rgba(0,0,0,.35);padding:3px 10px;border-radius:4px;
  animation:blink 2s ease-in-out infinite;
}
@keyframes blink{0%,100%{opacity:.7}50%{opacity:1}}

.ov-sprite{
  image-rendering:auto;object-fit:contain;
  filter:drop-shadow(0 0 var(--glow-px,12px) var(--glow,#f5c518));
  animation:float 3s ease-in-out infinite;
  transition:filter .5s,width .3s,height .3s;
}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}

.ov-name{
  font-family:'Press Start 2P',monospace;
  color:var(--name,#f5c518);
  text-shadow:0 0 var(--glow-px,12px) var(--glow,#f5c518);
  text-transform:uppercase;letter-spacing:.1em;line-height:1.5;
  text-align:center;
  transition:font-size .3s,color .3s;
}
</style>
</head>
<body>
<div class="ov" id="ov">
  <div class="ov-tag"    id="tag">✨ SHINY</div>
  <img class="ov-sprite" id="sprite" src="" width="160" height="160"/>
  <div class="ov-name"   id="name" style="font-size:28px"></div>
</div>

<script>
function applyState(s) {
  const ov = document.getElementById('ov');
  ov.style.setProperty('--bg',      s.bg_color  || 'transparent');
  ov.style.setProperty('--name',    s.name_color || '#f5c518');
  ov.style.setProperty('--glow',    s.glow_color || '#f5c518');
  ov.style.setProperty('--glow-px', (s.glow_strength ?? 12) + 'px');

  const sprite = document.getElementById('sprite');
  const oldSrc = sprite.src;
  if (s.sprite_url && s.sprite_url !== oldSrc) {
    sprite.style.opacity = '0';
    sprite.src = s.sprite_url;
    sprite.onload = () => {
      sprite.style.transition = 'opacity .4s';
      sprite.style.opacity = '1';
    };
  }
  sprite.width  = s.sprite_size || 160;
  sprite.height = s.sprite_size || 160;
  sprite.style.filter = `drop-shadow(0 0 ${s.glow_strength ?? 12}px ${s.glow_color || '#f5c518'})`;

  const nameEl = document.getElementById('name');
  nameEl.textContent    = (s.display_name || '').toUpperCase();
  nameEl.style.display  = s.show_name ? '' : 'none';
  nameEl.style.fontSize = (s.name_size || 28) + 'px';
  nameEl.style.color    = s.name_color || '#f5c518';
  nameEl.style.textShadow = `0 0 ${s.glow_strength ?? 12}px ${s.glow_color || '#f5c518'}`;

  const tag = document.getElementById('tag');
  tag.style.display = s.show_shiny_tag ? '' : 'none';
  tag.style.color   = s.glow_color || '#f5c518';
}

let _ready = false;

async function poll() {
  try {
    const r = await fetch('/api/state', {cache: 'no-store'});
    if (!r.ok) throw new Error('bad response');
    const s = await r.json();

    if (!s.sprite_url) {
      // Server is up but sprite not loaded yet — retry quickly
      setTimeout(poll, 2000);
      return;
    }

    applyState(s);
    _ready = true;
    setTimeout(poll, 4000);
  } catch(_) {
    // Network hiccup — retry in 3s
    setTimeout(poll, 3000);
  }
}
poll();
</script>
</body>
</html>"""

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[Pokémon Overlay]  http://localhost:{PORT}/")
    print(f"[Config Panel]     http://localhost:{PORT}/config  (password: {ADMIN_PASSWORD})")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
