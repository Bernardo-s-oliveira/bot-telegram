"""Cupons do Mercado Livre: só se anuncia o que vale para 1 unidade do produto."""
import json

from ofertas import pipeline
from ofertas.formatter import montar_caption
from ofertas.models import Oferta
from ofertas.sources import mercadolivre as ml


def oferta(preco=63.90, **kw):
    return Oferta("mercadolivre", "MLB1", "Protetor Solar", "x", url_produto="https://ml/1", preco=preco,
                  nota=4.9, vendas=100_000, **kw)


def cupom(label, status="unredeemed", tipo="COUPON_MIN_PURCHASE_REACHED", campanha="1"):
    return {"label": label, "status": status, "campanha": campanha, "tipo": tipo}


# ── cupons REAIS lidos das páginas de produto do ML em 2026-09-19 ────────────────────────

REAL_PRECO_FINAL = ('{"label":"R$ 106,32 com Cupom","status":"unredeemed","amount":0,"campaign_id":"14167118",'
                    '"type":"INACTIVE_COUPON_NOT_APPLIED","scarcity":"N\\u002FA"}')            # TP-Link Tapo C200, R$ 122,32
REAL_MINIMO_ACIMA = ('{"label":"Compre R$ 79,99 e ganhe 20% OFF","status":"unredeemed","amount_type":"percentage",'
                     '"amount":20,"campaign_id":"13566431","type":"COUPON_NOT_MIN_PURCHASE_AMOUNT","scarcity":"N\\u002FA"}')
REAL_SEGUIR_LOJA = ('{"label":"Compre R$ 200 e ganhe 5% OFF por seguir a loja","status":"unredeemed",'
                    '"amount_type":"percentage","amount":5,"campaign_id":"13659293",'
                    '"type":"COUPON_NOT_MIN_PURCHASE_AMOUNT","scarcity":"N\\u002FA"}')
REAL_ATIVADO_MINIMO = ('{"label":"Compre R$ 150 e ganhe R$ 8 OFF","status":"redeemed","amount":0,'
                       '"campaign_id":"14150538","type":"COUPON_NOT_MIN_PURCHASE_AMOUNT","scarcity":"N\\u002FA"}')   # tênis R$ 146,90


def lista(*objetos):
    return ml.parse_cupons(f'"coupons":{{"coupons":[{",".join(objetos)}]}}')


def test_cupom_real_com_preco_final_e_anunciado():
    # o card e a página dizem "R$ 106,32 com Cupom" para um produto de R$ 122,32
    assert ml.texto_cupom(oferta(122.32), lista(REAL_PRECO_FINAL)) == "🎟 R$ 106,32 com cupom (ative na página do produto)"


def test_cupom_real_com_compra_minima_acima_do_preco_nao_e_anunciado():
    # o pill do card diz "20% OFF com Cupom", mas o item custa R$ 63,90 e o mínimo é R$ 79,99
    assert ml.texto_cupom(oferta(63.90), lista(REAL_MINIMO_ACIMA)) is None


def test_cupom_real_condicionado_a_seguir_a_loja_nao_e_anunciado():
    assert ml.texto_cupom(oferta(250.0), lista(REAL_SEGUIR_LOJA)) is None


def test_cupom_real_com_minimo_logo_acima_do_preco_nao_e_anunciado():
    assert ml.texto_cupom(oferta(146.90), lista(REAL_ATIVADO_MINIMO)) is None       # R$ 8 OFF pedem R$ 150


def test_parse_cupons_le_os_dois_formatos_e_ignora_repeticao_da_campanha():
    html = f'"coupons":{{"coupons":[{REAL_PRECO_FINAL},{REAL_MINIMO_ACIMA}]}} ... "coupons":{{"coupons":[{REAL_PRECO_FINAL}]}}'
    assert [c["campanha"] for c in ml.parse_cupons(html)] == ["14167118", "13566431"]


def test_sem_cupons_na_pagina():
    assert ml.parse_cupons("<html>nada</html>") == []
    assert ml.texto_cupom(oferta(), []) is None


