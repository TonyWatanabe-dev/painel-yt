# Sincroniza arquivos com o gist secreto do painel.
# python gist_sync.py baixar <arquivo...>   (usa GIST_ID; GIST_TOKEN opcional)
# python gist_sync.py enviar <arquivo...>   (usa GIST_ID + GIST_TOKEN)
import json, os, sys, urllib.request

API = "https://api.github.com/gists/"

def _req(gid, token=None, data=None, method="GET"):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "painel-yt"}
    if token: h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(API + gid, data=data, headers=h, method=method)
    return json.loads(urllib.request.urlopen(r, timeout=60).read())

def baixar(gid, nomes, pasta=".", token=None):
    g = _req(gid, token)
    for n in nomes:
        f = g["files"].get(n)
        if not f: continue
        txt = f["content"] if not f.get("truncated") else urllib.request.urlopen(f["raw_url"], timeout=60).read().decode("utf-8")
        open(os.path.join(pasta, n), "w", encoding="utf-8").write(txt)
        print("baixado", n, len(txt))

def enviar(gid, token, arquivos):
    """arquivos: {nome_no_gist: caminho_local}"""
    files = {n: {"content": open(p, encoding="utf-8").read()} for n, p in arquivos.items()}
    _req(gid, token, json.dumps({"files": files}).encode("utf-8"), "PATCH")
    print("enviado", ", ".join(arquivos))

if __name__ == "__main__":
    acao, nomes = sys.argv[1], sys.argv[2:]
    gid, tok = os.environ["GIST_ID"], os.environ.get("GIST_TOKEN")
    pasta = os.environ.get("PAINEL_DIR", ".")
    if acao == "baixar": baixar(gid, nomes, pasta, tok)
    else: enviar(gid, tok, {n: os.path.join(pasta, n) for n in nomes})
