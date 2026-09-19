import html
import re

import pytest

from ofertas.formatter import montar_caption, titulo_curto
from ofertas.models import Oferta
from ofertas.tipos import tipo_do_produto

CONECTIVOS = {"de", "da", "do", "e", "com", "sem", "para", "por", "em", "a", "o"}


def visivel(caption: str) -> int:
    """Tamanho que o Telegram conta na legenda: texto sem tags, com entidades já resolvidas."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", caption)))


# ── linha de preço ───────────────────────────────────────────────────

def test_preco_usa_o_emoji_de_dinheiro_nas_tres_variantes():
    com_de_por = montar_caption(Oferta("amazon", "1", "Item", "x", preco=70.0, preco_original=100.0, desconto_verificado=True))
    sem_historico = montar_caption(Oferta("amazon", "1", "Item", "x", preco=70.0, preco_original=100.0))
    sem_de = montar_caption(Oferta("amazon", "1", "Item", "x", preco=70.0))
    assert "💰 Por: <b>R$ 70,00</b>" in com_de_por
    assert "💰 <b>R$ 70,00</b>" in sem_historico
    assert "💰 <b>R$ 70,00</b>" in sem_de
    for texto in (com_de_por, sem_historico, sem_de):
        assert "✅ Por" not in texto and "✅ <b>R$" not in texto


def test_post_nao_tem_rodape_de_divulgacao():
    texto = montar_caption(Oferta("shopee", "1", "Item", "x", preco=10.0))
    assert "t.me" not in texto and "#" not in texto and "Compartilhe" not in texto
    assert texto.endswith("🧡 Shopee")


# ── título curto ─────────────────────────────────────────────────────

ANKER = ("Anker Soundcore P20i Fone de Ouvido Bluetooth Sem Fio, 10mm Drivers, 30H de Reprodução, "
         "Bluetooth 5.3, Graves Potentes, Controle por App, IPX5, Pareamento Rápido")


def test_titulo_do_anker_e_cortado_no_separador_natural():
    assert titulo_curto(ANKER) == "Anker Soundcore P20i Fone de Ouvido Bluetooth Sem Fio"


def test_titulo_curto_nao_corta_palavra_nem_deixa_conectivo_pendurado():
    # sem vírgulas: o corte tem de cair no fim de uma palavra e não terminar em "sem"/"de"/"com"
    longo = "Fone de Ouvido Bluetooth Anker Soundcore P20i Sem Fio com 10mm Drivers e Graves Potentes 30H de Bateria"
    curto = titulo_curto(longo)
    assert len(curto) <= 75
    assert longo.startswith(curto) and longo[len(curto)] == " "          # fim de palavra
    assert curto.split()[-1].lower() not in CONECTIVOS


def test_titulo_que_terminaria_em_fone_sem_nao_termina():
    # o caso do post real: o corte de 75 caracteres caía em "…Fone sem"
    base = "Kit Acessorios Gamer Completo Premium Edicao Especial Anker Soundcore Fone"
    longo = f"{base} sem fio bluetooth 5.3"
    assert len(base) < 75
    curto = titulo_curto(longo, limite=len(base) + 4)     # limite cai logo depois de "sem"
    assert not curto.lower().endswith(" sem") and curto.endswith("Fone")


def test_titulo_curto_remove_numero_solto_no_fim():
    longo = "Parafusadeira Furadeira De Impacto The Black Tools Profissional TB-21PX 2 Baterias Com Maleta 60Hz"
    assert not re.search(r"\s\d{1,2}$", titulo_curto(longo))


def test_titulo_ate_o_limite_fica_como_esta():
    assert titulo_curto("Fone Bluetooth JBL Tune 510BT") == "Fone Bluetooth JBL Tune 510BT"
    assert titulo_curto("  Fone   Bluetooth \n JBL ") == "Fone Bluetooth JBL"


def test_codigo_de_modelo_no_inicio_e_removido():
    t = "Eps-6905 Balança Digital Corporal Bioimpedância Bluetooth Vidro Temperado 180kg Preta"
    assert titulo_curto(t).startswith("Balança Digital Corporal")
    assert not titulo_curto("TB-21PX Furadeira De Impacto Profissional").startswith("TB-21PX")


@pytest.mark.parametrize("titulo", [
    "S24 Ultra 256GB Samsung Galaxy Smartphone Preto",     # nome de modelo curto: é o produto
    "A36 Samsung Galaxy Smartphone 5G 128GB",
    "iPhone 15 128GB Apple Celular",
    "Eps-6905",                                            # sozinho, não há o que sobrar
    "Eps-6905 Xyzzy Plugh Quux",                           # resto não diz o que é o produto
])
def test_titulo_nao_perde_o_que_e_o_produto(titulo):
    assert titulo_curto(titulo).split()[0] == titulo.split()[0]


def test_post_usa_o_titulo_curto():
    texto = montar_caption(Oferta("amazon", "1", ANKER, "x", preco=100.0))
    assert "<b>Anker Soundcore P20i Fone de Ouvido Bluetooth Sem Fio</b>" in texto
    assert "IPX5" not in texto


def test_pior_caso_cabe_na_legenda_de_foto_de_1024_caracteres():
    o = Oferta("mercadolivre", "1", "Título muito longo de produto " * 12, "x", preco=1234.56, preco_original=2999.99,
               desconto_verificado=True, nota=4.9, vendas=1_500_000,
               selos=["✅ 41% abaixo do preço médio dos últimos 30 dias", "📉 Menor preço dos últimos 30 dias",
                      "🔁 De volta e mais barato: estava R$ 1.999,99 no último post", "🏅 Vendedor MercadoLíder Platinum"],
               cupom="🎟 Cupom de R$ 100,00 OFF → R$ 1.134,56 (ative na página do produto)",
               extra="🚚 Frete grátis · 💠 preço no Pix · Prime · 🏆 Mais vendido · ✔️ Escolha da Amazon · ⚡ Oferta Relâmpago")
    assert visivel(montar_caption(o)) <= 1024


# ── tipo de produto ──────────────────────────────────────────────────

@pytest.mark.parametrize("titulo, tipo", [
    ("Creatina Monohidratada Pura 500g Dark Lab", "creatina"),
    ("Chuveiro Lorenzetti Advanced 7500W", "chuveiro"),
    ("Eps-6905 Balança Digital Corporal", "balanca"),
    ("Balanças de Cozinha Digital", "balanca"),                       # plural
    ("Anker Soundcore P20i Fone de Ouvido Bluetooth", "fone"),         # marca antes do tipo
    ("JBL Tune 510BT Headset Bluetooth", "fone"),
    ("Capa para Celular Samsung", "capinha"),                          # o 1º tipo do título vence: capa, não celular
    ("Suporte Veicular para Celular", "suporte"),
    ("Samsung Galaxy Watch 6", "smartwatch"),                          # expressão longa antes de "galaxy"
    ("Samsung Galaxy A07 128gb", "celular"),
    ("Caixa de Som Bluetooth JBL", "caixa de som"),
    ("Processador Intel Core i5 12400F", None),                        # CPU não é "processador de alimentos"
    ("Sociedade do cansaço", None),
    ("", None),
])
def test_tipo_do_produto(titulo, tipo):
    assert tipo_do_produto(titulo) == tipo
