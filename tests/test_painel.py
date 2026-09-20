"""Painel: "Detectar IDs" sugere o destino de cada canal/grupo e não deixa o mesmo ID nos dois campos."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from ofertas import config as cfg
from ofertas import painel

APPLE, GERAL = -1002222222222, -1001111111111


@pytest.mark.parametrize("titulo, destino", [
    ("Promoções Apple", "apple"),
    ("Caçador Promo iPhone", "apple"),
    ("iOS Deals", "apple"),
    ("Mac Promo", "apple"),
    ("AirPods e Cia", "apple"),
    ("Caçador Promo", "geral"),
    ("Ofertas Gerais", "geral"),
    ("Pinapple Store", "geral"),          # "apple" dentro de outra palavra não conta
    ("Macarrão Barato", "geral"),         # nem "mac"
    ("", "geral"),
])
def test_sugerir_destino_pelo_nome(titulo, destino):
    assert painel.sugerir_destino(titulo) == destino


def test_campo_atual_por_id_e_por_username():
    env = {"TELEGRAM_CHAT_ID": str(GERAL), "TELEGRAM_CHAT_ID_APPLE": "@grupo_apple"}
    assert painel._campo_atual(GERAL, "", env) == "geral"
    assert painel._campo_atual(APPLE, "grupo_apple", env) == "apple"       # canal público gravado como @nome
    assert painel._campo_atual(APPLE, "", env) is None
    assert painel._campo_atual(1, "", {"TELEGRAM_CHAT_ID": "", "TELEGRAM_CHAT_ID_APPLE": ""}) is None


def _telegram_falso(monkeypatch, atualizacoes, env=None, ok=True, descricao=""):
    monkeypatch.setattr(painel, "ler_env", lambda: {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "",
                                                    "TELEGRAM_CHAT_ID_APPLE": "", **(env or {})})

    class Resp:
        def json(self):
            return {"ok": ok, "result": atualizacoes, "description": descricao}
    monkeypatch.setattr(painel.requests, "get", lambda *a, **k: Resp())


def test_detectar_classifica_cada_chat_pelo_nome(monkeypatch):
    _telegram_falso(monkeypatch, [
        {"channel_post": {"chat": {"id": GERAL, "type": "channel", "title": "Caçador Promo"}}},
        {"message": {"chat": {"id": APPLE, "type": "supergroup", "title": "Promoções Apple"}}},
        {"message": {"chat": {"id": 55, "type": "private", "first_name": "Bernardo"}}},
    ], env={"TELEGRAM_CHAT_ID": str(GERAL)})
    r = painel.detectar_ids()
    por_id = {c["id"]: c for c in r["canais"]}
    assert (por_id[GERAL]["sugestao"], por_id[GERAL]["atual"], por_id[GERAL]["tipo"]) == ("geral", "geral", "canal")
    assert (por_id[APPLE]["sugestao"], por_id[APPLE]["atual"], por_id[APPLE]["tipo"]) == ("apple", None, "grupo")
    assert r["pessoas"] == [{"id": 55, "nome": "Bernardo"}] and not r["vazio"]


def test_detectar_enxerga_grupo_onde_o_bot_acabou_de_entrar(monkeypatch):
    """Só o aviso 'my_chat_member' (bot adicionado ao grupo), sem nenhuma mensagem ainda."""
    _telegram_falso(monkeypatch, [{"my_chat_member": {"chat": {"id": APPLE, "type": "supergroup", "title": "Grupo Apple"}}}])
    r = painel.detectar_ids()
    assert [(c["id"], c["sugestao"]) for c in r["canais"]] == [(APPLE, "apple")]


def test_detectar_canal_encaminhado(monkeypatch):
    _telegram_falso(monkeypatch, [{"message": {"chat": {"id": 55, "type": "private", "first_name": "B"},
                                               "forward_from_chat": {"id": GERAL, "type": "channel", "title": "Caçador Promo"}}}])
    assert [c["id"] for c in painel.detectar_ids()["canais"]] == [GERAL]


def test_detectar_sem_token(monkeypatch):
    monkeypatch.setattr(painel, "ler_env", lambda: {"TELEGRAM_BOT_TOKEN": ""})
    assert "token" in painel.detectar_ids()["erro"].lower()


def test_detectar_com_o_bot_ligado_explica_o_que_fazer(monkeypatch):
    _telegram_falso(monkeypatch, [], ok=False, descricao="Conflict: terminated by other getUpdates request")
    erro = painel.detectar_ids()["erro"]
    assert "Desligue o bot" in erro and "Conflict" not in erro


# ── gravação: o mesmo ID nunca vai para os dois campos ───────────────

@pytest.fixture
def servidor(monkeypatch, tmp_path):
    monkeypatch.setattr(painel, "ENV_PATH", tmp_path / ".env")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), painel.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path / ".env"
    srv.shutdown()


def _salvar(base, campos):
    req = urllib.request.Request(base + "/api/config", data=json.dumps(campos).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req).read())


def test_painel_recusa_o_mesmo_id_nos_dois_campos(servidor):
    base, env = servidor
    r = _salvar(base, {"TELEGRAM_CHAT_ID": str(GERAL), "TELEGRAM_CHAT_ID_APPLE": str(GERAL)})
    assert "MESMO ID" in r["erro"]
    assert not env.exists() or str(GERAL) not in env.read_text(encoding="utf-8")     # nada foi gravado


def test_painel_grava_ids_diferentes(servidor):
    base, env = servidor
    assert _salvar(base, {"TELEGRAM_CHAT_ID": str(GERAL), "TELEGRAM_CHAT_ID_APPLE": str(APPLE)}) == {"ok": True}
    texto = env.read_text(encoding="utf-8")
    assert f"TELEGRAM_CHAT_ID={GERAL}" in texto and f"TELEGRAM_CHAT_ID_APPLE={APPLE}" in texto


def test_painel_aceita_grupo_apple_vazio(servidor):
    base, _ = servidor
    assert _salvar(base, {"TELEGRAM_CHAT_ID": str(GERAL), "TELEGRAM_CHAT_ID_APPLE": ""}) == {"ok": True}


def test_check_avisa_quando_os_dois_ids_sao_iguais(monkeypatch):
    monkeypatch.setattr(cfg.config, "chat_id", str(GERAL))
    monkeypatch.setattr(cfg.config, "chat_id_apple", str(GERAL))
    assert any("IGUAL" in p for p in cfg.verificar())
    monkeypatch.setattr(cfg.config, "chat_id_apple", str(APPLE))
    assert not any("IGUAL" in p for p in cfg.verificar())
