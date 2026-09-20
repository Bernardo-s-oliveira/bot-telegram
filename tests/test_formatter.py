import html
import re

import pytest

from ofertas.formatter import montar_caption, preco_por_unidade, titulo_curto
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
    assert texto.endswith("🛒 Loja: Shopee")


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



# ── layout do post ───────────────────────────────────────────────────

def test_layout_do_post_preco_depois_nota_e_vendas_na_mesma_linha_e_a_loja_no_fim():
    o = Oferta("mercadolivre", "1", "Tênis Masculino Kappa Park 2.0 Original", "x", preco=58.55, nota=4.9, vendas=50_000)
    assert montar_caption(o) == (
        "👟 <b>Tênis Masculino Kappa Park 2.0 Original</b>\n"
        "\n"
        "💰 <b>R$ 58,55</b>\n"
        "⭐ 4,9 · +50 mil vendidos\n"
        "🛒 Loja: Mercado Livre"
    )


def test_loja_de_cada_plataforma():
    for plataforma, nome in (("mercadolivre", "Mercado Livre"), ("shopee", "Shopee"), ("amazon", "Amazon")):
        assert montar_caption(Oferta(plataforma, "1", "Item", "x", preco=10.0)).endswith(f"🛒 Loja: {nome}")


def test_sem_nota_nem_vendas_o_post_nao_tem_linhas_vazias_de_prova_social():
    texto = montar_caption(Oferta("amazon", "1", "Item", "x", preco=10.0))
    assert "⭐" not in texto and "🏆" not in texto and "\n\n\n" not in texto


def test_selos_cupom_e_extras_ficam_entre_a_linha_de_vendas_e_a_loja():
    o = Oferta("mercadolivre", "1", "Item", "x", preco=58.55, nota=4.9, vendas=50_000,
               selos=["📉 Menor preço dos últimos 30 dias"], cupom="🎟 USAR CUPOM: ativar na página → R$ 50,00",
               extra="🚚 Frete grátis")
    linhas = montar_caption(o).split("\n")
    assert linhas[2:] == ["💰 <b>R$ 58,55</b>", "⭐ 4,9 · +50 mil vendidos", "📉 Menor preço dos últimos 30 dias",
                          "🎟 USAR CUPOM: ativar na página → R$ 50,00", "🚚 Frete grátis", "🛒 Loja: Mercado Livre"]


# ── preço por unidade ────────────────────────────────────────────────

@pytest.mark.parametrize("titulo, preco, esperado", [
    ("Papel Higiênico Supra Folha Tripla 24 Rolos", 34.90, "R$ 1,45/rolo"),
    ("Kit 6 Pares Meias Puma Cano Médio Alto", 60.0, "R$ 10,00/par"),
    ("Vitamina C 1000mg 120 Cápsulas", 60.0, "R$ 0,50/cápsula"),
    ("Papel Toalha Scott 6 Rolos 60 Folhas", 24.0, "R$ 4,00/rolo"),      # "folhas" não é unidade
    ("Bateria Alcalina AA 12 Pilhas", 36.0, "R$ 3,00/pilha"),
    ("Caixa com 12 Unidades Barra de Cereal", 24.0, "R$ 2,00/unidade"),
    ("Lâmina de Barbear 10 Lâminas", 50.0, "R$ 5,00/lâmina"),
])
def test_preco_por_unidade(titulo, preco, esperado):
    assert preco_por_unidade(titulo, preco) == esperado


@pytest.mark.parametrize("titulo", [
    "Fralda Pampers 4 Pacotes com 30 unidades",      # embalagem dentro de embalagem: seriam 120
    "Papel Higiênico Leve 24 Pague 20 Rolos",         # paga por menos do que leva
    "Pilha AA 2x12 unidades",                          # 2 × 12
    "Papel Higiênico 12 Rolos + 4 Pares de Meia",      # duas unidades diferentes
    "Papel Higiênico 12 Rolos 30 Rolos Folha Dupla",   # duas quantidades diferentes
    "Creatina Monohidratada 500g",                     # nenhuma unidade contável
    "Shampoo 750ml Hidratante",
    "Kit 10 Potes de Vidro 640ml",                     # "pote" não está na lista
    "Notebook Gamer 1 Unidade",                        # 1 não é "por unidade"
    "Smart TV 50 polegadas",
])
def test_preco_por_unidade_na_duvida_nao_mostra(titulo):
    assert preco_por_unidade(titulo, 100.0) is None


def test_preco_por_unidade_sem_preco():
    assert preco_por_unidade("Papel Higiênico 24 Rolos", None) is None
    assert preco_por_unidade("Papel Higiênico 24 Rolos", 0) is None


def test_post_do_exemplo_papel_higienico():
    o = Oferta("mercadolivre", "1", "Papel Higiênico Supra Folha Tripla 24 Rolos", "x", preco=34.90,
               nota=4.9, vendas=100_000, loja_oficial=True)
    assert montar_caption(o) == (
        "🧻 <b>Papel Higiênico Supra Folha Tripla 24 Rolos</b>\n"
        "\n"
        "💰 <b>R$ 34,90</b> (R$ 1,45/rolo)\n"
        "⭐ 4,9 · +100 mil vendidos · Loja oficial\n"
        "🛒 Loja: Mercado Livre"
    )


