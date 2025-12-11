# JobReqRes

Aplicação Flask para monitorar arquivos de reservas e requisições (SAP) em pastas compartilhadas, exibindo um painel web e permitindo confirmação de recebimento. Empacota em executável via PyInstaller e usa Waitress como servidor WSGI em produção.

## Requisitos
- Python 3.11
- Windows (paths de rede mapeados)
- Dependências: `pip install -r requirements.txt`
- Criar Executavel: pyinstaller JobReqRes.spec

## Configuração
- Ajuste, se necessário, os caminhos em `app.py`:
  - `PASTA_RESERVAS = r"Q:\\APPS\\SAP\\EP0\\Job_Reservas"`
  - `PASTA_REQUISICOES = r"Q:\\APPS\\SAP\\EP0\\Job_Requisicao"`
- O banco SQLite (`app.db`) é criado na raiz na primeira execução.

## Executar em modo desenvolvimento
```bash
D:/JobReqRes-main/venv/Scripts/python.exe app.py
```
Acesse: http://localhost:8000

## Executar em modo produção (Waitress)
Já configurado no bloco `if __name__ == "__main__":`:
```bash
D:/JobReqRes-main/venv/Scripts/python.exe app.py
```

## Frontend
- Servido em `/` a partir de `static/index.html`.
- Favicon em `static/favicon.ico`.

## Build do executável (PyInstaller)
Gera binário one-file com dados estáticos e ícone.
```bash
D:/JobReqRes-main/venv/Scripts/python.exe -m PyInstaller JobReqRes.spec
```
Saída:
- Executável: `dist/JobReqRes.exe`
- Build: `build/JobReqRes/`

## Endpoints
- `GET /api/reservas`
- `GET /api/requisicoes`
- `POST /api/confirmar` (body JSON: `id`, `chegou`, `tipo`)

## Notas
- O watchdog acompanha alterações nas pastas configuradas e recarrega o cache.
- Confirmações ficam persistidas em `app.db` (tabela `confirmacoes`).
