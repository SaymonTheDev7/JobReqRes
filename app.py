import os
import re
import time
import uuid
from datetime import datetime, date
from threading import Thread
from flask import Flask, jsonify, send_from_directory, request
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

app = Flask(__name__, static_folder="static", static_url_path="/static")

DB_FILE = "app.db"
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{DB_FILE}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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

DATE_RE = re.compile(r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b')

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

def split_cols(line: str):
    parts = line.split("|")
    trimmed = [p.strip() for p in parts]
    return trimmed[1:-1] if len(trimmed) > 2 else []

def parse_date_str(s: str):
    m = DATE_RE.search(s or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d.%m.%Y").date()
    except:
        return None

def parse_requisicoes_file(path: str):
    cards = []
    try:
        with open(path, "r", encoding="latin-1", errors="ignore") as f:
            lines = f.readlines()
    except:
        return cards

    modo_lista_compra = any("Exibir lista de requisições de compra" in l for l in lines)

    # ----------------------------------------------------------
    #  MODO "LISTA DE REQUISIÇÕES DE COMPRA" → usa colunas fixas
    # ----------------------------------------------------------
    if modo_lista_compra:
        for raw in lines:
            texto = raw.rstrip("\n")
            if "|" not in texto or "Material" in texto or "---" in texto:
                continue

            cols = split_cols(texto)
            while len(cols) < 13:
                cols.append("")

            reqc = cols[1]
            mat = cols[2]
            desc = cols[3]
            quant = cols[4]
            usuario = cols[5]
            preco_aval = cols[6]
            val_total = cols[7]    # ← AQUI PEGAMOS O VALOR TOTAL
            dataRem = cols[11]

            d = parse_date_str(dataRem)

            cards.append({
                "id": str(uuid.uuid4()),
                "tipo": "requisicao",
                "reqc": reqc,
                "material": mat,
                "descricao": desc,
                "quantidade": quant,
                "um": "",
                "usuario": usuario,
                "pedido": cols[0],
                "preco_aval": preco_aval,
                "val_total": val_total,    # ← JÁ ENVIADO AO FRONT
                "dataSolic": "",
                "dataChegada": dataRem.strip(),
                "dataChegadaISO": d.isoformat() if d else None,
                "confirmado": False,
                "lista": ""
            })

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

        if "Texto breve" in texto or "---" in texto:
            continue
        if "|" not in texto or texto.count("|") < 6:
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
        for i in [8,7,5,9,10]:
            if len(cols) > i and is_probable_user(cols[i]):
                usuario = cols[i]
                break
        if not usuario:
            usuario = cols[8]

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

def parse_reservas_file(path: str):
    cards = []
    try:
        with open(path,"r",encoding="latin-1",errors="ignore") as f:
            lines=f.readlines()
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
            c["_prioritario"] = True
            c["lista"] = "em_dia"
            em_dia.append(c)
            continue

        if CACHE["confirmados"][cid]:
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
    try:
        while True:
            time.sleep(1)
    except:
        obs.stop()
    obs.join()

@app.route("/api/confirmar", methods=["POST"])
def confirmar():
    data = request.json
    cid = data["id"]
    chegou = data["chegou"]
    tipo = data.get("tipo")
    salvar_confirmacao_db(cid, tipo, chegou)
    CACHE["confirmados"][cid] = chegou
    CACHE["reservas"] = classify_cards(CACHE["reservas"]["em_dia"]+CACHE["reservas"]["entregue"]+CACHE["reservas"]["atraso"])
    CACHE["requisicoes"] = classify_cards(CACHE["requisicoes"]["em_dia"]+CACHE["requisicoes"]["entregue"]+CACHE["requisicoes"]["atraso"])
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

if __name__=="__main__":
    carregar_confirmados_db()
    carregar_cache()
    Thread(target=start_watch, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)
