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
    assert selecao.avaliar(oferta(nota=None), None) == "sem avaliação"


def test_sem_avaliacao_so_passa_pela_queda_comprovada_se_exigencia_desligada(monkeypatch):
    monkeypatch.setattr(selecao.config, "exigir_avaliacao", False)
    assert selecao.avaliar(oferta(nota=None), None) == "critérios não atingidos"     # campeão exige nota
    o = oferta(preco=70.0, nota=None)
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=70.0)) is None      # queda de 30% comprovada
    assert o.faixa == "desconto"


def test_nota_baixa_e_rejeitada():
    assert selecao.avaliar(oferta(nota=4.0), None).startswith("nota baixa")


def test_sem_preco_e_rejeitada():
    assert selecao.avaliar(oferta(preco=None), None) == "sem preço"


# ── o desconto que a loja anuncia não conta ──────────────────────────

def test_campeao_nao_precisa_de_desconto():
    o = oferta(preco=90.0, original=None, vendas=50_000, nota=4.8)      # preço cheio, sem "De"
    assert selecao.avaliar(o, None) is None
    assert o.faixa == "campeao"


def test_desconto_anunciado_nao_muda_o_score_nem_entra_no_post():
    sem = oferta(preco=90.0, original=None)
    com = oferta(preco=90.0, original=400.0)                             # loja anuncia -78%
    assert selecao.avaliar(sem, None) is None and selecao.avaliar(com, None) is None
    assert com.score == sem.score
    assert com.desconto_verificado is False and com.selos == []


def test_desconto_anunciado_sozinho_nao_qualifica_a_faixa_de_queda():
    # poucas vendas: só a queda de preço poderia qualificar, e não há histórico que a comprove
    o = oferta(preco=50.0, original=100.0, vendas=100, nota=4.4)
    assert selecao.avaliar(o, None) == "critérios não atingidos"


def test_queda_comprovada_qualifica_a_faixa_de_queda():
    o = oferta(preco=70.0, original=100.0, vendas=100, nota=4.4)         # -30% no histórico, poucas vendas
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=70.0)) is None
    assert o.faixa == "desconto"


def test_queda_comprovada_sem_prova_social_e_rejeitada():
    o = oferta(preco=50.0, vendas=5, nota=4.9)
    assert selecao.avaliar(o, historico(referencia=100.0)) == "critérios não atingidos"


def test_queda_abaixo_do_minimo_da_faixa_nao_qualifica_a_faixa_de_queda():
    o = oferta(preco=85.0, vendas=100, nota=4.4)                         # -15%: abaixo dos 25% da faixa
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=85.0)) == "critérios não atingidos"


def test_amazon_converte_compras_do_mes_para_total():
    # 200 compras/mês × 6 = 1200 ≥ 1000 -> campeão; 100/mês × 6 = 600 -> não
    ok = oferta(preco=90.0, vendas=200, mensal=True, nota=4.7)
    assert selecao.avaliar(ok, None) is None and ok.faixa == "campeao"
    fraco = oferta(preco=90.0, vendas=100, mensal=True, nota=4.7)
    assert selecao.avaliar(fraco, None) == "critérios não atingidos"


# ── preço contra o histórico ─────────────────────────────────────────

def test_desconto_real_e_confirmado_e_ganha_selos():
    # preço normal R$ 100, agora R$ 70 (-30%), e já chegou a custar mais: é o menor preço dos últimos dias
    o = oferta(preco=70.0)
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=70.0, dias=20)) is None
    assert o.desconto_verificado is True and o.preco_original == 100.0 and o.desconto == 30
    assert any("Caiu 30%" in s for s in o.selos)
    assert any("Menor preço" in s for s in o.selos)


def test_o_de_e_o_percentual_do_post_vem_do_historico_nunca_da_loja():
    # a loja anuncia -65% (de R$ 200); o normal era R$ 100 e agora R$ 70: o post mostra o real, -30%
    o = oferta(preco=70.0, original=200.0)
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=70.0)) is None
    assert o.preco_original == 100.0 and o.desconto == 30


def test_queda_pequena_nao_vira_percentual_no_post():
    o = oferta(preco=95.0)                                               # -5%: aprovado como campeão
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=95.0)) is None
    assert o.desconto_verificado is False and o.desconto_pct is None
    assert not any("Caiu" in s for s in o.selos)                         # sem percentual (o selo de menor preço é outro fato)


def test_produto_de_preco_fixo_nao_ganha_selo_de_menor_preco():
    o = oferta(preco=100.0)
    assert selecao.avaliar(o, historico(referencia=100.0, minimo=100.0, dias=20)) is None
    assert o.selos == []


def test_preco_acima_do_normal_nao_e_oferta():
    assert selecao.avaliar(oferta(preco=120.0), historico(referencia=100.0)).startswith("preço acima do normal")
    assert selecao.avaliar(oferta(preco=104.0), historico(referencia=100.0)) is None    # até 5% acima passa


def test_historico_curto_nao_valida_nem_bloqueia():
    o = oferta(preco=100.0, original=200.0)
    curto = historico(referencia=50.0, amostras=2, dias=0.2)             # nem olha para ele
    assert selecao.avaliar(o, curto) is None
    assert o.desconto_verificado is False and o.selos == []


# ── comparação com anúncios iguais ───────────────────────────────────

def test_mais_caro_que_anuncios_iguais_e_rejeitado():
    caro = oferta(preco=120.0)
    caro.preco_mercado = 100.0
    assert selecao.avaliar(caro, None).startswith("mais caro que anúncios iguais")


def test_o_mais_em_conta_entre_os_iguais_passa():
    barato = oferta(preco=95.0)
    barato.preco_mercado = 100.0
    assert selecao.avaliar(barato, None) is None
    igual = oferta(preco=108.0)
    igual.preco_mercado = 100.0
    assert selecao.avaliar(igual, None) is None                          # até 10% acima da mediana passa


# ── ranking ──────────────────────────────────────────────────────────

def test_mais_vendido_e_mais_barato_pontuam_mais():
    mais_vendido = oferta(preco=80.0, vendas=100_000)
    menos_vendido = oferta(preco=80.0, vendas=1_000)
    selecao.avaliar(mais_vendido, None)
    selecao.avaliar(menos_vendido, None)
    assert mais_vendido.score > menos_vendido.score

    barato = oferta(preco=50.0, vendas=10_000)
    caro = oferta(preco=1500.0, vendas=10_000)
    selecao.avaliar(barato, None)
    selecao.avaliar(caro, None)
    assert barato.score > caro.score


def test_nota_com_poucas_avaliacoes_pesa_menos():
    consolidada = oferta(nota=4.7, avaliacoes=5000, vendas=2000)
    novata = oferta(nota=5.0, avaliacoes=25, vendas=2000)
    assert selecao.avaliar(consolidada, None) is None
    assert selecao.avaliar(novata, None) is None
    assert consolidada.score > novata.score


def test_queda_comprovada_maior_pontua_mais():
    pequena, grande = oferta(preco=88.0), oferta(preco=60.0)
    selecao.avaliar(pequena, historico(referencia=100.0, minimo=88.0))
    selecao.avaliar(grande, historico(referencia=100.0, minimo=60.0))
    assert grande.score > pequena.score
