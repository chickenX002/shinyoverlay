"""
pokemon_server.py – Pokémon Shiny Sprite Stream Overlay
────────────────────────────────────────────────────────
• 1 Pokémon  → shows it statically
• 2+ Pokémon → rotates every N seconds (default 40 s) until stopped

Routes:
  /                → OBS Browser Source overlay
  /config          → Web config panel (password protected)
  /api/state       → GET current state (JSON)
  /api/config      → POST settings
  /api/fetch       → POST {"slot":0,"name":"pikachu"} – load a slot
  /api/rotation    → POST {"action":"start"|"stop"|"next"|"prev"|"set_interval","interval":40}

Requirements:  pip install flask requests
Password:      OVERLAY_PASSWORD=yourpass python pokemon_server.py  (default: admin123)
"""

from flask import Flask, jsonify, render_template_string, request, session, redirect, url_for
import requests as req
import threading
import time
import copy
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pkmn-secret-change-me")

ADMIN_PASSWORD = os.environ.get("OVERLAY_PASSWORD", "admin123")
PORT = int(os.environ.get("PORT", os.environ.get("OVERLAY_PORT", 5051)))
MAX_SLOTS = 10

# ── Shared state ──────────────────────────────────────────────────────────────
def empty_slot():
    return {"name": "", "display_name": "", "sprite_url": "", "loading": False, "error": ""}

state = {
    "slots":          [empty_slot() for _ in range(MAX_SLOTS)],
    "active_slot":    0,
    # rotation
    "rotating":       False,
    "rotation_interval": 40,   # seconds per pokemon
    "seconds_left":   40,      # countdown shown in config panel
    "name_color":     "#f5c518",
    "glow_color":     "#f5c518",
    "bg_color":       "transparent",
    "show_name":      True,
    "show_shiny_tag": True,
    "name_size":      28,
    "sprite_size":    160,
    "glow_strength":  12,
}
state_lock = threading.Lock()

# ── Rotation engine (server-side) ─────────────────────────────────────────────
_rotation_stop = threading.Event()

def _filled_slots():
    """Return indices of slots that have a loaded sprite, in order."""
    return [i for i, s in enumerate(state["slots"]) if s["sprite_url"]]

def _next_filled(current: int) -> int:
    filled = _filled_slots()
    if not filled:
        return current
    idx = filled.index(current) if current in filled else -1
    return filled[(idx + 1) % len(filled)]

def _prev_filled(current: int) -> int:
    filled = _filled_slots()
    if not filled:
        return current
    idx = filled.index(current) if current in filled else 0
    return filled[(idx - 1) % len(filled)]

def rotation_loop():
    """Counts down and advances to next Pokémon when time runs out."""
    while True:
        _rotation_stop.wait()           # block until rotation is started
        with state_lock:
            interval = state["rotation_interval"]
            state["seconds_left"] = interval

        for remaining in range(interval, 0, -1):
            if not _rotation_stop.is_set():
                break                   # stopped mid-countdown
            with state_lock:
                state["seconds_left"] = remaining
            time.sleep(1)

        if not _rotation_stop.is_set():
            continue                    # was stopped — go back to waiting

        # Advance to next slot
        with state_lock:
            filled = _filled_slots()
            if len(filled) > 1:
                state["active_slot"] = _next_filled(state["active_slot"])
            state["seconds_left"] = state["rotation_interval"]

_rotation_thread = threading.Thread(target=rotation_loop, daemon=True)
_rotation_thread.start()

# ── PokéAPI ───────────────────────────────────────────────────────────────────
POKEAPI = "https://pokeapi.co/api/v2/pokemon/{}"

def fetch_pokemon(name: str):
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
                return "", "", f"'{name}' not found."
            last_err = f"HTTP {e.response.status_code}"
        except Exception as e:
            last_err = str(e)
        if attempt < 3:
            time.sleep(2 ** attempt)
    return "", "", last_err

def fetch_slot_bg(slot_index: int, name: str):
    with state_lock:
        state["slots"][slot_index]["loading"] = True
        state["slots"][slot_index]["error"]   = ""
    sprite, display, err = fetch_pokemon(name)
    with state_lock:
        s = state["slots"][slot_index]
        s["loading"] = False
        if sprite:
            s["sprite_url"]   = sprite
            s["display_name"] = display
            s["error"]        = ""
            # If this is the first filled slot, make it active
            if not any(sl["sprite_url"] for idx, sl in enumerate(state["slots"]) if idx != slot_index):
                state["active_slot"] = slot_index
        else:
            s["error"] = err

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def overlay():
    return render_template_string(OVERLAY_HTML)

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify(copy.deepcopy(state))

