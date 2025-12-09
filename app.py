import os
import re
import time
import uuid
import json
from datetime import datetime, date
from threading import Thread
from flask import Flask, jsonify, send_from_directory, request
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --------- CAMINHOS DOS JOBS ----------
PASTA_RESERVAS = r"Q:\APPS\SAP\EP0\Job_Reservas"
PASTA_REQUISICOES = r"Q:\APPS\SAP\EP0\Job_Requisicao"
# --------------------------------------

CONFIRMADOS_FILE = "confirmados.json"

app = Flask(__name__, static_folder="static", static_url_path="/static")

DATE_RE = re.compile(r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b')


# ----------------------------------------------------
# JSON PERSISTÊNCIA
# ----------------------------------------------------
def salvar_confirmados():
    try:
        with open(CONFIRMADOS_FILE, "w") as f:
            json.dump(CACHE["confirmados"], f)
    except Exception as e:
        print("Erro salvando confirmados:", e)


def carregar_confirmados():
    if os.path.exists(CONFIRMADOS_FILE):
        try:
            with open(CONFIRMADOS_FILE, "r") as f:
                CACHE["confirmados"] = json.load(f)
        except Exception as e:
            print("Erro carregando confirmados:", e)


# ----------------------------------------------------
# AUX
# ----------------------------------------------------
def split_cols(line: str):
    parts = line.split("|")
    if len(parts) < 2:
        return []
    trimmed = [p.strip() for p in parts]
    return trimmed[1:-1]


def parse_date_str(s: str):
    if not s:
        return None
    m = DATE_RE.search(s)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d.%m.%Y").date()
    except:
        return None


# ----------------------------------------------------
# PARSER RESERVAS
# ----------------------------------------------------
def parse_reservas_file(path: str):
    cards = []

    try:
        with open(path, "r", encoding="latin-1", errors="ignore") as f:
            lines = f.readlines()
    except:
        return cards

    for raw in lines:
        texto = raw.rstrip("\n")

        if not texto.startswith("|"):
            continue
        if "Ordem" in texto or "Material" in texto:
            continue
        if "---" in texto:
            continue

        cols = split_cols(texto)

        if not cols:
            continue

        while len(cols) < 13:
            cols.append("")

        ordem = cols[0]
        dataNec = cols[1]
        material = cols[2]
        descricao = cols[3]
        quantidade = cols[4]
        unidade = cols[5]
        usuario = cols[6]
        reserva = cols[9]
        recebedor = cols[11]

        if not material and not descricao:
            continue

        d = parse_date_str(dataNec)

        cards.append({
            "id": str(uuid.uuid4()),
            "tipo": "reserva",

            "ordem": ordem,
            "material": material,
            "descricao": descricao,
            "quantidade": quantidade,
            "um": unidade,
            "usuario": usuario,
            "reserva": reserva,
            "recebedor": recebedor,

            "dataChegada": dataNec.strip(),
            "dataChegadaISO": d.isoformat() if d else None,

            "confirmado": False,
            "lista": ""
        })

    return cards


# ----------------------------------------------------
# PARSER REQUISIÇÕES
# ----------------------------------------------------
def parse_requisicoes_file(path: str):
    cards = []
    try:
        with open(path, "r", encoding="latin-1", errors="ignore") as f:
            lines = f.readlines()
    except:
        return cards

    def is_probable_user(s: str):
        if not s:
            return False
        if DATE_RE.search(s):
            return False
        if re.search(r"[A-Za-zÀ-ú]", s):
            return len(s) <= 80
        return False

    for raw in lines:
        texto = raw.rstrip("\n")

        if not texto.strip():
            continue
        if "Texto breve" in texto:
            continue
        if "---" in texto:
            continue
        if "|" not in texto:
            continue
        if texto.count("|") < 6:
            continue

        cols = split_cols(texto)

        while len(cols) < 12:
            cols.append("")

        reqc = cols[0]
        dataSolic = cols[1]
        dataRem = cols[2]
        mat = cols[3]
        desc = cols[4]
        quant = cols[5]
        um = cols[6]
        pedido = cols[7]

        usuario = ""
        for i in [8, 7, 5, 9, 10]:
            if len(cols) > i and is_probable_user(cols[i]):
                usuario = cols[i]
                break
        if not usuario:
            usuario = cols[8] if len(cols) > 8 else ""

        d = parse_date_str(dataRem)

        cards.append({
            "id": str(uuid.uuid4()),
            "tipo": "requisicao",

            "reqc": reqc,
            "material": mat,
            "descricao": desc,
            "quantidade": quant,
            "um": um,
            "usuario": usuario,
            "pedido": pedido,

            "dataSolic": dataSolic,
            "dataChegada": dataRem.strip(),
            "dataChegadaISO": d.isoformat() if d else None,

            "confirmado": False,
            "lista": ""
        })

    return cards


# ----------------------------------------------------
# CLASSIFICAR
# ----------------------------------------------------
def classify_cards(cards):
    hoje = date.today()

    em_dia = []
    entregue = []
    atraso = []

    for c in cards:
        cid = c["id"]
        c["confirmado"] = CACHE["confirmados"].get(cid)

        d = parse_date_str(c["dataChegada"])

        if d is None or d > hoje:
            c["lista"] = "em_dia"
            em_dia.append(c)
            continue

        if d < hoje:
            c["lista"] = "entregue"
            entregue.append(c)
            continue

        if cid not in CACHE["confirmados"]:
            c["lista"] = "em_dia"
            c["_prioritario"] = True
            em_dia.append(c)
            continue

        if CACHE["confirmados"][cid] is True:
            entregue.append(c)
        else:
            atraso.append(c)

    prioridade = [c for c in em_dia if c.get("_prioritario")]
    resto = [c for c in em_dia if not c.get("_prioritario")]

    for c in prioridade:
        c.pop("_prioritario", None)

    return {
        "em_dia": prioridade + resto,
        "entregue": entregue,
        "atraso": atraso
    }


# ----------------------------------------------------
# CACHE
# ----------------------------------------------------
CACHE = {
    "reservas": {"em_dia": [], "entregue": [], "atraso": []},
    "requisicoes": {"em_dia": [], "entregue": [], "atraso": []},
    "confirmados": {}
}


def carregar_cache():
    try:
        arq = [os.path.join(PASTA_RESERVAS, f) for f in os.listdir(PASTA_RESERVAS)]
        arq = [f for f in arq if os.path.isfile(f)]
        if arq:
            newest = max(arq, key=os.path.getmtime)
            CACHE["reservas"] = classify_cards(parse_reservas_file(newest))
    except:
        pass

    try:
        arq = [os.path.join(PASTA_REQUISICOES, f) for f in os.listdir(PASTA_REQUISICOES)]
        arq = [f for f in arq if os.path.isfile(f)]
        if arq:
            newest = max(arq, key=os.path.getmtime)
            CACHE["requisicoes"] = classify_cards(parse_requisicoes_file(newest))
    except:
        pass


# ----------------------------------------------------
# WATCHER
# ----------------------------------------------------
class WatchHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory:
            carregar_cache()

    def on_created(self, event):
        if not event.is_directory:
            carregar_cache()


def start_watch():
    obs = Observer()
    if os.path.isdir(PASTA_RESERVAS):
        obs.schedule(WatchHandler(), PASTA_RESERVAS)
    if os.path.isdir(PASTA_REQUISICOES):
        obs.schedule(WatchHandler(), PASTA_REQUISICOES)
    obs.start()
    try:
        while True:
            time.sleep(1)
    except:
        obs.stop()
    obs.join()


# ----------------------------------------------------
# API — CONFIRMAR
# ----------------------------------------------------
@app.route("/api/confirmar", methods=["POST"])
def confirmar():
    data = request.json
    cid = data["id"]
    chegou = data["chegou"]

    CACHE["confirmados"][cid] = chegou
    salvar_confirmados()

    CACHE["reservas"] = classify_cards(
        CACHE["reservas"]["em_dia"] + CACHE["reservas"]["entregue"] + CACHE["reservas"]["atraso"]
    )
    CACHE["requisicoes"] = classify_cards(
        CACHE["requisicoes"]["em_dia"] + CACHE["requisicoes"]["entregue"] + CACHE["requisicoes"]["atraso"]
    )

    return jsonify({"ok": True})


# ----------------------------------------------------
# ROTAS
# ----------------------------------------------------
@app.route("/api/reservas")
def api_reservas():
    return jsonify(CACHE["reservas"])


@app.route("/api/requisicoes")
def api_requisicoes():
    return jsonify(CACHE["requisicoes"])


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ----------------------------------------------------
# RUN
# ----------------------------------------------------
if __name__ == "__main__":
    carregar_confirmados()
    carregar_cache()
    Thread(target=start_watch, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)
