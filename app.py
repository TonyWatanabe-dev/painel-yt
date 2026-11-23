# App do Painel YouTube: servidor local em http://127.0.0.1:8765
# - coleta rápida (inscritos + RSS) a cada 2 min e completa (todos os vídeos) a cada 15 min, em segundo plano
# - /api/data -> dados atuais | /api/refresh?modo=rapido|completo -> coleta na hora e devolve os dados
import json, os, sys, threading, time, traceback
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import coletar

PORT = 8765
RAPIDO_S, COMPLETO_S = 120, 15 * 60
STATE = {"payload": None, "coletando": False, "erro": None}

def carregar_cache():
    try:
        txt = open(coletar.OUT_FILE, encoding="utf-8").read()
        STATE["payload"] = json.loads(txt[txt.index("=") + 1:].strip().rstrip(";"))
    except Exception:
        pass

GIST_ID = "9f6dfcef3d75c567a8cd6bf7a32c6538"  # gist secreto lido pelo app do celular
_TOKEN = {}

def enviar_celular():
    """Manda a coleta do PC pro gist (o celular usa a mais recente entre PC e nuvem)."""
    try:
        import subprocess, gist_sync
        tf = os.path.join(BASE, ".gist_token")
        if "t" not in _TOKEN and os.path.exists(tf):
            _TOKEN["t"] = open(tf).read().strip()
        if "t" not in _TOKEN:
            _TOKEN["t"] = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                                         creationflags=coletar.NOWIN).stdout.strip()
        if _TOKEN["t"]:
            gist_sync.enviar(GIST_ID, _TOKEN["t"], {"dados_pc.json": coletar.JSON_FILE})
    except Exception:
        _TOKEN.pop("t", None)

def coletar_agora(modo):
    STATE["coletando"] = True
    try:
        STATE["payload"] = coletar.run(modo); STATE["erro"] = None
        threading.Thread(target=enviar_celular, daemon=True).start()
    except Exception:
        STATE["erro"] = traceback.format_exc(limit=2)
    finally:
        STATE["coletando"] = False
    return STATE["payload"]

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
        u = urlparse(self.path)
        if u.path == "/":
            self.send_response(302); self.send_header("Location", "/painel.html"); self.end_headers(); return
        if u.path == "/api/data":
            return self._json({"dados": STATE["payload"], "coletando": STATE["coletando"], "erro": STATE["erro"]})
        if u.path == "/api/refresh":
            modo = parse_qs(u.query).get("modo", ["completo"])[0]
            p = coletar_agora("rapido" if modo == "rapido" else "completo")
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