@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    if not session.get("authed"):
        if request.headers.get("X-Overlay-Password") != ADMIN_PASSWORD:
            return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(force=True)
    slot = int(data.get("slot", 0))
    name = data.get("name", "").strip()
    if not (0 <= slot < MAX_SLOTS):
        return jsonify({"error": "Invalid slot"}), 400
    if not name:
        with state_lock:
            state["slots"][slot] = empty_slot()
        return jsonify({"ok": True, "cleared": True})
    with state_lock:
        state["slots"][slot]["name"] = name.lower()
    threading.Thread(target=fetch_slot_bg, args=(slot, name), daemon=True).start()
    return jsonify({"ok": True, "loading": True})

@app.route("/api/config", methods=["POST"])
def api_config():
    if not session.get("authed"):
        if request.headers.get("X-Overlay-Password") != ADMIN_PASSWORD:
            return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(force=True)
    with state_lock:
        for key in ("active_slot","name_color","glow_color","bg_color","show_name",
                    "show_shiny_tag","name_size","sprite_size","glow_strength"):
            if key in data:
                state[key] = data[key]
    return jsonify({"ok": True})

@app.route("/api/rotation", methods=["POST"])
def api_rotation():
    if not session.get("authed"):
        if request.headers.get("X-Overlay-Password") != ADMIN_PASSWORD:
            return jsonify({"error": "Unauthorized"}), 401
    data   = request.get_json(force=True)
    action = data.get("action", "")

    if action == "start":
        with state_lock:
            filled = _filled_slots()
            if len(filled) < 2:
                return jsonify({"error": "Need at least 2 Pokémon to rotate."}), 400
            state["rotating"] = True
            state["seconds_left"] = state["rotation_interval"]
        _rotation_stop.set()

    elif action == "stop":
        _rotation_stop.clear()
        with state_lock:
            state["rotating"] = False
            state["seconds_left"] = state["rotation_interval"]

    elif action == "next":
        with state_lock:
            state["active_slot"] = _next_filled(state["active_slot"])
            state["seconds_left"] = state["rotation_interval"]

    elif action == "prev":
        with state_lock:
            state["active_slot"] = _prev_filled(state["active_slot"])
            state["seconds_left"] = state["rotation_interval"]

    elif action == "set_interval":
        interval = max(5, int(data.get("interval", 40)))
        with state_lock:
            state["rotation_interval"] = interval
            state["seconds_left"]      = interval

    with state_lock:
        return jsonify({"ok": True, "rotating": state["rotating"],
                        "active_slot": state["active_slot"]})

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
        s = copy.deepcopy(state)
    return render_template_string(CONFIG_HTML, state=s, max_slots=MAX_SLOTS)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("config_panel"))

# ── Preload Charizard into slot 0 ─────────────────────────────────────────────
def preload():
    with state_lock:
        state["slots"][0]["name"] = "charizard"
    fetch_slot_bg(0, "charizard")

threading.Thread(target=preload, daemon=True).start()

