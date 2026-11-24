# App do Painel YouTube: servidor local em http://127.0.0.1:8765
# - coleta rápida (inscritos + RSS) a cada 2 min e completa (todos os vídeos) a cada 15 min, em segundo plano
# - /api/data -> dados atuais | /api/refresh?modo=rapido|completo -> coleta na hora
# - /api/canais/add?c=<link|@handle|UC..> | /api/canais/remove?id=UC.. -> edita a lista (gist = fonte única)
import json, os, sys, threading, time, traceback, subprocess
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import coletar, gist_sync

PORT = 8765
RAPIDO_S, COMPLETO_S = 120, 15 * 60
STATE = {"payload": None, "coletando": False, "erro": None}
GIST_ID = "9f6dfcef3d75c567a8cd6bf7a32c6538"  # gist secreto: dados do celular + lista de canais
_TOKEN = {}
CANAIS_LOCK = threading.Lock()

def carregar_cache():
    try:
        txt = open(coletar.OUT_FILE, encoding="utf-8").read()
        STATE["payload"] = json.loads(txt[txt.index("=") + 1:].strip().rstrip(";"))
    except Exception:
        pass

def token():
    if not _TOKEN.get("t"):
        tf = os.path.join(BASE, ".gist_token")
        t = open(tf).read().strip() if os.path.exists(tf) else ""
        if not t:
            t = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                               creationflags=coletar.NOWIN).stdout.strip()
        _TOKEN["t"] = t
    return _TOKEN["t"]

def canais_gist():
    g = gist_sync._req(GIST_ID, token())
    f = g["files"].get("canais.txt")
    return f["content"] if f else None

def salvar_canais(txt):
    open(coletar.CANAIS_FILE, "w", encoding="utf-8").write(txt)
    gist_sync._req(GIST_ID, token(), json.dumps({"files": {"canais.txt": {"content": txt}}}).encode(), "PATCH")

def sync_canais():
    """Puxa a lista do gist (editada pelo celular) para o canais.txt local."""
    try:
        with CANAIS_LOCK:
            txt = canais_gist()
            local = open(coletar.CANAIS_FILE, encoding="utf-8").read()
            if txt is None: salvar_canais(local)
            elif txt != local: open(coletar.CANAIS_FILE, "w", encoding="utf-8").write(txt)
    except Exception:
        _TOKEN.pop("t", None)

def enviar_celular(antes):
    try:
        gist_sync.enviar(GIST_ID, token(), {"dados_pc.json": coletar.JSON_FILE})
        # só se o coletor resolveu algum @handle nesta rodada, devolve a lista resolvida
        with CANAIS_LOCK:
            local = open(coletar.CANAIS_FILE, encoding="utf-8").read()
            if local != antes and canais_gist() == antes: salvar_canais(local)
    except Exception:
        _TOKEN.pop("t", None)

def coletar_agora(modo, sync=True):
    if sync: sync_canais()
    STATE["coletando"] = True
    try:
        antes = open(coletar.CANAIS_FILE, encoding="utf-8").read()
        STATE["payload"] = coletar.run(modo); STATE["erro"] = None
        threading.Thread(target=enviar_celular, args=(antes,), daemon=True).start()
    except Exception:
        STATE["erro"] = traceback.format_exc(limit=2)
    finally:
        STATE["coletando"] = False
    return STATE["payload"]

def editar_canais(add=None, remove=None):
    with CANAIS_LOCK:
        txt = canais_gist()
        if txt is None: txt = open(coletar.CANAIS_FILE, encoding="utf-8").read()
        linhas = [l for l in txt.splitlines() if l.strip()]
        if add:
            ent = coletar.normalizar(add)
            if not ent: return "Não reconheci o canal. Cole o link do canal, o @handle ou o ID (UC...)."
            uc = coletar.resolver(ent)
            if not uc: return "Não achei esse canal no YouTube."
            if any(coletar.normalizar(l) == uc for l in linhas): return "Esse canal já está no painel."
            linhas.append(f"{uc}  # {ent}" if ent != uc else uc)
        if remove:
            linhas = [l for l in linhas if coletar.normalizar(l) != remove]
        salvar_canais("\n".join(linhas) + "\n")
    return None

def loop():
    ultimo_completo = 0
    while True:
        modo = "completo" if time.time() - ultimo_completo >= COMPLETO_S else "rapido"
        coletar_agora(modo)
        if modo == "completo": ultimo_completo = time.time()
        time.sleep(RAPIDO_S)

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=BASE, **k)
    def log_message(self, *a): pass
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/":
            self.send_response(302); self.send_header("Location", "/painel.html"); self.end_headers(); return
        if u.path == "/api/data":
            return self._json({"dados": STATE["payload"], "coletando": STATE["coletando"], "erro": STATE["erro"]})
        if u.path == "/api/refresh":
            modo = q.get("modo", ["completo"])[0]
            p = coletar_agora("rapido" if modo == "rapido" else "completo")
            return self._json({"dados": p, "coletando": False, "erro": STATE["erro"]})
        if u.path in ("/api/canais/add", "/api/canais/remove"):
            try:
                err = editar_canais(add=q.get("c", [None])[0]) if u.path.endswith("add") \
                    else editar_canais(remove=q.get("id", [None])[0])
            except Exception as ex:
                err = f"Falha: {ex}"
            if err: return self._json({"erro_msg": err}, 400)
            p = coletar_agora("completo", sync=False)
            return self._json({"dados": p, "coletando": False, "erro": STATE["erro"]})
        if u.path == "/api/ping":
            return self._json({"ok": True})
        return super().do_GET()

if __name__ == "__main__":
    carregar_cache()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        sys.exit(0)  # já está rodando
    threading.Thread(target=loop, daemon=True).start()
    srv.serve_forever()
