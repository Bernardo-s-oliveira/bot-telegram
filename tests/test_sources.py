"""Parsers das fontes, com trechos de HTML reduzidos dos que ML e Amazon devolvem (2026-09-19)."""
from bs4 import BeautifulSoup

from ofertas.formatter import montar_caption
from ofertas.models import Oferta
from ofertas.sources import amazon, mercadolivre, shopee

CARD_ML = """
<div class="poly-card">
  <a class="poly-component__title" href="https://produto.mercadolivre.com.br/MLB-3456789012-fone-x-_JM#pos">Fone X</a>
  <div class="poly-component__price">
    <s class="andes-money-amount andes-money-amount--previous"><span class="andes-money-amount__fraction">200</span></s>
    <div class="poly-price__current"><span class="andes-money-amount__fraction">150</span></div>
  </div>
  <span class="poly-component__review-compacted">4.9 | +10mil vendidos</span>
</div>"""

CARD_AMAZON = """
<div data-component-type="s-search-result" data-asin="B077C3VFR5">
  <h2><a class="a-link-normal s-no-outline" href="/dp/B077C3VFR5"><span>Fone JBL</span></a></h2>
  <span class="a-price"><span class="a-offscreen">R$ 90,00</span></span>
  <span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">R$ 120,00</span></span>
  <span class="a-icon-alt">4,8 de 5 estrelas</span>
  <span class="a-size-base s-underline-text">(2&nbsp;mil)</span>
  <span>Mais de 2&nbsp;mil compras no mês passado</span>
</div>"""


def test_ml_le_nota_e_vendas_do_card():
    card = BeautifulSoup(CARD_ML, "lxml").select_one("div.poly-card")
    o = mercadolivre._parse_card(card)
    assert (o.nota, o.vendas) == (4.9, 10_000)
    assert (o.preco, o.preco_original) == (150.0, 200.0)
    assert o.id_produto == "MLB3456789012"


def test_ml_card_sem_avaliacao():
    card = BeautifulSoup(CARD_ML.replace("4.9 | +10mil vendidos", ""), "lxml").select_one("div.poly-card")
    # bloco vazio: sem nota nem vendas (a seleção descarta por "sem avaliação")
    assert mercadolivre._parse_review(card.select_one(".poly-component__review-compacted")) == (None, None)


def test_ml_nota_sem_vendas():
    card = BeautifulSoup('<span class="poly-component__review-compacted">4.7</span>', "lxml")
    assert mercadolivre._parse_review(card.select_one("span")) == (4.7, None)


def test_amazon_le_nota_avaliacoes_e_compras_no_mes(monkeypatch):
    monkeypatch.setattr(amazon.config, "amazon_tag", "tag-20")
    card = BeautifulSoup(CARD_AMAZON, "lxml").select_one('div[data-component-type="s-search-result"]')
    o = amazon._card_para_oferta(card)
    assert (o.nota, o.avaliacoes, o.vendas, o.vendas_mensal) == (4.8, 2_000, 2_000, True)
    assert (o.preco, o.preco_original) == (90.0, 120.0)


def test_amazon_popularidade_esta_nas_tarefas_de_busca(monkeypatch):
    monkeypatch.setattr(amazon.config, "fonte_amazon", {"ativa": True})
    tarefas = amazon._tarefas()
    assert any(t["params"].get("s") == amazon.ORDEM_POPULARIDADE for t in tarefas)
    assert any("s" not in t["params"] for t in tarefas)   # e ao menos uma na ordem padrão


def test_shopee_le_nota_e_vendas():
    o = shopee._node_para_oferta({"itemId": 1, "productName": "Mouse", "priceMin": "40.00",
                                  "priceDiscountRate": 20, "ratingStar": "4.85", "sales": 12345})
    assert (o.nota, o.vendas, o.vendas_mensal) == (4.85, 12_345, False)
    assert o.preco_original == 50.0


# ── post ─────────────────────────────────────────────────────────────

def test_post_mostra_prova_social_e_selos():
    o = Oferta("amazon", "B1", "Fone <JBL>", "x", preco=90.0, preco_original=120.0, nota=4.8,
               vendas=2_000, vendas_mensal=True, selos=["✅ 25% abaixo do preço médio dos últimos 14 dias"])
    texto = montar_caption(o)
    assert "⭐ 4,8 · +2 mil compras no último mês" in texto
    assert "25% abaixo do preço médio" in texto
    assert "&lt;JBL&gt;" in texto   # HTML do título é escapado