# ─────────────────────────────────────────────────────────────────────────────
# LOGIN HTML
# ─────────────────────────────────────────────────────────────────────────────
LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Pokémon Overlay – Login</title>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=DM+Sans:wght@400;500&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d0f1a;min-height:100vh;display:flex;align-items:center;justify-content:center;
  font-family:'DM Sans',sans-serif;
  background-image:radial-gradient(ellipse at 30% 40%,#f5c51815 0%,transparent 55%)}
.card{background:#12151f;border:1px solid #222840;border-radius:20px;
  padding:48px 40px;width:100%;max-width:380px;
  box-shadow:0 20px 60px rgba(0,0,0,.6);text-align:center}
.pokeball{font-size:40px;margin-bottom:16px;display:block;animation:spin 4s linear infinite}
@keyframes spin{0%,100%{transform:rotate(0deg)}50%{transform:rotate(180deg)}}
h1{font-family:'Press Start 2P',monospace;font-size:11px;color:#f5c518;margin-bottom:6px;line-height:1.6}
.sub{font-size:12px;color:#4b6070;margin-bottom:32px}
label{display:block;font-size:11px;font-weight:500;color:#6b8090;margin-bottom:6px;text-align:left}
input[type=password]{width:100%;padding:12px 16px;background:#1a1e2e;border:1px solid #222840;
  border-radius:10px;color:#fff;font-size:14px;outline:none;transition:border-color .2s;margin-bottom:4px}
input[type=password]:focus{border-color:#f5c518}
.error{background:#ff2d5515;border:1px solid #ff2d5540;border-radius:8px;
  padding:10px 14px;font-size:12px;color:#ff6b80;margin:10px 0;text-align:left}
button{width:100%;margin-top:16px;padding:13px;background:#f5c518;border:none;
  border-radius:10px;color:#0d0f1a;font-family:'Press Start 2P',monospace;
  font-size:9px;cursor:pointer;transition:background .2s,transform .1s}
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

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG PANEL HTML
# ─────────────────────────────────────────────────────────────────────────────
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
  background-image:radial-gradient(ellipse at 80% 10%,#f5c51810 0%,transparent 45%)}

.topbar{display:flex;align-items:center;justify-content:space-between;
  margin-bottom:28px;max-width:1080px;margin-left:auto;margin-right:auto}
.brand{font-family:'Press Start 2P',monospace;font-size:9px;color:var(--accent)}
.logout{font-size:12px;color:var(--muted);text-decoration:none;
  border:1px solid var(--border);padding:6px 14px;border-radius:8px;transition:all .2s}
.logout:hover{color:var(--text);border-color:#f5c51860}

.layout{display:grid;grid-template-columns:1fr 340px;gap:20px;max-width:1080px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:22px;margin-bottom:16px}
.card:last-child{margin-bottom:0}
.card-title{font-family:'Press Start 2P',monospace;font-size:8px;color:var(--accent);
  letter-spacing:.1em;margin-bottom:16px}

/* ── Rotation controls ── */
.rotation-card{background:var(--card);border:2px solid var(--border);border-radius:16px;padding:22px;margin-bottom:16px;transition:border-color .3s}
.rotation-card.running{border-color:#34d399;box-shadow:0 0 0 1px #34d39940,0 0 20px #34d39915}

.rotation-status{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.rot-indicator{width:10px;height:10px;border-radius:50%;background:#4b6070;flex-shrink:0;transition:background .3s}
.rot-indicator.running{background:#34d399;box-shadow:0 0 8px #34d399;animation:glow 1.5s ease-in-out infinite}
@keyframes glow{0%,100%{box-shadow:0 0 4px #34d399}50%{box-shadow:0 0 14px #34d399}}
.rot-label{font-family:'Press Start 2P',monospace;font-size:8px;color:var(--muted)}
.rot-label.running{color:#34d399}

.countdown-wrap{
  background:#181c2a;border:1px solid var(--border);border-radius:10px;
  padding:12px 16px;margin-bottom:14px;
  display:flex;align-items:center;justify-content:space-between}
.countdown-label{font-size:11px;color:var(--muted)}
.countdown-val{font-family:'Press Start 2P',monospace;font-size:16px;color:var(--accent)}

.ctrl-row{display:flex;gap:8px;margin-bottom:12px}
.ctrl-btn{flex:1;padding:10px 6px;border:none;border-radius:9px;
  font-family:'Press Start 2P',monospace;font-size:8px;cursor:pointer;
  transition:background .2s,transform .1s}
.ctrl-btn:active{transform:scale(.95)}
.btn-start{background:#34d399;color:#0d0f1a}
.btn-start:hover{background:#2bb885}
.btn-stop{background:#ef4444;color:#fff}
.btn-stop:hover{background:#dc2626}
.btn-prev,.btn-next{background:#1e2d40;color:var(--text);border:1px solid var(--border)}
.btn-prev:hover,.btn-next:hover{background:#273548;border-color:var(--accent)}

.interval-row{display:flex;align-items:center;gap:10px}
.interval-row label{font-size:11px;color:var(--muted);white-space:nowrap}
.interval-row input{flex:1;padding:8px 12px;background:#181c2a;border:1px solid var(--border);
  border-radius:8px;color:var(--text);font-size:13px;outline:none;transition:border-color .2s}
.interval-row input:focus{border-color:var(--accent)}
.interval-row button{padding:8px 14px;background:var(--accent);border:none;border-radius:8px;
  color:#0d0f1a;font-family:'Press Start 2P',monospace;font-size:7px;cursor:pointer;
  white-space:nowrap;transition:background .2s}
.interval-row button:hover{background:#e6b800}
.rot-note{font-size:11px;color:var(--muted);margin-top:10px;line-height:1.5}

/* ── Slots ── */
.slots-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:4px}
.slot{background:#181c2a;border:2px solid var(--border);border-radius:12px;
  padding:10px 8px;cursor:pointer;transition:all .2s;
  display:flex;flex-direction:column;align-items:center;gap:6px;
  position:relative;min-height:110px}
.slot:hover{border-color:#f5c51860;background:#1e2338}
.slot.selected{border-color:#60a5fa;background:#1a2035}
.slot.on-air{border-color:var(--accent);background:#1e2338;
  box-shadow:0 0 0 1px var(--accent),0 0 14px #f5c51830}
.slot.has-pokemon img{width:60px;height:60px;object-fit:contain;
  filter:drop-shadow(0 0 6px var(--accent))}
.slot-num{font-family:'Press Start 2P',monospace;font-size:7px;
  color:var(--muted);position:absolute;top:6px;left:8px}
.slot-name{font-size:10px;font-weight:600;color:var(--text);text-align:center;
  text-transform:capitalize;max-width:70px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.slot-empty{font-size:22px;opacity:.2;margin-top:8px}
.slot-loading{font-size:9px;color:var(--muted);animation:pulse .8s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:1}}
.slot-err{font-size:9px;color:#ff6b80;text-align:center;max-width:70px;line-height:1.3}
.air-badge{position:absolute;top:5px;right:5px;
  font-family:'Press Start 2P',monospace;font-size:5px;
  background:var(--accent);color:#0d0f1a;padding:2px 4px;border-radius:3px}

.edit-area{background:#181c2a;border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:12px}
.edit-title{font-family:'Press Start 2P',monospace;font-size:8px;color:var(--muted);margin-bottom:12px}
.search-wrap{display:flex;gap:8px}
input[type=text],input[type=number]{
  width:100%;padding:10px 14px;background:#0f1220;border:1px solid var(--border);
  border-radius:9px;color:var(--text);font-size:13px;outline:none;
  transition:border-color .2s;font-family:'DM Sans',sans-serif}
input:focus{border-color:var(--accent)}
.search-btn{padding:10px 16px;background:var(--accent);border:none;border-radius:9px;
  color:#0d0f1a;font-family:'Press Start 2P',monospace;font-size:7px;
  cursor:pointer;white-space:nowrap;transition:background .2s;flex-shrink:0}
.search-btn:hover{background:#e6b800}
.clear-btn{padding:10px 12px;background:transparent;border:1px solid #ff6b8060;
  border-radius:9px;color:#ff6b80;font-size:12px;cursor:pointer;
  white-space:nowrap;transition:all .2s;flex-shrink:0}
.clear-btn:hover{background:#ff6b8015}
.search-result{font-size:12px;margin-top:8px;min-height:16px}
.search-result.ok{color:#34d399}
.search-result.err{color:#ff6b80}
.set-active-btn{margin-top:10px;padding:9px 16px;background:transparent;
  border:1px solid var(--accent);border-radius:9px;color:var(--accent);
  font-family:'Press Start 2P',monospace;font-size:7px;
  cursor:pointer;transition:all .2s;width:100%}
.set-active-btn:hover{background:#f5c51815}
.set-active-btn.is-active{background:var(--accent);color:#0d0f1a}

/* ── Settings ── */
.field{margin-bottom:14px}
.field label{display:block;font-size:11px;font-weight:500;color:var(--muted);margin-bottom:6px;letter-spacing:.04em}
.color-field{display:flex;align-items:center;gap:10px}
.color-field input[type=color]{width:40px;height:36px;padding:2px;background:#181c2a;
  border:1px solid var(--border);border-radius:8px;cursor:pointer;flex-shrink:0}
.color-field input[type=text]{flex:1}
.range-row{display:flex;align-items:center;gap:10px}
input[type=range]{width:100%;accent-color:var(--accent);cursor:pointer}
.range-val{font-family:'Press Start 2P',monospace;font-size:9px;color:var(--accent);min-width:36px;text-align:right}
.toggle-row{display:flex;align-items:center;justify-content:space-between;
  padding:10px 0;border-bottom:1px solid var(--border)}
.toggle-row:last-child{border-bottom:none}
.toggle{position:relative;width:40px;height:22px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;inset:0;background:#1e2d40;border-radius:99px;cursor:pointer;transition:.3s}
.slider:before{content:'';position:absolute;width:16px;height:16px;left:3px;top:3px;
  background:#fff;border-radius:50%;transition:.3s}
input:checked+.slider{background:var(--accent)}
input:checked+.slider:before{transform:translateX(18px)}

.apply-btn{width:100%;padding:13px;background:var(--accent);border:none;border-radius:11px;
  color:#0d0f1a;font-family:'Press Start 2P',monospace;font-size:8px;
  cursor:pointer;transition:background .2s,transform .1s;margin-top:4px}
.apply-btn:hover{background:#e6b800}
.apply-btn:active{transform:scale(.98)}
.toast{display:none;text-align:center;font-size:12px;color:#34d399;
  padding:10px;border-radius:8px;background:#34d39915;border:1px solid #34d39930;margin-top:10px}

/* ── Right panel ── */
.sticky-right{position:sticky;top:24px}
.preview-wrap{border-radius:12px;overflow:hidden;padding:32px 16px;
  background:repeating-conic-gradient(#141824 0% 25%,#11151f 0% 50%) 0 0/20px 20px;
  display:flex;align-items:center;justify-content:center;min-height:220px;margin-bottom:14px}
.ov{display:inline-flex;flex-direction:column;align-items:center;gap:6px;
  padding:16px 24px 20px;border-radius:14px;background:var(--ov-bg,transparent)}
.ov-tag{font-family:'Press Start 2P',monospace;font-size:7px;color:var(--ov-glow,#f5c518);
  letter-spacing:.12em;background:rgba(0,0,0,.3);padding:3px 8px;border-radius:4px}
.ov-sprite{image-rendering:auto;object-fit:contain;animation:float 3s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
.ov-name{font-family:'Press Start 2P',monospace;color:var(--ov-name,#f5c518);
  text-transform:uppercase;letter-spacing:.08em;line-height:1.4;text-align:center}

.url-box{background:#181c2a;border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px}
.url-label{font-size:11px;color:var(--muted);margin-bottom:6px;font-weight:500}
.url-row{display:flex;align-items:center;gap:8px}
.url-text{font-family:'Courier New',monospace;font-size:11px;color:#34d399;flex:1;word-break:break-all;line-height:1.4}
.copy-btn{padding:6px 12px;background:#1e2d40;border:1px solid var(--border);
  border-radius:7px;color:var(--text);font-size:11px;cursor:pointer;white-space:nowrap;transition:all .2s}
.copy-btn:hover{border-color:var(--accent)}
.status{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px}
.dot{width:7px;height:7px;border-radius:50%;background:#ef4444;flex-shrink:0;box-shadow:0 0 6px #ef4444}
.dot.ok{background:#34d399;box-shadow:0 0 6px #34d399}
</style>
</head>
<body>

<div class="topbar">
  <div class="brand">⬤ POKÉMON OVERLAY</div>
  <a href="/logout" class="logout">Log out</a>
</div>

<div class="layout">
<div>

  <!-- ── Rotation controls ── -->
  <div class="rotation-card" id="rot-card">
    <div class="card-title" style="margin-bottom:12px">ROTATION</div>

    <div class="rotation-status">
      <div class="rot-indicator" id="rot-indicator"></div>
      <span class="rot-label" id="rot-label">STOPPED</span>
    </div>

    <div class="countdown-wrap" id="countdown-wrap" style="display:none">
      <span class="countdown-label">Next Pokémon in</span>
      <span class="countdown-val" id="countdown">–</span>
    </div>

    <div class="ctrl-row">
      <button class="ctrl-btn btn-prev" onclick="rotAction('prev')">◀ PREV</button>
      <button class="ctrl-btn btn-start" id="btn-start" onclick="rotAction('start')">▶ START</button>
      <button class="ctrl-btn btn-stop"  id="btn-stop"  onclick="rotAction('stop')" style="display:none">■ STOP</button>
      <button class="ctrl-btn btn-next" onclick="rotAction('next')">NEXT ▶</button>
    </div>

    <div class="interval-row">
      <label>SECONDS PER POKÉMON</label>
      <input type="number" id="interval-input" value="{{ state.rotation_interval }}" min="5" max="3600"/>
      <button onclick="setInterval_()">SET</button>
    </div>
    <div class="rot-note" id="rot-note">
      Add at least 2 Pokémon to enable rotation.
    </div>
  </div>

  <!-- ── Slots ── -->
  <div class="card">
    <div class="card-title">POKÉMON SLOTS (click to edit)</div>
    <div class="slots-grid" id="slots-grid">
      {% for i in range(max_slots) %}
      {% set slot = state.slots[i] %}
      <div class="slot {% if slot.sprite_url %}has-pokemon{% endif %} {% if i == state.active_slot %}on-air{% endif %}"
           id="slot-{{ i }}" onclick="selectSlot({{ i }})">
        <span class="slot-num">{{ i+1 }}</span>
        {% if i == state.active_slot %}<span class="air-badge">ON AIR</span>{% endif %}
        {% if slot.loading %}
          <div style="margin-top:20px" class="slot-loading">loading…</div>
        {% elif slot.sprite_url %}
          <img src="{{ slot.sprite_url }}" alt="{{ slot.display_name }}"/>
          <span class="slot-name">{{ slot.display_name }}</span>
        {% elif slot.error %}
          <div class="slot-empty">❌</div>
          <span class="slot-err">{{ slot.error[:28] }}</span>
        {% else %}
          <div class="slot-empty">＋</div>
          <span class="slot-name" style="color:var(--muted)">empty</span>
        {% endif %}
      </div>
      {% endfor %}
    </div>

    <div class="edit-area">
      <div class="edit-title" id="edit-title">SELECT A SLOT TO EDIT</div>
      <div class="search-wrap">
        <input type="text" id="pkmn-input" placeholder="e.g. pikachu, mewtwo…"
          onkeydown="if(event.key==='Enter')fetchSlot()"/>
        <button class="search-btn" onclick="fetchSlot()">FIND</button>
        <button class="clear-btn"  onclick="clearSlot()" title="Clear slot">✕</button>
      </div>
      <div class="search-result" id="search-result"></div>
      <button class="set-active-btn" id="set-active-btn" onclick="setActive()">
        SET AS ACTIVE (SHOW ON OVERLAY)
      </button>
    </div>
  </div>

  <!-- ── Colors ── -->
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
      <label>BACKGROUND</label>
      <div class="color-field">
        <input type="color" id="bg_color_pick" value="{{ state.bg_color if state.bg_color != 'transparent' else '#0a0a0a' }}" oninput="syncColor('bg_color','bg_color_pick')"/>
        <input type="text"  id="bg_color"      value="{{ state.bg_color }}" oninput="syncColorText('bg_color','bg_color_pick')"/>
      </div>
    </div>
  </div>

  <!-- ── Size & Glow ── -->
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

  <!-- ── Visibility ── -->
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

<!-- ── RIGHT ── -->
<div class="sticky-right">
  <div class="card">
    <div class="card-title">PREVIEW</div>
    <div class="preview-wrap">
      <div class="ov" id="ov_box">
        <div class="ov-tag"    id="ov_tag">✨ SHINY</div>
        <img class="ov-sprite" id="ov_sprite"
          src="{{ state.slots[state.active_slot].sprite_url }}"
          width="{{ state.sprite_size }}" height="{{ state.sprite_size }}" style="object-fit:contain"/>
        <div class="ov-name"   id="ov_name"
          style="font-size:{{ state.name_size }}px">{{ state.slots[state.active_slot].display_name }}</div>
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
</div><!-- /layout -->

<script>
let selectedSlot = {{ state.active_slot }};
let activeSlot   = {{ state.active_slot }};
let slotsData    = {{ state.slots | tojson }};
let isRotating   = {{ 'true' if state.rotating else 'false' }};

// ── Rotation actions ──────────────────────────────────────────────────────
async function rotAction(action) {
  try {
    const r = await fetch('/api/rotation', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({action})
    });
    const data = await r.json();
    if (data.error) { alert(data.error); return; }
    isRotating = data.rotating;
    updateRotUI(isRotating);
  } catch(e) { alert('Error: '+e); }
}

async function setInterval_() {
  const val = parseInt(document.getElementById('interval-input').value);
  if (isNaN(val) || val < 5) { alert('Minimum 5 seconds.'); return; }
  await fetch('/api/rotation', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action:'set_interval', interval: val})
  });
}

function updateRotUI(rotating) {
  const card = document.getElementById('rot-card');
  const ind  = document.getElementById('rot-indicator');
  const lbl  = document.getElementById('rot-label');
  const cdWrap = document.getElementById('countdown-wrap');
  const btnStart = document.getElementById('btn-start');
  const btnStop  = document.getElementById('btn-stop');
  const note = document.getElementById('rot-note');

  if (rotating) {
    card.classList.add('running');
    ind.classList.add('running');
    lbl.textContent = 'ROTATING';
    lbl.classList.add('running');
    cdWrap.style.display = '';
    btnStart.style.display = 'none';
    btnStop.style.display  = '';
    note.style.display = 'none';
  } else {
    card.classList.remove('running');
    ind.classList.remove('running');
    lbl.textContent = 'STOPPED';
    lbl.classList.remove('running');
    cdWrap.style.display = 'none';
    btnStart.style.display = '';
    btnStop.style.display  = 'none';
    const filled = slotsData.filter(s=>s.sprite_url).length;
    note.style.display = filled >= 2 ? 'none' : '';
    note.textContent = filled >= 2 ? '' : 'Add at least 2 Pokémon to enable rotation.';
  }
}

// ── Slot management ───────────────────────────────────────────────────────
function selectSlot(i) {
  selectedSlot = i;
  document.querySelectorAll('.slot').forEach((el,idx) => {
    el.classList.toggle('selected', idx === i);
  });
  document.getElementById('edit-title').textContent = `EDITING SLOT ${i+1}`;
  document.getElementById('pkmn-input').value = slotsData[i].name || '';
  document.getElementById('search-result').textContent = '';
  document.getElementById('search-result').className = 'search-result';
  const btn = document.getElementById('set-active-btn');
  btn.textContent = i === activeSlot ? 'CURRENTLY ON AIR ✓' : 'SET AS ACTIVE (SHOW ON OVERLAY)';
  btn.className = 'set-active-btn' + (i === activeSlot ? ' is-active' : '');
}

async function fetchSlot() {
  const name = document.getElementById('pkmn-input').value.trim();
  if (!name) return;
  const res = document.getElementById('search-result');
  res.className = 'search-result'; res.textContent = 'Searching…';
  try {
    const r = await fetch('/api/fetch', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({slot: selectedSlot, name})
    });
    const data = await r.json();
    if (data.error) { res.className='search-result err'; res.textContent='✗ '+data.error; return; }
    res.className='search-result ok'; res.textContent='⏳ Loading sprite…';
    waitForSlot(selectedSlot);
  } catch(e) { res.className='search-result err'; res.textContent='✗ '+e; }
}

async function waitForSlot(slotIdx) {
  const res = document.getElementById('search-result');
  for (let i=0; i<20; i++) {
    await new Promise(r=>setTimeout(r,1500));
    try {
      const s = await fetch('/api/state',{cache:'no-store'}).then(r=>r.json());
      const slot = s.slots[slotIdx];
      slotsData = s.slots;
      if (!slot.loading) {
        if (slot.error) { res.className='search-result err'; res.textContent='✗ '+slot.error; }
        else { res.className='search-result ok'; res.textContent='✓ Found: '+slot.display_name; }
        refreshSlotUI(slotIdx, slot);
        // update rotation note
        const filled = slotsData.filter(s=>s.sprite_url).length;
        const note = document.getElementById('rot-note');
        note.style.display = (!isRotating && filled < 2) ? '' : 'none';
        return;
      }
    } catch(_){}
  }
  res.className='search-result err'; res.textContent='✗ Timed out.';
}

function refreshSlotUI(i, slot) {
  const el = document.getElementById('slot-' + i);
  if (!el) return;
  const isAir = (i === activeSlot);
  el.className = 'slot' + (slot.sprite_url?' has-pokemon':'') + (isAir?' on-air':'') + (i===selectedSlot?' selected':'');
  el.innerHTML = `
    <span class="slot-num">${i+1}</span>
    ${isAir ? '<span class="air-badge">ON AIR</span>' : ''}
    ${slot.sprite_url
      ? `<img src="${slot.sprite_url}" alt="${slot.display_name}"/><span class="slot-name">${slot.display_name}</span>`
      : slot.error
        ? `<div class="slot-empty">❌</div><span class="slot-err">${slot.error.slice(0,28)}</span>`
        : `<div class="slot-empty">＋</div><span class="slot-name" style="color:var(--muted)">empty</span>`}
  `;
  el.onclick = () => selectSlot(i);
}

async function setActive() {
  await fetch('/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({active_slot: selectedSlot})
  });
  activeSlot = selectedSlot;
  slotsData.forEach((_,i) => refreshSlotUI(i, slotsData[i]));
  selectSlot(selectedSlot);
  updatePreviewSprite(slotsData[activeSlot]);
}

async function clearSlot() {
  await fetch('/api/fetch', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({slot: selectedSlot, name: ''})
  });
  slotsData[selectedSlot] = {name:'',display_name:'',sprite_url:'',loading:false,error:''};
  refreshSlotUI(selectedSlot, slotsData[selectedSlot]);
  document.getElementById('pkmn-input').value='';
  document.getElementById('search-result').textContent='';
}

// ── Preview ───────────────────────────────────────────────────────────────
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
  box.style.background = bgColor;
  const sprite = document.getElementById('ov_sprite');
  sprite.width  = spriteSize; sprite.height = spriteSize;
  sprite.style.filter = `drop-shadow(0 0 ${glowStr}px ${glowColor})`;
  const nameEl = document.getElementById('ov_name');
  nameEl.style.display    = showName ? '' : 'none';
  nameEl.style.fontSize   = nameSize + 'px';
  nameEl.style.color      = nameColor;
  nameEl.style.textShadow = `0 0 ${glowStr}px ${glowColor}`;
  const tag = document.getElementById('ov_tag');
  tag.style.display = showTag ? '' : 'none';
  tag.style.color   = glowColor;
}

function updatePreviewSprite(slot) {
  if (!slot) return;
  const sprite = document.getElementById('ov_sprite');
  if (slot.sprite_url && sprite.src !== slot.sprite_url) {
    sprite.style.opacity = '0';
    sprite.src = slot.sprite_url;
    sprite.onload = () => { sprite.style.transition='opacity .4s'; sprite.style.opacity='1'; };
  }
  document.getElementById('ov_name').textContent = (slot.display_name||'').toUpperCase();
}

function syncColor(textId,pickId){document.getElementById(textId).value=document.getElementById(pickId).value;updatePreview()}
function syncColorText(textId,pickId){const v=document.getElementById(textId).value;if(/^#[0-9a-fA-F]{6}$/.test(v)){document.getElementById(pickId).value=v;updatePreview()}}

async function applySettings() {
  const payload = {
    name_color:    document.getElementById('name_color').value,
    glow_color:    document.getElementById('glow_color').value,
    bg_color:      document.getElementById('bg_color').value,
    show_name:     document.getElementById('show_name').checked,
    show_shiny_tag:document.getElementById('show_shiny_tag').checked,
    name_size:     parseInt(document.getElementById('name_size').value),
    sprite_size:   parseInt(document.getElementById('sprite_size').value),
    glow_strength: parseInt(document.getElementById('glow_strength').value),
  };
  const r = await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if (r.ok){const t=document.getElementById('toast');t.style.display='block';setTimeout(()=>t.style.display='none',2500)}
}

function copyUrl(){
  const url=document.getElementById('overlay_url').textContent;
  navigator.clipboard.writeText(url).catch(()=>{const el=document.createElement('textarea');el.value=url;document.body.appendChild(el);el.select();document.execCommand('copy');document.body.removeChild(el)});
  const btn=document.querySelector('.copy-btn');btn.textContent='Copied!';btn.style.color='#34d399';
  setTimeout(()=>{btn.textContent='Copy';btn.style.color=''},1500);
}

// ── Status + countdown poller ─────────────────────────────────────────────
async function pollStatus() {
  try {
    const r = await fetch('/api/state',{cache:'no-store'});
    if (!r.ok) throw new Error('bad');
    const s = await r.json();
    slotsData  = s.slots;
    isRotating = s.rotating;

    // Countdown
    document.getElementById('countdown').textContent = s.seconds_left + 's';

    // If active slot changed (rotation advanced), update preview + slots
    if (s.active_slot !== activeSlot) {
      activeSlot = s.active_slot;
      slotsData.forEach((_,i) => refreshSlotUI(i, slotsData[i]));
      updatePreviewSprite(slotsData[activeSlot]);
    }

    const active = s.slots[s.active_slot];
    if (!active || !active.sprite_url) {
      document.getElementById('status_dot').className = 'dot';
      document.getElementById('status_text').textContent = '⏳ Loading sprite…';
      setTimeout(pollStatus, 2000); return;
    }

    document.getElementById('status_dot').className = 'dot ok';
    document.getElementById('status_text').textContent =
      s.rotating
        ? `▶ Rotating — ${active.display_name} (${s.seconds_left}s left)`
        : `✓ ${active.display_name} on air`;

    updateRotUI(s.rotating);
    setTimeout(pollStatus, 1000);   // poll every 1s to keep countdown fresh
  } catch(_) {
    document.getElementById('status_dot').className = 'dot';
    document.getElementById('status_text').textContent = 'Disconnected';
    setTimeout(pollStatus, 3000);
  }
}

// Init
document.querySelectorAll('input,select').forEach(el=>{el.addEventListener('change',updatePreview);el.addEventListener('input',updatePreview)});
updatePreview();
updateRotUI(isRotating);
pollStatus();
selectSlot({{ state.active_slot }});
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────────────────
# OVERLAY HTML
# ─────────────────────────────────────────────────────────────────────────────
OVERLAY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Pokémon Shiny Overlay</title>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{background:transparent;overflow:hidden;width:100%;height:100%}
.ov{display:inline-flex;flex-direction:column;align-items:center;gap:6px;
  padding:16px 28px 22px;border-radius:14px;background:var(--bg,transparent)}
.ov-tag{font-family:'Press Start 2P',monospace;font-size:7px;color:var(--glow,#f5c518);
  letter-spacing:.14em;background:rgba(0,0,0,.35);padding:3px 10px;border-radius:4px;
  animation:blink 2s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:.7}50%{opacity:1}}
.ov-sprite{image-rendering:auto;object-fit:contain;
  filter:drop-shadow(0 0 var(--glow-px,12px) var(--glow,#f5c518));
  animation:float 3s ease-in-out infinite;transition:opacity .5s,filter .5s}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}
.ov-name{font-family:'Press Start 2P',monospace;color:var(--name,#f5c518);
  text-shadow:0 0 var(--glow-px,12px) var(--glow,#f5c518);
  text-transform:uppercase;letter-spacing:.1em;line-height:1.5;text-align:center;
  transition:font-size .3s,color .3s}
</style>
</head>
<body>
<div class="ov" id="ov">
  <div class="ov-tag" id="tag">✨ SHINY</div>
  <img class="ov-sprite" id="sprite" src="" width="160" height="160"/>
  <div class="ov-name"  id="name" style="font-size:28px"></div>
</div>
<script>
let lastSpriteUrl = '';
let lastActiveSlot = -1;

function applyState(s) {
  const active = s.slots && s.slots[s.active_slot];
  if (!active || !active.sprite_url) return;

  const ov = document.getElementById('ov');
  ov.style.setProperty('--bg',      s.bg_color  || 'transparent');
  ov.style.setProperty('--name',    s.name_color || '#f5c518');
  ov.style.setProperty('--glow',    s.glow_color || '#f5c518');
  ov.style.setProperty('--glow-px', (s.glow_strength ?? 12) + 'px');

  const sprite = document.getElementById('sprite');
  if (active.sprite_url !== lastSpriteUrl) {
    lastSpriteUrl = active.sprite_url;
    sprite.style.opacity = '0';
    sprite.src = active.sprite_url;
    sprite.onload = () => { sprite.style.opacity = '1'; };
  }
  sprite.width  = s.sprite_size || 160;
  sprite.height = s.sprite_size || 160;
  sprite.style.filter = `drop-shadow(0 0 ${s.glow_strength ?? 12}px ${s.glow_color || '#f5c518'})`;

  const nameEl = document.getElementById('name');
  nameEl.textContent    = (active.display_name || '').toUpperCase();
  nameEl.style.display  = s.show_name ? '' : 'none';
  nameEl.style.fontSize = (s.name_size || 28) + 'px';
  nameEl.style.color    = s.name_color || '#f5c518';
  nameEl.style.textShadow = `0 0 ${s.glow_strength ?? 12}px ${s.glow_color || '#f5c518'}`;

  document.getElementById('tag').style.display = s.show_shiny_tag ? '' : 'none';
  document.getElementById('tag').style.color   = s.glow_color || '#f5c518';
}

async function poll() {
  try {
    const r = await fetch('/api/state', {cache: 'no-store'});
    if (!r.ok) throw new Error('bad');
    const s = await r.json();
    const active = s.slots && s.slots[s.active_slot];
    if (!active || !active.sprite_url) { setTimeout(poll, 2000); return; }
    applyState(s);
    setTimeout(poll, 2000);   // poll every 2s so transitions are smooth
  } catch(_) {
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
