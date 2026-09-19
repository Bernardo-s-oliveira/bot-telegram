"""Descontos suspeitos: "De" inflado, preço errado e vendedor duvidoso."""
import datetime as dt

from test_selecao import historico, oferta

from ofertas import db, pipeline, selecao
from ofertas.formatter import montar_caption
from ofertas.models import Oferta
from ofertas.sources import mercadolivre


# ── o caso real: De R$ 399,70 por R$ 102,66 (-74%), mas o produto sempre custou ~R$ 130 ──

def chuveiro(**kw):
    base = dict(preco=102.66, original=399.70, nota=4.8, vendas=5000, titulo="Chuveiro Lorenzetti Advanced 7500W")
    return oferta(**{**base, **kw})


def test_desconto_74_sem_historico_espera_o_historico():
    assert selecao.avaliar(chuveiro(), None).startswith("desconto alto sem histórico")


def test_desconto_74_com_historico_de_preco_estavel_e_inflado():
    motivo = selecao.avaliar(chuveiro(), historico(referencia=130.0, minimo=125.0))
    assert motivo.startswith("desconto inflado") and "real -21%" in motivo


def test_desconto_74_sem_historico_mas_com_anuncios_iguais_e_inflado():
    o = chuveiro()
    o.preco_mercado = 130.0    # outros anúncios do mesmo chuveiro custam ~R$ 130
    motivo = selecao.avaliar(o, None)
    assert motivo.startswith("desconto inflado") and "anúncios iguais" in motivo


def test_preco_muito_abaixo_dos_anuncios_iguais_e_rejeitado():
    o = oferta(preco=50.0, original=None)
    o.preco_mercado = 130.0
    assert selecao.avaliar(o, None).startswith("preço muito abaixo de anúncios iguais")


def test_sem_historico_nao_posta_desconto_acima_do_limite_mas_posta_no_limite():
    assert selecao.avaliar(oferta(preco=50.0, original=100.0), None) is None                        # -50%
    assert selecao.avaliar(oferta(preco=45.0, original=100.0), None).startswith("desconto alto")    # -55%


# ── desconto real e muito alto: exige vendedor confiável ou prova forte ──

def queda_real_de_70(**kw):
    """Queda de 70% registrada no histórico (preço normal R$ 300, agora R$ 90)."""
    return oferta(**{**dict(preco=90.0, original=300.0, nota=4.8, vendas=2000), **kw})


def test_desconto_suspeito_ml_passa_provisoriamente_para_a_checagem_do_vendedor():
    o = queda_real_de_70()
    assert selecao.avaliar(o, historico(referencia=300.0, minimo=90.0)) is None
    assert o.suspeita is True


def test_desconto_suspeito_fora_do_ml_exige_prova_forte():
    fraco = queda_real_de_70(plataforma="amazon", vendas=200, mensal=True)                # 200×6 = 1200 vendas
    assert selecao.avaliar(fraco, historico(referencia=300.0)).startswith("desconto suspeito")
    forte = queda_real_de_70(plataforma="amazon", vendas=1500, mensal=True, nota=4.7)     # 9000 vendas
    assert selecao.avaliar(forte, historico(referencia=300.0)) is None


def test_desconto_suspeito_com_checagem_de_vendedor_desligada_exige_prova_forte(monkeypatch):
    monkeypatch.setattr(selecao.config, "verificar_vendedor", False)
    assert selecao.avaliar(queda_real_de_70(vendas=2000), historico(referencia=300.0)).startswith("desconto suspeito")


def test_de_so_e_verificado_com_historico():
    sem = oferta(preco=70.0, original=100.0)
    selecao.avaliar(sem, None)
    assert sem.desconto_verificado is False
    com = oferta(preco=70.0, original=100.0)
    selecao.avaliar(com, historico(referencia=100.0, minimo=70.0))
    assert com.desconto_verificado is True


# ── avaliar_vendedor ─────────────────────────────────────────────────

def vendedor(nivel=5, status="platinum", oficial=False, suspeita=False):
    o = oferta()
    o.vendedor_nivel, o.vendedor_status, o.loja_oficial = nivel, status, oficial
    o.suspeita, o.vendedor_checado = suspeita, True
    return o


