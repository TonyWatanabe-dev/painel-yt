# Coletor do Painel YouTube do Tony — sem API key, em paralelo.
# Modo "rapido" (~2-5 s): página do canal (inscritos) + RSS (últimos 15: data, views, likes) de todos os canais.
# Modo "completo" (~20-40 s): + yt-dlp --flat-playlist (/videos e /shorts) para views de TODOS os vídeos.
# Saída: dados.js (window.PAINEL = {...}) + historico.json. Uso: python coletar.py [rapido|completo]
import json, re, os, sys, subprocess, html, urllib.request, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("PAINEL_DIR", BASE)
CANAIS_FILE = os.path.join(BASE, "canais.txt")
HIST_FILE = os.path.join(DATA, "historico.json")
OUT_FILE = os.path.join(DATA, "dados.js")
JSON_FILE = os.path.join(DATA, "dados.json")
YTDLP = os.environ.get("YTDLP") or (r"C:\Users\Pichau\bin\yt-dlp.exe" if os.name == "nt" else "yt-dlp")
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
     "Accept-Language": "en-US", "Cookie": "CONSENT=YES+1; SOCS=CAI"}
LOCK = threading.Lock()
NOWIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=20).read().decode("utf-8", "replace")

def num(s):
    if not s: return None
    m = re.match(r"([\d.,]+)\s*([KMB])?", s.replace(",", ""))
    if not m: return None
    return int(round(float(m.group(1)) * {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2), 1)))

def pagina(cid):
    h = get(f"https://www.youtube.com/channel/{cid}?hl=en&gl=US")
    f = lambda p: (re.search(p, h) or [None, None])[1]
    subs_txt = f(r'"content":"([\d.,KMB]+ subscribers?)"')
    return {"nome": html.unescape(f(r'<meta property="og:title" content="([^"]*)') or cid),
            "handle": f(r'"canonicalBaseUrl":"(/@[^"]+)"'),
            "avatar": f(r'<meta property="og:image" content="([^"]*)'),
            "descricao": html.unescape(f(r'<meta property="og:description" content="([^"]*)') or ""),
            "inscritos": num(subs_txt), "inscritos_txt": subs_txt,
            "videos_count": num(f(r'"content":"([\d.,K]+) videos?"'))}

