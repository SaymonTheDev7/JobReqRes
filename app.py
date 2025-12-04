# app.py (versão revisada de parser)
import os
import re
import time
from datetime import datetime, date
from threading import Thread
from flask import Flask, jsonify, send_from_directory
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --------- CAMINHOS DOS JOBS ----------
PASTA_RESERVAS = r"Q:\APPS\SAP\EP0\Job_Reservas"
PASTA_REQUISICOES = r"Q:\APPS\SAP\EP0\Job_Requisicao"
# --------------------------------------

app = Flask(__name__, static_folder="static", static_url_path="/static")

# regex pra detectar datas no formato DD.MM.AAAA
DATE_RE = re.compile(r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b')

# ignorar linhas que são só traços (bordas)
def is_separator(line: str):
    return line.count('-') > 20 or line.strip() == ''

def split_cols(line: str):
    # split mantendo conteúdo entre pipes; remove células vazias ocasionais
    parts = [p.strip() for p in line.split('|')]
    parts = [p for p in parts if p != ""]
    return parts

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

def parse_reservas_file(path: str):
    cards = []
    try:
        with open(path, 'r', encoding='latin-1', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        print("Erro abrindo reservas:", e)
        return cards

    for raw in lines:
        if '|' not in raw:
            continue
        if is_separator(raw):
            continue

        # se não houver data no texto, pular
        if not DATE_RE.search(raw):
            continue

        cols = split_cols(raw)
        if not cols:
            continue

        # encontrar índice da coluna que tem a data (a primeira ocorrência)
        date_idx = None
        for i, c in enumerate(cols):
            if DATE_RE.search(c):
                date_idx = i
                break
        if date_idx is None:
            continue

        # a partir do date_idx mapeamos os campos com heurística:
        # Data nec. = cols[date_idx]
        # Material = cols[date_idx+1]
        # Descricao = cols[date_idx+2]
        # QtdNec = cols[date_idx+3]
        # UMR = cols[date_idx+4]
        # Usuario = cols[date_idx+5]
        # Reserva = cols[date_idx+6]
        def get(i):
            return cols[i] if i < len(cols) else ""

        data_str = get(date_idx)
        data_dt = parse_date_str(data_str)
        material = get(date_idx + 1)
        descricao = get(date_idx + 2)
        quantidade = get(date_idx + 3)
        um = get(date_idx + 4)
        usuario = get(date_idx + 5)
        reserva = get(date_idx + 6)

        card = {
            "dataNec": data_str,
            "dataNecDate": data_dt.isoformat() if data_dt else None,
            "material": material,
            "descricao": descricao,
            "quantidade": quantidade,
            "um": um,
            "usuario": usuario,
            "reserva": reserva,
            "raw": raw.strip()
        }
        cards.append(card)

    # ordenar por data (se disponível) descendente; items sem data ficam no final
    def sort_key(it):
        d = it.get("dataNecDate")
        return d if d is not None else "0000-00-00"
    cards.sort(key=lambda x: sort_key(x), reverse=True)

    # limitar (configurável): os mais recentes primeiro
    return cards[:200]


def parse_requisicoes_file(path: str):
    cards = []
    try:
        with open(path, 'r', encoding='latin-1', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        print("Erro abrindo requisicoes:", e)
        return cards

    for raw in lines:
        if '|' not in raw:
            continue
        if is_separator(raw):
            continue
        # muitas linhas de requisição têm data em colunas variadas; exigir presença de data
        if not DATE_RE.search(raw):
            continue

        cols = split_cols(raw)
        if not cols:
            continue

        # encontrar índice da primeira data (pode ser DtaSolic. ou DataRem.)
        date_idx = None
        for i, c in enumerate(cols):
            if DATE_RE.search(c):
                date_idx = i
                break
        if date_idx is None:
            continue

        # heurística comum (observada nos arquivos que você enviou):
        # se date_idx apontar para DtaSolic, tentar usar a próxima data como DataRem
        # tentamos localizar a coluna com DataRem procurando a segunda ocorrência de data na mesma linha
        # se houver apenas uma data, assumimos que é DataRem quando a estrutura for compatível
        date_positions = [i for i, c in enumerate(cols) if DATE_RE.search(c)]
        data_rem_idx = None
        if len(date_positions) >= 2:
            # segunda data costuma ser DataRem
            data_rem_idx = date_positions[1]
        else:
            # se só há uma data, assumimos que é DataRem em muitas linhas
            data_rem_idx = date_positions[0] if date_positions else None

        if data_rem_idx is None:
            continue

        def get(i):
            return cols[i] if i < len(cols) else ""

        data_rem_str = get(data_rem_idx)
        data_rem_dt = parse_date_str(data_rem_str)

        # material tipicamente aparece logo após a dataRem (observado)
        material = get(data_rem_idx + 1)
        descricao = get(data_rem_idx + 2)
        quantidade = get(data_rem_idx + 3)
        um = get(data_rem_idx + 4)
        pedido = get(data_rem_idx + 6) if len(cols) > data_rem_idx + 6 else ""

        card = {
            "dataRem": data_rem_str,
            "dataRemDate": data_rem_dt.isoformat() if data_rem_dt else None,
            "material": material,
            "descricao": descricao,
            "quantidade": quantidade,
            "um": um,
            "pedido": pedido,
            "raw": raw.strip()
        }
        cards.append(card)

    cards.sort(key=lambda x: x.get("dataRemDate") or "0000-00-00", reverse=True)
    return cards[:200]


def classify_cards(cards, date_key):
    hoje = date.today()
    em_dia = []
    entregue = []
    for c in cards:
        d_str = c.get(date_key)
        d = None
        if d_str:
            try:
                # já guardamos como ISO quando possível; parse se necessário
                if re.match(r'\d{4}-\d{2}-\d{2}', d_str):
                    d = datetime.strptime(d_str, "%Y-%m-%d").date()
                else:
                    # tentar localizar dd.mm.yyyy
                    m = DATE_RE.search(d_str)
                    if m:
                        d = datetime.strptime(m.group(1), "%d.%m.%Y").date()
            except:
                d = None
        # sem data => considerar em dia por padrão
        if d is None:
            em_dia.append(c)
        else:
            if d <= hoje:
                entregue.append(c)
            else:
                em_dia.append(c)
    return {"em_dia": em_dia, "entregue": entregue}


# cache simples
CACHE = {
    "reservas": {"em_dia": [], "entregue": []},
    "requisicoes": {"em_dia": [], "entregue": []}
}

def carregar_cache():
    # reservas
    try:
        arquivos = [os.path.join(PASTA_RESERVAS, f) for f in os.listdir(PASTA_RESERVAS) if os.path.isfile(os.path.join(PASTA_RESERVAS, f))]
        if arquivos:
            newest = max(arquivos, key=os.path.getmtime)
            cards = parse_reservas_file(newest)
            CACHE["reservas"] = classify_cards(cards, "dataNec")
        else:
            CACHE["reservas"] = {"em_dia": [], "entregue": []}
    except Exception as e:
        print("Erro carregar_cache reservas:", e)

    # requisicoes
    try:
        arquivos = [os.path.join(PASTA_REQUISICOES, f) for f in os.listdir(PASTA_REQUISICOES) if os.path.isfile(os.path.join(PASTA_REQUISICOES, f))]
        if arquivos:
            newest = max(arquivos, key=os.path.getmtime)
            cards = parse_requisicoes_file(newest)
            CACHE["requisicoes"] = classify_cards(cards, "dataRem")
        else:
            CACHE["requisicoes"] = {"em_dia": [], "entregue": []}
    except Exception as e:
        print("Erro carregar_cache requisicoes:", e)

class WatchHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.is_directory:
            return
        print("Arquivo modificado:", event.src_path)
        carregar_cache()

    def on_created(self, event):
        if event.is_directory:
            return
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

@app.route("/api/reservas")
def api_reservas():
    return jsonify(CACHE["reservas"])

@app.route("/api/requisicoes")
def api_requisicoes():
    return jsonify(CACHE["requisicoes"])

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

if __name__ == "__main__":
    carregar_cache()
    Thread(target=start_watch, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)
