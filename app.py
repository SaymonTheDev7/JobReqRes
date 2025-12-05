# app.py — versão FINAL com dataChegada + atraso + watcher corrigido
import os
import re
import time
import uuid
from datetime import datetime, date
from threading import Thread
from flask import Flask, jsonify, send_from_directory, request
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --------- CAMINHOS DOS JOBS ----------
PASTA_RESERVAS = r"Q:\APPS\SAP\EP0\Job_Reservas"
PASTA_REQUISICOES = r"Q:\APPS\SAP\EP0\Job_Requisicao"
# --------------------------------------

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Regex para DD.MM.AAAA
DATE_RE = re.compile(r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b')


# ----------------------------------------------------
# FUNÇÕES AUXILIARES
# ----------------------------------------------------

def is_separator(line: str):
    return line.count('-') > 20 or line.strip() == ''


def split_cols(line: str):
    parts = [p.strip() for p in line.split('|')]
    return [p for p in parts if p != ""]


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
# PARSER — RESERVAS
# ----------------------------------------------------

def parse_reservas_file(path: str):
    cards = []

    try:
        with open(path, 'r', encoding='latin-1', errors='ignore') as f:
            lines = f.readlines()
    except:
        return cards

    for raw in lines:
        if '|' not in raw or is_separator(raw):
            continue

        cols = split_cols(raw)
        if len(cols) < 13:
            continue

        ordem, dataNec, material, descricao, quantidade, um, usuario, cc, centro, reserva, umb, recebedor, item = cols[:13]

        d = parse_date_str(dataNec)

        cards.append({
            "id": str(uuid.uuid4()),
            "tipo": "reserva",

            "ordem": ordem,
            "material": material,
            "descricao": descricao,
            "quantidade": quantidade,
            "usuario": usuario,
            "reserva": reserva,

            # padronização
            "dataChegada": dataNec.strip(),
            "dataChegadaISO": d.isoformat() if d else None,

            "raw": raw.strip()
        })

    cards.sort(key=lambda c: c.get("dataChegadaISO") or "0000-00-00", reverse=True)
    return cards[:200]


# ----------------------------------------------------
# PARSER — REQUISIÇÕES ME5A
# ----------------------------------------------------

def parse_requisicoes_file(path: str):
    cards = []

    try:
        with open(path, 'r', encoding='latin-1', errors='ignore') as f:
            lines = f.readlines()
    except:
        return cards

    for raw in lines:
        if '|' not in raw or is_separator(raw):
            continue

        cols = split_cols(raw)
        if len(cols) < 13:
            continue

        pedido = cols[0]
        reqc = cols[1]
        material = cols[2]
        descricao = cols[3]
        quantidade = cols[4]
        usuario = cols[5]
        fornecedor = cols[8]
        dataSolic = cols[9]
        dataRem = cols[11]

        d = parse_date_str(dataRem)

        cards.append({
            "id": str(uuid.uuid4()),
            "tipo": "requisicao",

            "pedido": pedido,
            "reqc": reqc,
            "material": reqc,
            "descricao": descricao,
            "quantidade": quantidade,
            "usuario": usuario,
            "fornecedor": fornecedor,
            "dataSolic": dataSolic,

            # padronização:
            "dataChegada": dataRem.strip(),
            "dataChegadaISO": d.isoformat() if d else None,

            "raw": raw.strip()
        })

    cards.sort(key=lambda c: c.get("dataChegadaISO") or "0000-00-00", reverse=True)
    return cards[:300]


# ----------------------------------------------------
# CLASSIFICAÇÃO
# ----------------------------------------------------

def classify_cards(cards):
    hoje = date.today()

    em_dia = []
    entregue = []
    atraso = []

    for c in cards:

        data_str = c.get("dataChegada", "").strip()
        d = parse_date_str(data_str)

        # 1) Data inválida → EM DIA
        if d is None:
            em_dia.append(c)
            continue

        # 2) FUTURO → EM DIA normal
        if d > hoje:
            em_dia.append(c)
            continue

        # 3) PASSADO → ENTREGUE automático
        if d < hoje:
            entregue.append(c)
            continue

        # 4) HOJE → precisa perguntar
        cid = c["id"]

        # ainda não respondeu → fica em dia, mas PRIORITÁRIO
        if cid not in CACHE["confirmados"]:
            c["_prioritario"] = True    # marca para ordenar depois
            em_dia.append(c)
            continue

        # respondeu: chegou → entregue
        if CACHE["confirmados"][cid] is True:
            entregue.append(c)
        else:
            atraso.append(c)

    # ------------------------------------------------
    # ORDENAR EM DIA: prioridade primeiro
    # ------------------------------------------------
    prioridade = [c for c in em_dia if c.get("_prioritario")]
    resto = [c for c in em_dia if not c.get("_prioritario")]

    # junta com prioridade primeiro
    em_dia_ordenado = prioridade + resto

    # remove a flag antes de enviar ao frontend
    for c in em_dia_ordenado:
        c.pop("_prioritario", None)

    return {
        "em_dia": em_dia_ordenado,
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

    # RESERVAS
    try:
        arquivos = [
            os.path.join(PASTA_RESERVAS, f)
            for f in os.listdir(PASTA_RESERVAS)
        ]
        arquivos = [f for f in arquivos if os.path.isfile(f)]

        if arquivos:
            newest = max(arquivos, key=os.path.getmtime)
            cards = parse_reservas_file(newest)
            CACHE["reservas"] = classify_cards(cards)

    except Exception as e:
        print("Erro reservas:", e)

    # REQUISIÇÕES
    try:
        arquivos = [
            os.path.join(PASTA_REQUISICOES, f)
            for f in os.listdir(PASTA_REQUISICOES)
        ]
        arquivos = [f for f in arquivos if os.path.isfile(f)]

        if arquivos:
            newest = max(arquivos, key=os.path.getmtime)
            cards = parse_requisicoes_file(newest)
            CACHE["requisicoes"] = classify_cards(cards)

    except Exception as e:
        print("Erro requisicoes:", e)


# ----------------------------------------------------
# MONITORAMENTO DE ARQUIVOS SAP
# ----------------------------------------------------

class WatchHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory:
            print("Arquivo modificado:", event.src_path)
            carregar_cache()

    def on_created(self, event):
        if not event.is_directory:
            print("Arquivo criado:", event.src_path)
            carregar_cache()


def start_watch():
    obs = Observer()

    if os.path.isdir(PASTA_RESERVAS):
        obs.schedule(WatchHandler(), PASTA_RESERVAS, recursive=False)

    if os.path.isdir(PASTA_REQUISICOES):
        obs.schedule(WatchHandler(), PASTA_REQUISICOES, recursive=False)

    obs.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()


# ----------------------------------------------------
# API – Confirmar Chegada
# ----------------------------------------------------

@app.route("/api/confirmar", methods=["POST"])
def confirmar():
    data = request.json
    id = data["id"]
    chegou = data["chegou"]
    CACHE["confirmados"][id] = chegou
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
# INICIAR SERVIDOR
# ----------------------------------------------------

if __name__ == "__main__":
    carregar_cache()
    Thread(target=start_watch, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)
