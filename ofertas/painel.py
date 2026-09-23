"""Painel de controle gráfico (interface web local) para o bot de ofertas.

Roda um servidor só em 127.0.0.1 e abre no navegador. Dali dá para:
- preencher a configuração (.env) em formulário, sem mexer no Bloco de Notas;
- instalar o navegador, fazer login no Mercado Livre e testar as fontes por botões;
- descobrir os IDs do Telegram automaticamente;
- ligar/desligar o bot e acompanhar o log ao vivo — tudo sem terminal.

Sobe com:  uv run python -m ofertas painel   (ou dê 2 cliques em PAINEL.bat)
"""
import json
import re
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import requests

from .config import BASE_DIR, DATA_DIR
from .painel_html import PAGINA

HOST, PORT = "127.0.0.1", 8481  # 8478=split-app, 8479=ClipOS/cortes — evita colisão
ENV_PATH = BASE_DIR / ".env"

# (chave, rótulo, grupo, é_segredo, ajuda)
CAMPOS = [
    ("TELEGRAM_BOT_TOKEN", "Token do bot", "Telegram", True,
     "Crie no @BotFather com /newbot e cole o token aqui."),
    ("TELEGRAM_OWNER_ID", "Seu user ID", "Telegram", False,
     "Use o botão 'Detectar IDs' depois de colocar o token."),
    ("TELEGRAM_CHAT_ID", "ID do canal", "Telegram", False,
     "O canal onde o bot posta. Use 'Detectar IDs'."),
    ("TELEGRAM_CHAT_ID_PESSOAL", "ID do canal pessoal", "Telegram", False,
     "Opcional. Só recebe pedido de cliente marcado com 'destino: pessoal' em pedidos.yaml; sem ele, esses "
     "pedidos caem no canal. Use 'Detectar IDs'."),
    ("TELEGRAM_CHAT_ID_APPLE", "ID do grupo Apple", "Telegram", False,
     "Opcional. Grupo só de produtos Apple; sem ele, tudo vai para o canal. Use 'Detectar IDs'."),
    ("ML_ETIQUETA", "Etiqueta do afiliado", "Mercado Livre", False,
     "A 'Etiqueta em uso' que aparece no Linkbuilder do painel de afiliados."),
    ("AMAZON_TAG", "Tag de associado", "Amazon", False,
     "Sua tag do Amazon Associados (ex: seunome-20)."),
    ("AMAZON_CREDENTIAL_ID", "Creators API — ID", "Amazon", False,
     "Opcional. Associates Central > Creators API > Aplicativos."),
    ("AMAZON_CREDENTIAL_SECRET", "Creators API — Secret", "Amazon", True,
     "Opcional. Aparece só uma vez, na criação da credencial."),
    ("SHOPEE_APP_ID", "App ID", "Shopee", False,
     "Painel de afiliados Shopee > menu 'Abrir API'."),
    ("SHOPEE_APP_SECRET", "App Secret", "Shopee", True,
     "Painel de afiliados Shopee > menu 'Abrir API'."),
]
CHAVES = [c[0] for c in CAMPOS]


# ── .env ──────────────────────────────────────────────────────────────

def ler_env() -> dict[str, str]:
    valores = {k: "" for k in CHAVES}
    if ENV_PATH.exists():
        for linha in ENV_PATH.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, _, v = linha.partition("=")
                if k.strip() in valores:
                    valores[k.strip()] = v.strip()
    return valores


def salvar_env(novos: dict[str, str]) -> None:
    atuais = ler_env()
    for k, v in novos.items():
        if k in atuais:
            atuais[k] = str(v).strip()
    linhas = ["# Configuração do bot de ofertas (gerado pelo painel).",
              "# Não compartilhe este arquivo — ele guarda seus segredos.", ""]
    grupo_atual = None
    for chave, rotulo, grupo, *_ in CAMPOS:
        if grupo != grupo_atual:
            linhas.append(f"# ── {grupo} ──")
            grupo_atual = grupo
        linhas.append(f"{chave}={atuais.get(chave, '')}")
    ENV_PATH.write_text("\n".join(linhas) + "\n", encoding="utf-8")


# ── Processo do bot ───────────────────────────────────────────────────

