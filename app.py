import os
import re
import time
import uuid
from datetime import datetime, date
from threading import Thread
from flask import Flask, jsonify, send_from_directory, request
from waitress import serve
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

app = Flask(__name__, static_folder="static", static_url_path="/static")

DB_FILE = "app.db"
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_FILE}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

class Confirmacao(db.Model):
    __tablename__ = "confirmacoes"
    id = db.Column(db.String(64), primary_key=True)
    tipo = db.Column(db.String(20))
    chegou = db.Column(db.Boolean, nullable=False)
    quando = db.Column(db.DateTime, server_default=func.now(), onupdate=func.now())

with app.app_context():
    db.create_all()

PASTA_RESERVAS = r"Q:\APPS\SAP\EP0\Job_Reservas"
PASTA_REQUISICOES = r"Q:\APPS\SAP\EP0\Job_Requisicao"

DATE_RE = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b")

CACHE = {
    "reservas": {"em_dia": [], "entregue": [], "atraso": []},
    "requisicoes": {"em_dia": [], "entregue": [], "atraso": []},
    "confirmados": {}
}

def carregar_confirmados_db():
    CACHE["confirmados"].clear()
    with app.app_context():
        for c in Confirmacao.query.all():
            CACHE["confirmados"][c.id] = bool(c.chegou)

def salvar_confirmacao_db(cid, tipo, chegou):
    with app.app_context():
        rec = Confirmacao.query.get(cid)
        if rec is None:
            rec = Confirmacao(id=cid, tipo=tipo, chegou=chegou)
            db.session.add(rec)
        else:
            rec.chegou = chegou
        db.session.commit()

def split_cols(line):
    parts = [p.strip() for p in line.split("|")]
    return parts[1:-1] if len(parts) > 2 else []

def parse_date_str(s):
    m = DATE_RE.search(s or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d.%m.%Y").date()
    except:
        return None

def build_col_index(header_line):
    cols = split_cols(header_line)
    return {c.lower(): i for i, c in enumerate(cols)}

def get_col(cols, idx_map, *names):
    for n in names:
        i = idx_map.get(n.lower())
        if i is not None and i < len(cols):
            return cols[i]
    return ""

def parse_requisicoes_file(path):
    cards = []
    try:
        with open(path, "r", encoding="latin-1", errors="ignore") as f:
            lines = f.readlines()
    except:
        return cards

    header_idx = None
    col_map = {}

    for i, l in enumerate(lines):
        if "Texto breve" in l and "|" in l:
            header_idx = i
            col_map = build_col_index(l)
            break

    if header_idx is None:
        return cards

    hoje = date.today()

    for raw in lines[header_idx + 1:]:
        if "|" not in raw or "---" in raw:
            continue

        cols = split_cols(raw)
        if not cols:
            continue

        pedido = get_col(cols, col_map, "pedido").strip()
        reqc = get_col(cols, col_map, "reqc", "requisição").strip()
        material = get_col(cols, col_map, "material").strip()
        descricao = get_col(cols, col_map, "texto breve", "descrição").strip()
        quantidade = get_col(cols, col_map, "quantidade").strip()
        usuario = get_col(cols, col_map, "criado/a", "usuário").strip()
        preco_aval = get_col(cols, col_map, "preço aval").strip()
        val_total = get_col(cols, col_map, "val.total", "valor total").strip()
        data_rem = get_col(cols, col_map, "datarem.", "data remessa", "data rem.").strip()
        data_liber = get_col(
            cols,
            col_map,
            "dat.liber.",
            "dat.liber",
            "data liberação",
            "data liber."
        ).strip()
        data_solic = get_col(
            cols,
            col_map,
            "dtasolic.",
            "dtasolic",
            "data solicitação",
            "data solic."
        ).strip()

        if (
            not reqc or
            reqc.lower() in ("reqc", "requisição") or
            descricao.lower() == "texto breve"
        ):
            continue

        if usuario.lower() in ("criado/a", "usuario", "usuário"):
            usuario = ""

        d_rem = parse_date_str(data_rem)
        d_solic = parse_date_str(data_solic)

        if pedido:
            status_aprovacao = "SIM"
        elif d_solic:
            dias = (hoje - d_solic).days
            if dias < 30:
                status_aprovacao = "EM_AGUARDO"
            else:
                status_aprovacao = "NAO"
        else:
            status_aprovacao = "NAO"

        cards.append({
            "id": f"REQ-{reqc}",
            "tipo": "requisicao",
            "reqc": reqc,
            "pedido": pedido if pedido else None,
            "material": material,
            "descricao": descricao,
            "quantidade": quantidade,
            "usuario": usuario,
            "preco_aval": preco_aval,
            "val_total": val_total,
            "dataChegada": data_rem,
            "dataChegadaISO": d_rem.isoformat() if d_rem else None,
            "dataLiberacao": data_liber if data_liber else None,
            "dataSolicitacao": data_solic if data_solic else None,
            "aprovadoGestor": status_aprovacao,
            "confirmado": False,
            "lista": ""
        })

    return cards



def parse_reservas_file(path):
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
        if "Ordem" in texto or "Material" in texto or "---" in texto:
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
           "id": f"RES-{reserva.strip()}",
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

def classify_cards(cards):
    hoje = date.today()
    em_dia, entregue, atraso = [], [], []
    for c in cards:
        cid = c["id"]
        confirmado = CACHE["confirmados"].get(cid)  
        c["confirmado"] = confirmado
        c["perguntar"] = False

        d = parse_date_str(c["dataChegada"])
        if d is None or d > hoje:
            em_dia.append(c)
        elif d < hoje:
            entregue.append(c)
        else:
            if confirmado is None:
                c["perguntar"] = True
                em_dia.append(c)

            elif confirmado is True:
                entregue.append(c)

            else:
                c["perguntar"] = True
                atraso.append(c)

    return {
        "em_dia": em_dia,
        "entregue": entregue,
        "atraso": atraso
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
    while True:
        time.sleep(1)

@app.route("/api/confirmar", methods=["POST"])
def confirmar():
    data = request.json
    cid = data["id"]
    chegou = data["chegou"]
    tipo = data.get("tipo")

    salvar_confirmacao_db(cid, tipo, chegou)
    CACHE["confirmados"][cid] = chegou

    carregar_cache()  # <<< ISSO É CRUCIAL

    return jsonify({"ok": True})


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
    carregar_confirmados_db()
    carregar_cache()
    Thread(target=start_watch, daemon=True).start()
    serve(app, host="0.0.0.0", port=8000, threads=4)