def test_preco_por_unidade_tambem_na_linha_por_da_queda_comprovada():
    o = Oferta("mercadolivre", "1", "Papel Higiênico 24 Rolos", "x", preco=34.90, preco_original=42.0,
               desconto_verificado=True)
    assert "💰 Por: <b>R$ 34,90</b> (R$ 1,45/rolo)" in montar_caption(o)


# ── emoji do produto ─────────────────────────────────────────────────

@pytest.mark.parametrize("titulo, emoji", [
    ("Papel Higiênico Supra Folha Tripla 24 Rolos", "🧻"),
    ("Anker Soundcore P20i Fone de Ouvido Bluetooth", "🎧"),
    ("Chuveiro Lorenzetti Advanced 7500W", "🚿"),
    ("Creatina Monohidratada Dark Lab 500g", "💪"),
    ("Tênis Masculino Kappa Park 2.0", "👟"),
    ("Sociedade do cansaço", "🔥"),                    # tipo desconhecido: o 🔥 de sempre
])
def test_emoji_do_produto_abre_o_post(titulo, emoji):
    assert montar_caption(Oferta("amazon", "1", titulo, "x", preco=10.0)).startswith(f"{emoji} <b>")


def test_nenhum_emoji_orfao():
    from ofertas.tipos import EMOJIS, TIPOS
    assert set(EMOJIS) <= set(TIPOS)                  # todo emoji pertence a um tipo que existe


# ── linha de nota, vendas e loja oficial ─────────────────────────────

def linha_social(o):
    linhas = montar_caption(o).split("\n")
    return next((l for l in linhas if "⭐" in l or "vendidos" in l or "Loja oficial" in l), None)


def test_linha_social_com_tudo():
    o = Oferta("mercadolivre", "1", "Item", "x", preco=10.0, nota=4.9, vendas=100_000, loja_oficial=True)
    assert linha_social(o) == "⭐ 4,9 · +100 mil vendidos · Loja oficial"


def test_linha_social_sem_loja_oficial_nao_deixa_separador_sobrando():
    o = Oferta("mercadolivre", "1", "Item", "x", preco=10.0, nota=4.9, vendas=100_000)
    assert linha_social(o) == "⭐ 4,9 · +100 mil vendidos"


def test_linha_social_so_com_nota_ou_so_com_loja_oficial():
    assert linha_social(Oferta("shopee", "1", "Item", "x", preco=10.0, nota=4.7)) == "⭐ 4,7"
    assert linha_social(Oferta("mercadolivre", "1", "Item", "x", preco=10.0, loja_oficial=True)) == "Loja oficial"


def test_linha_social_na_amazon_diz_compras_no_ultimo_mes():
    o = Oferta("amazon", "1", "Item", "x", preco=10.0, nota=4.5, vendas=8_000, vendas_mensal=True)
    assert linha_social(o) == "⭐ 4,5 · +8 mil compras no último mês"


def test_loja_oficial_nao_aparece_duas_vezes():
    o = Oferta("mercadolivre", "1", "Item", "x", preco=10.0, nota=4.9, vendas=1_000, loja_oficial=True)
    assert montar_caption(o).count("Loja oficial") == 1


# ── títulos reais da Amazon que enganavam o dicionário de tipos ──────

@pytest.mark.parametrize("titulo, tipo", [
    ("KitKat Creme Crocante de Chocolate – Pasta para Passar, 330 g", "cafe"),          # não é hidratante
    ("Capa de Chuva Reutilizável Impermeável para Verão e Carnaval", "agasalho"),       # não é capinha de celular
    ("Cadeira Para Auto 0-36 Kg Mass Preta Litet", "fralda"),                            # cadeirinha de bebê, não móvel
    ("Garrafa De Tinta Original Epson Ecotank T544 Preto", "impressora"),               # não é garrafa térmica
    ("Kit 2 Saco para Lavar Tênis para Calçados (Cinza)", "limpeza"),                    # o saco vem antes do tênis
    ("BONI NATURAL - Creme Dental com óleos naturais de Menta", "higiene"),              # "creme dental" continua valendo
])
def test_tipos_de_titulos_reais_da_amazon(titulo, tipo):
    assert tipo_do_produto(titulo) == tipo


def test_o_tipo_vem_do_comeco_do_titulo_nao_de_uma_lista_de_usos_no_final():
    curto = "Duracell Pilhas Moeda CR2032 Pack 2 Unidades"
    longo = curto + ", bateria de lítio 3V para chaves, calculadoras, relógios, controles e placa mãe de computadores"
    assert tipo_do_produto(curto) is None
    assert tipo_do_produto(longo) is None            # "placa mãe" no final não faz da pilha um SSD
    assert tipo_do_produto("Anker Soundcore P20i Fone de Ouvido Bluetooth " + "x" * 90) == "fone"
