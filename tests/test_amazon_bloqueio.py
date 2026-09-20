"""Amazon: detecção de bloqueio (sem falso positivo), motivo no log, página guardada e pausa depois do bloqueio."""
import logging
import time

import pytest
from test_apple import CARD_AMAZON_OK, RespostaFalsa, SessaoFalsa

from ofertas.sources import amazon

PAGINA_CAPTCHA = "<html><head><title>Amazon.com.br</title></head><body>Digite os caracteres que você vê: captcha</body></html>"
PAGINA_ROBOT = "<html><head><title>Robot Check</title></head><body>Sorry</body></html>"
PAGINA_INTERSTICIO = "<html><head><title>&nbsp;</title></head><body></body></html>"
# página NORMAL de resultados que menciona "captcha" logo no início (script de proteção): não é bloqueio
PAGINA_NORMAL_COM_CAPTCHA_NO_HEAD = ('<html><head><title>Amazon.com.br : apple iphone</title><script>var captchaEnabled = false;'
                                     '</script></head><body><div data-component-type="s-search-result" data-asin="B0ABC12345"></div></body></html>')


@pytest.fixture
def sem_pausas(monkeypatch):
    monkeypatch.setattr(amazon.time, "sleep", lambda s: None)


@pytest.fixture
def dados_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(amazon, "DATA_DIR", tmp_path)
    return tmp_path


# ── o que é (e o que não é) bloqueio ─────────────────────────────────

def test_pagina_com_resultados_nunca_e_bloqueio_mesmo_com_captcha_no_head():
    """O falso positivo que o detector antigo tinha: 'captcha' nos primeiros 5000 caracteres de uma página boa."""
    assert amazon._motivo_bloqueio(PAGINA_NORMAL_COM_CAPTCHA_NO_HEAD) is None
    assert amazon._bloqueado(PAGINA_NORMAL_COM_CAPTCHA_NO_HEAD) is False


def test_captcha_de_verdade_e_bloqueio_e_diz_o_motivo():
    assert "captcha" in amazon._motivo_bloqueio(PAGINA_CAPTCHA)
    assert "robot check" in amazon._motivo_bloqueio(PAGINA_ROBOT)


def test_interstício_em_branco_e_bloqueio():
    assert "interstício" in amazon._motivo_bloqueio(PAGINA_INTERSTICIO)


def test_pagina_normal_de_resultados_nao_e_bloqueio():
    assert amazon._motivo_bloqueio(CARD_AMAZON_OK) is None


def test_busca_sem_resultados_mas_com_titulo_normal_nao_e_bloqueio():
    assert amazon._motivo_bloqueio("<html><head><title>Amazon.com.br : xyzzy</title></head><body>Nenhum resultado</body></html>") is None


# ── registro do bloqueio ─────────────────────────────────────────────

def test_bloqueio_loga_o_motivo_guarda_a_pagina_e_pausa(monkeypatch, dados_tmp, caplog):
    monkeypatch.setattr(amazon.config, "fonte_amazon", {"ativa": True, "pausa_apos_bloqueio_minutos": 45})
    antes = time.time()
    with caplog.at_level(logging.WARNING, logger="ofertas.amazon"):
        amazon._registrar_bloqueio("busca 'apple iphone'", "verificação de robô/captcha ('captcha')", RespostaFalsa(200, PAGINA_CAPTCHA))
    assert (dados_tmp / "debug_amazon_bloqueio.html").read_text(encoding="utf-8") == PAGINA_CAPTCHA
    assert 44 * 60 < amazon._pausa_ate - antes <= 45 * 60 + 1
    msg = caplog.text
    assert "apple iphone" in msg and "captcha" in msg and "HTTP 200" in msg and "45 min" in msg and "debug_amazon_bloqueio.html" in msg


def test_pausa_zero_desliga_a_espera(monkeypatch, dados_tmp):
    monkeypatch.setattr(amazon.config, "fonte_amazon", {"ativa": True, "pausa_apos_bloqueio_minutos": 0})
    amazon._registrar_bloqueio("x", "captcha", RespostaFalsa(200, PAGINA_CAPTCHA))
    assert amazon._pausa_ate == 0.0


# ── a raspagem para, espera e volta ──────────────────────────────────

TAREFA = [{"rotulo": "busca 'apple iphone'", "params": {"k": "apple iphone"}}]


def test_bloqueio_para_o_ciclo_e_poe_a_amazon_em_pausa(monkeypatch, dados_tmp, sem_pausas):
    monkeypatch.setattr(amazon.config, "fonte_amazon", {"ativa": True, "paginas": 1, "pausa_apos_bloqueio_minutos": 60})
    sessao = SessaoFalsa(RespostaFalsa(200, PAGINA_CAPTCHA))
    monkeypatch.setattr(amazon, "_sessao", lambda: sessao)
    assert amazon._buscar_por_scraping(TAREFA) == []
    assert amazon._pausa_ate > time.time()