class Processo:
    """Encapsula um subprocesso (o bot, ou uma ação) e guarda o log recente."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.linhas: deque[str] = deque(maxlen=500)
        self.rotulo = ""

    def rodando(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def iniciar(self, args: list[str], rotulo: str) -> bool:
        if self.rodando():
            return False
        self.rotulo = rotulo
        self.linhas.append(f"▶ {rotulo}...")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "ofertas", *args],
            cwd=str(BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._ler, daemon=True).start()
        return True

    def _ler(self):
        for linha in self.proc.stdout:
            self.linhas.append(linha.rstrip())
        cod = self.proc.wait()
        fim = "concluído ✓" if cod == 0 else f"terminou (código {cod})"
        self.linhas.append(f"■ {self.rotulo}: {fim}")

    def parar(self):
        if self.rodando():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.linhas.append("■ Bot parado.")


bot = Processo()      # o bot (run), fica ligado
acao = Processo()     # ações pontuais (instalar, login, testar)


# ── Ações auxiliares ──────────────────────────────────────────────────

_RE_TITULO_APPLE = re.compile(r"\b(?:apple|iphone|ipad|macbook|imac|airpods?|airtag|ios|mac)\b", re.I)


def sugerir_destino(titulo: str) -> str:
    """"apple" se o NOME do canal/grupo fala de Apple ("Promoções Apple", "iPhone Barato"); senão "geral"."""
    return "apple" if _RE_TITULO_APPLE.search(titulo or "") else "geral"


_CAMPO_POR_DESTINO = {"geral": "TELEGRAM_CHAT_ID", "pessoal": "TELEGRAM_CHAT_ID_PESSOAL", "apple": "TELEGRAM_CHAT_ID_APPLE"}


def _campo_atual(chat_id, username: str, env: dict[str, str]) -> str | None:
    """Em qual destino este chat já está: "geral", "pessoal", "apple" ou None. Compara o ID numérico e, para
    canais públicos gravados como @nome, o username."""
    def igual(valor: str) -> bool:
        return bool(valor) and (valor == str(chat_id) or (bool(username) and valor.lower() == f"@{username}".lower()))
    for destino, campo in _CAMPO_POR_DESTINO.items():
        if igual(env.get(campo, "")):
            return destino
    return None


def detectar_ids() -> dict:
    """Consulta o Telegram (getUpdates) e lista o seu usuário e os canais/grupos onde o bot apareceu. Cada
    canal/grupo vem com a sugestão de destino (pelo nome) e o campo que ele já ocupa no .env."""
    env = ler_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"erro": "Preencha e salve o token do bot primeiro."}
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
        dados = r.json()
    except Exception as e:
        return {"erro": f"Não consegui falar com o Telegram: {e}"}
    if not dados.get("ok"):
        descricao = dados.get("description", "?")
        if "Conflict" in descricao:
            return {"erro": "O bot está ligado e já está lendo as mensagens do Telegram. Desligue o bot aqui no "
                            "painel, clique em Detectar IDs de novo e depois ligue-o outra vez."}
        return {"erro": f"Telegram recusou o token: {descricao}"}

    pessoas: dict[int, str] = {}
    canais: dict[int, dict] = {}

    def anotar_chat(chat: dict) -> None:
        if not chat.get("id"):
            return
        if chat.get("type") == "private":
            nome = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            pessoas[chat["id"]] = nome or chat.get("username") or str(chat["id"])
        elif chat.get("type") in ("channel", "supergroup", "group"):
            canais[chat["id"]] = {"id": chat["id"], "nome": chat.get("title") or str(chat["id"]),
                                  "tipo": "canal" if chat["type"] == "channel" else "grupo",
                                  "username": chat.get("username") or ""}

    for upd in dados.get("result", []):
        # message/channel_post: alguém escreveu no chat. my_chat_member: o bot foi adicionado (ou removido) de um
        # chat, o que aparece mesmo antes de qualquer mensagem.
        for tipo in ("message", "channel_post", "edited_message", "edited_channel_post", "my_chat_member", "chat_member"):
            msg = upd.get(tipo) or {}
            anotar_chat(msg.get("chat") or {})
            origem = msg.get("forward_from_chat") or {}
            if origem.get("type") == "channel":
                anotar_chat(origem)
    for c in canais.values():
        c["sugestao"] = sugerir_destino(c["nome"])
        c["atual"] = _campo_atual(c["id"], c["username"], env)
    return {
        "pessoas": [{"id": i, "nome": n} for i, n in pessoas.items()],
        "canais": list(canais.values()),
        "vazio": not pessoas and not canais,
    }


def status() -> dict:
    env = ler_env()
    tem_navegador = bool(list((DATA_DIR / "pw-browsers").glob("chromium-*")))
    perfil = DATA_DIR / "ml_profile"
    tem_sessao_ml = perfil.exists() and any(perfil.iterdir())
    return {
        "bot_rodando": bot.rodando(),
        "acao_rodando": acao.rotulo if acao.rodando() else "",
        "preenchidos": {k: bool(env.get(k)) for k in CHAVES},
        "navegador": tem_navegador,
        "sessao_ml": tem_sessao_ml,
        "pronto": bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")
                       and env.get("TELEGRAM_OWNER_ID")),
    }


# ── Servidor HTTP ─────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # silencia o log padrão

    def _json(self, obj, code=200):
        corpo = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _corpo_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self):
        rota = urlparse(self.path).path
        if rota == "/":
            corpo = PAGINA.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif rota == "/api/status":
            self._json(status())
        elif rota == "/api/config":
            env = ler_env()
            # nunca devolve segredo preenchido em texto puro; manda só se está setado
            saida = {}
            for chave, _, _, segredo, _ in CAMPOS:
                saida[chave] = "" if (segredo and env.get(chave)) else env.get(chave, "")
                saida[chave + "__set"] = bool(env.get(chave))
            self._json(saida)
        elif rota == "/api/logs":
            fonte = urlparse(self.path).query
            alvo = acao if "acao" in fonte else bot
            self._json({"linhas": list(alvo.linhas)})
        elif rota == "/api/nichos":
            from .nichos import catalogo, ler_selecao
            self._json({"catalogo": catalogo(), "selecionados": ler_selecao()})
        else:
            self._json({"erro": "rota desconhecida"}, 404)

    def do_POST(self):
        rota = urlparse(self.path).path
        dados = self._corpo_json()
        if rota == "/api/config":
            # não sobrescreve segredo com vazio (campo em branco = manter o atual)
            atuais = ler_env()
            filtrados = {}
            for chave, _, _, segredo, _ in CAMPOS:
                v = dados.get(chave, "")
                if segredo and not v and atuais.get(chave):
                    continue
                filtrados[chave] = v
            ids = [(destino, filtrados.get(campo, "")) for destino, campo in _CAMPO_POR_DESTINO.items()]
            for i, (nome_a, id_a) in enumerate(ids):
                for nome_b, id_b in ids[i + 1:]:
                    if id_a and id_a == id_b:
                        self._json({"erro": f"O canal {nome_a} e o {nome_b} estão com o MESMO ID. Cada um precisa "
                                            "do seu: use os botões de 'Detectar IDs' e escolha o destino de cada chat."})
                        return
            salvar_env(filtrados)
            self._json({"ok": True})
        elif rota == "/api/start":
            ok = bot.iniciar(["run"], "Bot")
            self._json({"ok": ok, "rodando": bot.rodando()})
        elif rota == "/api/stop":
            bot.parar()
            self._json({"ok": True, "rodando": bot.rodando()})
        elif rota == "/api/acao":
            nome = dados.get("nome", "")
            mapa = {
                "instalar-navegador": (["instalar-navegador"], "Instalando navegador"),
                "ml-login": (["ml-login"], "Login no Mercado Livre"),
                "testar-ml": (["testar", "ml"], "Testando Mercado Livre"),
                "testar-shopee": (["testar", "shopee"], "Testando Shopee"),
                "testar-amazon": (["testar", "amazon"], "Testando Amazon"),
            }
            if nome not in mapa:
                return self._json({"erro": "ação desconhecida"}, 400)
            if acao.rodando():
                return self._json({"erro": f"Já rodando: {acao.rotulo}"}, 409)
            args, rotulo = mapa[nome]
            acao.linhas.clear()
            acao.iniciar(args, rotulo)
            self._json({"ok": True})
        elif rota == "/api/detectar-ids":
            self._json(detectar_ids())
        elif rota == "/api/nichos":
            from .nichos import salvar_selecao
            salvar_selecao(dados.get("selecionados") or [])
            self._json({"ok": True})
        else:
            self._json({"erro": "rota desconhecida"}, 404)


def painel():
    url = f"http://{HOST}:{PORT}/"
    servidor = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"\n  Painel de controle aberto em {url}")
    print("  (deixe esta janela aberta; feche-a para desligar o painel)\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bot.parar()
        servidor.shutdown()