def test_vendedor_reputacao_baixa_e_reprovado_mesmo_sem_desconto_suspeito():
    assert selecao.avaliar_vendedor(vendedor(nivel=3)).startswith("vendedor com reputação baixa")


def test_desconto_suspeito_exige_mercadolider_gold_platinum_ou_loja_oficial():
    assert selecao.avaliar_vendedor(vendedor(status="silver", suspeita=True)).startswith("desconto suspeito")
    assert selecao.avaliar_vendedor(vendedor(status=None, suspeita=True)).startswith("desconto suspeito")
    assert selecao.avaliar_vendedor(vendedor(status="gold", suspeita=True)) is None
    assert selecao.avaliar_vendedor(vendedor(status=None, oficial=True, suspeita=True)) is None


def test_vendedor_sem_dados_so_barra_desconto_suspeito():
    assert selecao.avaliar_vendedor(vendedor(nivel=None, status=None, suspeita=True)) == "vendedor não verificado"
    assert selecao.avaliar_vendedor(vendedor(nivel=None, status=None, suspeita=False)) is None


def test_vendedor_confiavel_ganha_selo():
    o = vendedor(status="platinum")
    assert selecao.avaliar_vendedor(o) is None
    assert any("MercadoLíder Platinum" in s for s in o.selos)
    o = vendedor(oficial=True)
    assert selecao.avaliar_vendedor(o) is None
    assert any("Loja oficial" in s for s in o.selos)


# ── post: "De" só quando é real ──────────────────────────────────────

def test_post_nao_apresenta_de_como_fato_sem_historico():
    o = Oferta("mercadolivre", "1", "Chuveiro", "x", preco=102.66, preco_original=399.70, desconto_pct=74)
    texto = montar_caption(o)
    assert "🏷 Loja anuncia -74% (de R$ 399,70)" in texto
    assert "De:" not in texto and "<s>" not in texto


def test_post_mostra_de_e_por_quando_o_historico_comprova():
    o = Oferta("mercadolivre", "1", "Chuveiro", "x", preco=102.66, preco_original=130.0, desconto_verificado=True)
    texto = montar_caption(o)
    assert "❌ De: <s>R$ 130,00</s>" in texto and "💰 Por: <b>R$ 102,66</b>" in texto and "-21%" in texto


# ── leitura do vendedor na página do produto (trechos do bloco de tracking, 2026-09-19) ──

PDP_MERCADOLIDER = ('<html><body><span class="ui-pdp-seller__header__subtitle">+10 mil vendas</span>'
                    '<script>{"event_data":{"seller_id":3054792763,"seller_name":"PRAC Company ltda",'
                    '"reputation_level":"5_green","power_seller_status":"silver","subtitle_types":["MERCADO_LEADER"],'
                    '"installment_info":"12"}}</script></body></html>')
PDP_LOJA_OFICIAL = ('<html><body><script>{"event_data":{"seller_id":510386964,"seller_name":"Casa Dalonso",'
                    '"reputation_level":"5_green","power_seller_status":"platinum","official_store_id":5361,'
                    '"subtitle_types":["SOLD_QUANTITY"]}}</script></body></html>')
PDP_VENDEDOR_NOVO = ('<html><body><script>{"event_data":{"seller_id":99,"seller_name":"Loja Nova",'
                     '"reputation_level":"2_orange","power_seller_status":null}}</script></body></html>')


def test_parse_vendedor_mercadolider_silver():
    assert mercadolivre.parse_vendedor(PDP_MERCADOLIDER) == {
        "vendedor": "PRAC Company ltda", "nivel": 5, "status": "silver", "loja_oficial": False, "vendas": 10_000}


def test_parse_vendedor_loja_oficial():
    d = mercadolivre.parse_vendedor(PDP_LOJA_OFICIAL)
    assert (d["nivel"], d["status"], d["loja_oficial"]) == (5, "platinum", True)


def test_parse_vendedor_reputacao_ruim_e_sem_status():
    d = mercadolivre.parse_vendedor(PDP_VENDEDOR_NOVO)
    assert (d["nivel"], d["status"], d["loja_oficial"]) == (2, None, False)