def test_durante_a_pausa_nem_pede_nada_a_amazon(monkeypatch, sem_pausas, caplog):
    monkeypatch.setattr(amazon, "_pausa_ate", time.time() + 30 * 60)

    def proibida():
        raise AssertionError("não deveria falar com a Amazon durante a pausa")
    monkeypatch.setattr(amazon, "_sessao", proibida)
    with caplog.at_level(logging.INFO, logger="ofertas.amazon"):
        assert amazon._buscar_por_scraping(TAREFA) == []
        assert amazon.buscar_termos(["apple iphone", "apple ipad"], 2) == []          # a busca de termos também respeita
    assert "em pausa" in caplog.text and "30 min" in caplog.text


def test_terminada_a_pausa_a_amazon_volta_a_ser_consultada(monkeypatch, sem_pausas):
    monkeypatch.setattr(amazon, "_pausa_ate", time.time() - 1)
    monkeypatch.setattr(amazon.config, "fonte_amazon", {"ativa": True, "paginas": 1})
    monkeypatch.setattr(amazon.config, "amazon_tag", "tag-20")
    monkeypatch.setattr(amazon, "_sessao", lambda: SessaoFalsa(RespostaFalsa(200, CARD_AMAZON_OK)))
    assert [o.id_produto for o in amazon._buscar_por_scraping(TAREFA)] == ["B0ABC12345"]


def test_resposta_com_captcha_e_resultados_de_verdade_nao_para_o_ciclo(monkeypatch, sem_pausas):
    monkeypatch.setattr(amazon.config, "fonte_amazon", {"ativa": True, "paginas": 1})
    monkeypatch.setattr(amazon.config, "amazon_tag", "tag-20")
    monkeypatch.setattr(amazon, "_sessao", lambda: SessaoFalsa(RespostaFalsa(200, PAGINA_NORMAL_COM_CAPTCHA_NO_HEAD)))
    amazon._buscar_por_scraping(TAREFA)
    assert amazon._pausa_ate == 0.0                                    # o detector antigo bloquearia esta página


def test_dois_503_seguidos_tambem_contam_como_bloqueio(monkeypatch, dados_tmp, sem_pausas, caplog):
    monkeypatch.setattr(amazon.config, "fonte_amazon", {"ativa": True, "paginas": 1, "pausa_apos_bloqueio_minutos": 60})
    monkeypatch.setattr(amazon, "_sessao", lambda: SessaoFalsa(RespostaFalsa(503), RespostaFalsa(503)))
    with caplog.at_level(logging.WARNING, logger="ofertas.amazon"):
        amazon._buscar_por_scraping(TAREFA)
    assert "resposta HTTP 503" in caplog.text and amazon._pausa_ate > time.time()


# ── o desafio anti-robô real (página capturada em 2026-09-20, token abreviado) ──

PAGINA_DESAFIO_BM_VERIFY = ('<!DOCTYPE html><html><head><meta charset="utf-8">'
                            '<meta http-equiv="refresh" content="5; URL=\'/s?k=apple+watch&bm-verify=AAQAAAAO\'" />'
                            '<title>&nbsp;</title><script> var i = 1789922000; var j = i + Number("6656" + "72470"); </script></head>'
                            '<body><script>xhr.open("POST", "/_sec/verify?provider=interstitial", false);</script></body></html>')


def test_desafio_bm_verify_e_reconhecido_com_motivo_claro():
    assert "bm-verify" in amazon._motivo_bloqueio(PAGINA_DESAFIO_BM_VERIFY)
    assert "anti-robô" in amazon._motivo_bloqueio(PAGINA_DESAFIO_BM_VERIFY)


def test_desafio_ao_vivo_pausa_a_amazon_em_vez_de_insistir(monkeypatch, sem_pausas, caplog):
    monkeypatch.setattr(amazon.config, "fonte_amazon", {"ativa": True, "paginas": 1, "pausa_apos_bloqueio_minutos": 60})
    sessao = SessaoFalsa(RespostaFalsa(200, PAGINA_DESAFIO_BM_VERIFY))
    monkeypatch.setattr(amazon, "_sessao", lambda: sessao)
    with caplog.at_level(logging.WARNING, logger="ofertas.amazon"):
        assert amazon._buscar_por_scraping(TAREFA) == []
    assert sessao.pedidos == 1                                        # um pedido só: não fica insistindo no desafio
    assert "bm-verify" in caplog.text and amazon._pausa_ate > time.time()


def test_a_pagina_de_depuracao_vai_para_a_pasta_do_teste_e_nao_para_data(tmp_path):
    assert amazon.DATA_DIR == tmp_path