# ── formatos aplicáveis sem exemplo real ativo (dados sintéticos) ────────────────────────

def test_cupom_percentual_com_minimo_atingido():
    t = ml.texto_cupom(oferta(90.0), [cupom("Compre R$ 79,99 e ganhe 20% OFF")])
    assert t == "🎟 Cupom de 20% OFF em compras a partir de R$ 79,99 (ative na página do produto)"


def test_cupom_percentual_sem_minimo():
    assert ml.texto_cupom(oferta(90.0), [cupom("Ganhe 10% OFF")]) == "🎟 Cupom de 10% OFF (ative na página do produto)"


def test_cupom_de_valor_fixo_mostra_preco_final():
    t = ml.texto_cupom(oferta(150.0), [cupom("Compre R$ 100 e ganhe R$ 20 OFF")])
    assert t == "🎟 Cupom de R$ 20,00 OFF → R$ 130,00 (ative na página do produto)"


def test_preco_abaixo_do_minimo_nao_ganha_cupom():
    assert ml.texto_cupom(oferta(50.0), [cupom("Compre R$ 79,99 e ganhe 20% OFF")]) is None


def test_status_e_da_conta_do_bot_ativado_tambem_vale():
    assert ml.texto_cupom(oferta(90.0), [cupom("Ganhe 10% OFF", status="redeemed")]) is not None


def test_status_desconhecido_nao_e_anunciado():
    assert ml.texto_cupom(oferta(90.0), [cupom("Ganhe 10% OFF", status="expired")]) is None


def test_preco_com_cupom_que_nao_e_menor_que_o_preco_e_ignorado():
    assert ml.texto_cupom(oferta(100.0), [cupom("R$ 100,00 com Cupom")]) is None
    assert ml.texto_cupom(oferta(100.0), [cupom("R$ 120,00 com Cupom")]) is None


def test_escolhe_o_cupom_de_maior_economia():
    t = ml.texto_cupom(oferta(200.0), [cupom("Ganhe 5% OFF", campanha="1"), cupom("Ganhe 15% OFF", campanha="2")])
    assert "15% OFF" in t


# ── post e pipeline ───────────────────────────────────────────────────

def test_post_mostra_a_linha_do_cupom():
    o = oferta(90.0, cupom="🎟 Cupom de 10% OFF (ative na página do produto)")
    assert "🎟 Cupom de 10% OFF (ative na página do produto)" in montar_caption(o)


def _air_fryer():
    return Oferta("mercadolivre", "7", "Air Fryer Marca Boa Gama", "x", url_produto="https://ml/7", preco=200.0,
                  preco_original=280.0, nota=4.9, vendas=50_000)   # -29%, sem histórico


def test_so_cupons_ligado_le_a_pagina_sem_reprovar_vendedor(monkeypatch):
    """verificar_vendedor desligado + buscar_cupons ligado: abre a página só para o cupom."""
    monkeypatch.setattr(pipeline.config, "verificar_vendedor", False)
    visitadas = []

    def verificar(ofertas):
        for o in ofertas:
            visitadas.append(o.id_produto)
            o.vendedor_checado, o.vendedor_nivel, o.cupom = True, 1, "🎟 Cupom de 10% OFF (ative na página do produto)"
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", verificar)
    escolhidas, _ = pipeline.selecionar([_air_fryer()], 1)
    assert visitadas == ["7"] and [x.id_produto for x in escolhidas] == ["7"]   # nível 1 não reprova: checagem desligada
    assert escolhidas[0].cupom


def test_ambos_desligados_nao_abre_o_navegador(monkeypatch):
    monkeypatch.setattr(pipeline.config, "verificar_vendedor", False)
    monkeypatch.setattr(pipeline.config, "buscar_cupons", False)

    def nao_deveria_abrir(_):
        raise AssertionError("não deveria abrir o navegador")
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", nao_deveria_abrir)
    assert len(pipeline.selecionar([_air_fryer()], 1)[0]) == 1
