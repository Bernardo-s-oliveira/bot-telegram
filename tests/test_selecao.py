from ofertas import selecao
from ofertas.db import Historico
from ofertas.models import Oferta


def oferta(preco=100.0, original=None, nota=4.8, vendas=5000, avaliacoes=None, mensal=False,
           titulo="Produto Teste", plataforma="mercadolivre", id_produto="1"):
    return Oferta(plataforma=plataforma, id_produto=id_produto, titulo=titulo, url_afiliado="x",
                  preco=preco, preco_original=original, nota=nota, vendas=vendas,
                  avaliacoes=avaliacoes, vendas_mensal=mensal)


def historico(referencia=100.0, minimo=None, amostras=10, dias=15.0):
    return Historico(minimo=minimo or referencia, referencia=referencia, amostras=amostras, dias=dias)


# ── portões de qualidade ─────────────────────────────────────────────

def test_sem_avaliacao_e_rejeitada():
    assert selecao.avaliar(oferta(nota=None, original=200), None) == "sem avaliação"


def test_sem_avaliacao_passa_se_exigencia_desligada(monkeypatch):
    monkeypatch.setattr(selecao.config, "exigir_avaliacao", False)
    assert selecao.avaliar(oferta(nota=None, original=200), None) is None


def test_nota_baixa_e_rejeitada():
    assert selecao.avaliar(oferta(nota=4.0, original=200), None).startswith("nota baixa")


def test_sem_preco_e_rejeitada():
    assert selecao.avaliar(oferta(preco=None), None) == "sem preço"


# ── faixas ───────────────────────────────────────────────────────────

def test_campeao_barato_com_desconto_modesto():
    o = oferta(preco=90.0, original=100.0, vendas=50_000, nota=4.8)   # -10%
    assert selecao.avaliar(o, None) is None
    assert o.faixa == "campeao"


def test_desconto_grande_sem_ser_campeao():
    o = oferta(preco=50.0, original=100.0, vendas=100, nota=4.4)      # -50%, poucas vendas
    assert selecao.avaliar(o, None) is None
    assert o.faixa == "desconto"


def test_desconto_grande_sem_prova_social_e_rejeitado():
    o = oferta(preco=50.0, original=100.0, vendas=5, nota=4.9)
    assert selecao.avaliar(o, None) == "critérios não atingidos"


def test_muito_vendido_sem_promocao_nao_e_oferta():
    o = oferta(preco=100.0, original=None, vendas=100_000, nota=4.9)
    assert selecao.avaliar(o, None) == "critérios não atingidos"


def test_amazon_converte_compras_do_mes_para_total():
    # 200 compras/mês × 6 = 1200 ≥ 1000 -> campeão; 100/mês × 6 = 600 -> não
    ok = oferta(preco=90.0, original=100.0, vendas=200, mensal=True, nota=4.7)
    assert selecao.avaliar(ok, None) is None and ok.faixa == "campeao"
    fraco = oferta(preco=90.0, original=100.0, vendas=100, mensal=True, nota=4.7)
    assert selecao.avaliar(fraco, None) == "critérios não atingidos"


# ── validação contra o histórico de preços ───────────────────────────

def test_desconto_inflado_e_rejeitado():
    # anuncia -50% (de R$ 200), mas o produto sempre custou ~R$ 100
    o = oferta(preco=100.0, original=200.0)
    motivo = selecao.avaliar(o, historico(referencia=100.0))
    assert motivo.startswith("desconto inflado")


def test_desconto_inflado_passa_se_checagem_desligada(monkeypatch):
    monkeypatch.setattr(selecao.config, "rejeitar_desconto_falso", False)
    o = oferta(preco=100.0, original=200.0, vendas=100, nota=4.4)
    # com a checagem desligada o critério vira o efetivo (0%) e cai fora por não ter desconto real
    assert selecao.avaliar(o, historico(referencia=100.0)) == "critérios não atingidos"


def test_desconto_real_e_confirmado_e_ganha_selos():
    # preço normal R$ 100, agora R$ 70 (-30%), menor dos últimos dias
    o = oferta(preco=70.0, original=100.0)
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=70.0, dias=20)) is None
    assert any("30% abaixo do preço médio" in s for s in o.selos)
    assert any("Menor preço" in s for s in o.selos)


def test_desconto_anunciado_exagerado_e_corrigido_para_o_real():
    # anuncia -50% (de R$ 200); o normal era R$ 100 e agora R$ 70: o real é -30%
    o = oferta(preco=70.0, original=200.0)
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=70.0)) is None
    assert o.preco_original == 100.0 and o.desconto == 30


def test_queda_verificada_sem_desconto_anunciado():
    # a Amazon não mostrou preço "de", mas o histórico prova -30%
    o = oferta(preco=70.0, original=None)
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=70.0)) is None
    assert o.desconto == 30


def test_preco_acima_do_normal_nao_e_oferta():
    sem_desconto = oferta(preco=120.0, original=None)
    assert selecao.avaliar(sem_desconto, historico(referencia=100.0)) == "critérios não atingidos"
    # e se anuncia desconto estando acima do normal, é desconto inflado
    anunciando = oferta(preco=120.0, original=150.0)
    assert selecao.avaliar(anunciando, historico(referencia=100.0)).startswith("desconto inflado")


def test_historico_curto_nao_valida_nem_bloqueia():
    o = oferta(preco=100.0, original=200.0)
    curto = historico(referencia=100.0, amostras=2, dias=0.2)
    assert selecao.avaliar(o, curto) is None      # aceita o desconto anunciado (ainda sem como checar)
    assert o.selos == []


# ── ranking ──────────────────────────────────────────────────────────

def test_mais_vendido_e_mais_barato_pontuam_mais():
    mais_vendido = oferta(preco=80.0, original=100.0, vendas=100_000)
    menos_vendido = oferta(preco=80.0, original=100.0, vendas=1_000)
    selecao.avaliar(mais_vendido, None)
    selecao.avaliar(menos_vendido, None)
    assert mais_vendido.score > menos_vendido.score

    barato = oferta(preco=50.0, original=62.5, vendas=10_000)
    caro = oferta(preco=1500.0, original=1875.0, vendas=10_000)
    selecao.avaliar(barato, None)
    selecao.avaliar(caro, None)
    assert barato.score > caro.score


def test_nota_com_poucas_avaliacoes_pesa_menos():
    consolidada = oferta(nota=4.7, avaliacoes=5000, vendas=None, original=140.0)
    novata = oferta(nota=5.0, avaliacoes=25, vendas=None, original=140.0)
    assert selecao.avaliar(consolidada, None) is None
    assert selecao.avaliar(novata, None) is None
    assert consolidada.score > novata.score