def test_parse_vendedor_pagina_sem_dados():
    assert mercadolivre.parse_vendedor("<html><body>nada</body></html>") == {}


# ── preço de mercado: mediana de anúncios do MESMO produto ───────────

def anuncio(titulo, preco, id_produto):
    return Oferta("mercadolivre", id_produto, titulo, "x", preco=preco)


def test_preco_de_mercado_e_a_mediana_de_anuncios_iguais():
    ofertas = [anuncio("Chuveiro Lorenzetti Advanced 7500W Branco", p, str(i))
               for i, p in enumerate((100, 130, 140, 150))]
    pipeline.anotar_preco_mercado(ofertas)
    assert ofertas[0].preco_mercado == 140      # mediana de 130, 140 e 150 (os outros três)


def test_anuncios_com_numeros_diferentes_nao_sao_o_mesmo_produto():
    ofertas = [
        anuncio("Chuveiro Lorenzetti Advanced 7500W", 130, "a"),
        anuncio("Chuveiro Lorenzetti Advanced 5500W", 90, "b"),
        anuncio("Chuveiro Lorenzetti Advanced 5500W", 95, "c"),
    ]
    pipeline.anotar_preco_mercado(ofertas)
    assert ofertas[0].preco_mercado is None     # nenhum par com os mesmos números
    assert ofertas[1].preco_mercado is None     # só 1 par (o mínimo é 2)


# ── pipeline.selecionar: checagem de vendedor nas escolhidas ─────────

def ml_com_queda_real(id_produto, titulo, preco):
    """Produto que caiu de R$ 300 para `preco` (histórico gravado), popular e bem avaliado."""
    agora = dt.datetime.now()
    for dias in (11, 8, 5, 2):
        db.registrar_precos([Oferta("mercadolivre", id_produto, titulo, "x", preco=300.0)],
                            agora - dt.timedelta(days=dias))
    return Oferta("mercadolivre", id_produto, titulo, "x", url_produto=f"https://ml/{id_produto}",
                  preco=preco, preco_original=300.0, nota=4.9, vendas=50_000)


def verificador_falso(mapa):
    """Troca a leitura da página do ML: mapa {id_produto: (nivel, status, oficial)}."""
    def verificar(ofertas):
        for o in ofertas:
            o.vendedor_checado = True
            o.vendedor_nivel, o.vendedor_status, o.loja_oficial = mapa[o.id_produto]
    return verificar


def test_vendedor_reprovado_sai_e_a_vaga_vai_para_o_proximo(monkeypatch):
    duvidoso = ml_com_queda_real("1", "Purificador Marca Duvidosa Alfa", preco=90.0)     # -70%, vendedor silver
    seguro = ml_com_queda_real("2", "Air Fryer Marca Confiavel Beta", preco=120.0)       # -60%, vendedor platinum
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores",
                        verificador_falso({"1": (5, "silver", False), "2": (5, "platinum", False)}))
    escolhidas, rejeicoes = pipeline.selecionar([duvidoso, seguro], 2)
    assert [o.id_produto for o in escolhidas] == ["2"]
    assert rejeicoes["desconto suspeito"] == 1


def test_falha_ao_ler_vendedores_barra_so_o_desconto_suspeito(monkeypatch):
    suspeito = ml_com_queda_real("1", "Purificador Marca Duvidosa Alfa", preco=90.0)     # -70%
    normal = ml_com_queda_real("2", "Air Fryer Marca Confiavel Beta", preco=210.0)       # -30%

    def falha(_):
        raise RuntimeError("navegador indisponível")
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", falha)
    escolhidas, _ = pipeline.selecionar([suspeito, normal], 2)
    assert [o.id_produto for o in escolhidas] == ["2"]


def test_checagem_de_vendedor_desligada_nao_abre_o_navegador(monkeypatch):
    monkeypatch.setattr(pipeline.config, "verificar_vendedor", False)

    def nao_deveria_abrir(_):
        raise AssertionError("não deveria abrir o navegador")
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", nao_deveria_abrir)
    normal = ml_com_queda_real("2", "Air Fryer Marca Confiavel Beta", preco=210.0)
    escolhidas, _ = pipeline.selecionar([normal], 1)
    assert len(escolhidas) == 1