def rss(cid):
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
          "media": "http://search.yahoo.com/mrss/"}
    root = ET.fromstring(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"))
    out = {}
    for e in root.findall("a:entry", ns):
        vid = e.find("yt:videoId", ns).text
        g = e.find("media:group", ns)
        st = g.find("media:community/media:statistics", ns)
        rt = g.find("media:community/media:starRating", ns)
        out[vid] = {"id": vid, "titulo": e.find("a:title", ns).text, "publicado": e.find("a:published", ns).text,
                    "views": int(st.get("views")) if st is not None else None,
                    "likes": int(rt.get("count")) if rt is not None else None,
                    "short": "/shorts/" in e.find("a:link", ns).get("href", "")}
    return out

def flat(cid, aba):
    try:
        r = subprocess.run([YTDLP, "--flat-playlist", "-J", "--no-warnings",
                            f"https://www.youtube.com/channel/{cid}/{aba}"],
                           capture_output=True, text=True, encoding="utf-8", timeout=120, creationflags=NOWIN)
        d = json.loads(r.stdout) or {}
        if not isinstance(d, dict): return []
    except Exception:
        return []  # aba inexistente
    return [{"id": e["id"], "titulo": e.get("title"), "views": e.get("view_count"),
             "duracao": e.get("duration"), "short": aba == "shorts"} for e in (d.get("entries") or []) if e and e.get("id")]

def ultimo(hv):
    s = hv.get("serie") or []
    return s[-1][1] if s else None

def coletar_canal(cid, hist_videos, completo, pool):
    c = {"id": cid, "erro": None}
    fl = [pool.submit(flat, cid, a) for a in ("videos", "shorts")] if completo else []
    try: c.update(pagina(cid))
    except Exception as ex: c["erro"] = f"pagina: {ex}"
    try: r = rss(cid)
    except Exception as ex: r = {}; c["erro"] = f"rss: {ex}"
    vids = {}
    # base: lista conhecida (protege contra RSS/yt-dlp que às vezes voltam vazios)
    for vid, hv in hist_videos.items():
        if hv.get("canal") == cid:
            vids[vid] = {"id": vid, "titulo": hv.get("titulo"), "views": ultimo(hv),
                         "duracao": hv.get("duracao"), "short": hv.get("short", False)}
    if completo:
        for f in fl:
            for v in f.result():
                old = vids.get(v["id"], {})
                if v.get("views") is None: v["views"] = old.get("views")
                vids[v["id"]] = v
    for vid, v in r.items():
        base = vids.get(vid, {})
        merged = {**base, **{k: val for k, val in v.items() if val is not None}}
        if base.get("short"): merged["short"] = True
        merged["views"] = max([x for x in (base.get("views"), v.get("views")) if x is not None], default=None)
        vids[vid] = merged
    for vid, v in vids.items():
        old = hist_videos.get(vid, {})
        for k in ("publicado", "likes", "duracao"):
            if v.get(k) is None: v[k] = old.get(k)
        if v.get("views") is None: v["views"] = ultimo(old)
    c["videos"] = sorted(vids.values(), key=lambda v: v.get("publicado") or "", reverse=True)
    c["views_total"] = sum(v.get("views") or 0 for v in c["videos"])
    c["likes_total"] = sum(v.get("likes") or 0 for v in c["videos"])
    c["n_videos"] = len(c["videos"])
    c["n_shorts"] = sum(1 for v in c["videos"] if v.get("short"))
    return c

def afinar(serie, now, chave=lambda x: x[0]):
    """Tudo das últimas 48h; antes disso, 1 ponto por hora (histórico não incha)."""
    lim = (now - timedelta(hours=48)).isoformat(timespec="seconds")
    out, vistos = [], set()
    for p in serie:
        t = chave(p)
        if t >= lim: out.append(p); continue
        if t[:13] not in vistos: vistos.add(t[:13]); out.append(p)
    return out

def normalizar(s):
    """Link/@handle/ID -> 'UC...' ou '@handle' (None se não reconhecer)."""
    s = (s or "").split("#")[0].strip()
    m = re.search(r"(UC[\w-]{22})", s)
    if m: return m.group(1)
    m = re.search(r"@([\w.\-]+)", s)
    if m: return "@" + m.group(1)
    return None

def resolver(ent):
    """'@handle' -> 'UC...' lendo a página do canal."""
    if ent.startswith("UC"): return ent
    h = get(f"https://www.youtube.com/{ent}?hl=en")
    m = re.search(r'"externalId":"(UC[\w-]{22})"', h) or re.search(r'"channelId":"(UC[\w-]{22})"', h)
    return m.group(1) if m else None

def arquivo_canais():
    p = os.path.join(DATA, "canais.txt")
    return p if os.path.exists(p) else CANAIS_FILE

def ler_canais():
    """Lê canais.txt (aceita links/@handles), resolve para UC... e regrava o arquivo já resolvido."""
    if os.environ.get("CANAIS") and not os.path.exists(arquivo_canais()):
        return [i.strip() for i in os.environ["CANAIS"].split(",") if i.strip()]
    path = arquivo_canais()
    linhas = open(path, encoding="utf-8").read().splitlines()
    ids, novas, mudou = [], [], False
    for l in linhas:
        ent = normalizar(l)
        if not ent: novas.append(l); continue
        if not ent.startswith("UC"):
            try:
                uc = resolver(ent)
            except Exception:
                uc = None
            if uc:
                l = f"{uc}  # {ent}"; ent = uc; mudou = True
            else:
                novas.append(l); continue  # tenta de novo na próxima
        if ent not in ids: ids.append(ent); novas.append(l)
        else: mudou = True  # duplicado removido
    if mudou:
        open(path, "w", encoding="utf-8").write("\n".join(novas) + "\n")
    return ids

def run(modo="completo"):
    completo = modo == "completo"
    with LOCK:
        ids = ler_canais()
        hist = json.load(open(HIST_FILE, encoding="utf-8")) if os.path.exists(HIST_FILE) else {"snapshots": [], "videos": {}}
        # canal novo sem lista ainda -> força completo
        conhecidos = {hv.get("canal") for hv in hist["videos"].values()}
        if not completo and any(i not in conhecidos for i in ids) and not hist.get("ultimo_completo"):
            completo, modo = True, "completo"
        now = datetime.now(timezone.utc); agora = now.isoformat(timespec="seconds")
        with ThreadPoolExecutor(16) as pool, ThreadPoolExecutor(len(ids) or 1) as cpool:
            canais = list(cpool.map(lambda i: coletar_canal(i, hist["videos"], completo, pool), ids))
        for c in canais:
            for v in c["videos"]:
                hv = hist["videos"].setdefault(v["id"], {"canal": c["id"], "serie": []})
                hv["canal"] = c["id"]
                hv.update({k: v.get(k) for k in ("titulo", "publicado", "likes", "duracao", "short") if v.get(k) is not None})
                if v.get("views") is not None:
                    hv["serie"].append([agora, v["views"]])
                    hv["serie"] = afinar(hv["serie"], now)[-400:]
        hist["snapshots"].append({"t": agora, "modo": modo, "canais": {c["id"]: {"inscritos": c.get("inscritos"),
                                  "views": c["views_total"], "videos": c["n_videos"], "likes": c["likes_total"]} for c in canais}})
        hist["snapshots"] = afinar(hist["snapshots"], now, chave=lambda s: s["t"])
        if completo: hist["ultimo_completo"] = agora
        tmp = HIST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(hist, f, ensure_ascii=False)
        os.replace(tmp, HIST_FILE)
        for c in canais:
            for v in c["videos"]:
                v["serie"] = hist["videos"].get(v["id"], {}).get("serie", [])[-60:]
        payload = {"atualizado": agora, "ultimo_completo": hist.get("ultimo_completo"), "modo": modo,
                   "canais": canais, "snapshots": hist["snapshots"]}
        tmp = OUT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("window.PAINEL = " + json.dumps(payload, ensure_ascii=False) + ";\n")
        os.replace(tmp, OUT_FILE)
        with open(JSON_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(JSON_FILE + ".tmp", JSON_FILE)
        return payload

if __name__ == "__main__":
    import time; t = time.time()
    p = run(sys.argv[1] if len(sys.argv) > 1 else "completo")
    if not os.environ.get("GITHUB_ACTIONS"):  # na nuvem (log público) não imprime nomes
        for c in p["canais"]: print(c.get("nome"), c.get("inscritos"), c["views_total"], c["n_videos"], c["erro"] or "")
    else:
        print("canais:", len(p["canais"]), "erros:", sum(1 for c in p["canais"] if c["erro"]),
              "videos:", sum(c["n_videos"] for c in p["canais"]))
    print("OK", p["atualizado"], p["modo"], f"{time.time()-t:.1f}s")
