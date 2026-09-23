"""Pedidos de clientes (pedidos.yaml): regras de título, faixa de preço, prioridade e produto internacional."""
import logging
from collections import Counter

import pytest

from ofertas import db, destinos, mix, pedidos, pipeline, selecao
from ofertas.config import BASE_DIR
from ofertas.formatter import montar_caption
from ofertas.models import Oferta
from ofertas.sources import mercadolivre

CPU = pedidos.Pedido("Ryzen 5 5600", ["ryzen 5 5600"], 500, 600, ["ryzen", "5600"], [],
                     ["kit", "combo", "upgrade", "placa", "notebook"])
PLACA = pedidos.Pedido("Placa-mãe A520 ou B550", ["placa mae a520", "placa mae b550"], 300, 600, ["placa"],
                       ["a520*", "b550*"], ["kit", "combo", "upgrade", "processador", "notebook"])
RAM = pedidos.Pedido("Memória 16GB DDR4", ["memoria ram ddr4 16gb"], 500, 1000, ["ddr4"], ["16gb", "2x8gb"],
                     ["notebook", "laptop", "sodimm", "so-dimm", "servidor", "ecc", "32gb", "64gb", "2x16gb", "4x8gb", "2133mhz"])


def oferta(titulo, preco=550.0, plataforma="mercadolivre", id_produto=None, nota=None, vendas=None, vendedor=None, **kw):
    return Oferta(plataforma, id_produto or titulo, titulo, "https://afiliado/x", url_produto=f"https://ml/{id_produto or titulo}",
                  preco=preco, nota=nota, vendas=vendas, vendedor=vendedor, **kw)


# ── normalização e palavras ──────────────────────────────────────────

@pytest.mark.parametrize("texto, esperado", [
    ("Placa Mãe A520M", "placa mae a520m"),
    ("Memória 16 GB DDR4 3200 Mhz", "memoria 16gb ddr4 3200mhz"),
    ("Kit 2 x 8GB", "kit 2x8gb"),
    ("  Ryzen   5   5600  ", "ryzen 5 5600"),
])
def test_normalizar(texto, esperado):
    assert pedidos.normalizar(texto) == esperado


# ── quais títulos combinam com cada pedido (títulos reais vistos no ML) ──

@pytest.mark.parametrize("titulo", [
    "Processador AMD Ryzen 5 5600, 6 Núcleos, 12 Threads, 3.5GHz (4.4GHz Turbo), AM4",
    "Amd Ryzen 5600 100-100001488box",
    "Processadores De Cpu Amd Ryzen 5 Série R5 5600",
    "PROCESSADOR AMD RYZEN 5 5600 COM COOLER WRAITH STEALTH",
])
def test_cpu_combina(titulo):
    assert pedidos.combina(CPU, titulo)


@pytest.mark.parametrize("titulo", [
    "Processador AMD Ryzen 5 5600X 4.6GHz AM4",                     # outro processador
    "Processador AMD Ryzen 5 5600G Com Vídeo 3.9GHz",
    "Processador Amd Ryzen 5 5600gt 3.6ghz Am4",
    "Kit Upgrade Gamer, Amd Ryzen 5 5600, A520m + 16gb Ddr4",       # kit: não é só o processador
    "Ryzen 5 5600 Usado Perfeito Estado",
    "Processador AMD Ryzen 5 5600 Recondicionado",
    "Processador Ryzen 5 4600G",
])
def test_cpu_nao_combina(titulo):
    assert not pedidos.combina(CPU, titulo)


@pytest.mark.parametrize("titulo", [
    "Placa-mãe Msi A520m-a Pro Am4 Matx Ddr4 Hdmi Dvi M.2",          # "A520M-A": o * do "a520*" aceita
    "Placa Mãe Msi Pro B550 M - A, Socket Am4, Ddr4, Matx",
    "Placa Mãe Gigabyte Ultra Durable A520M K V2",
    "Placa Mãe Golden Memory AM4 A520 DDR4 para AMD Ryzen",          # menciona Ryzen e continua sendo placa
    "Placa-mãe Am4 Msi B550m A Pro Msi",
])
def test_placa_combina(titulo):
    assert pedidos.combina(PLACA, titulo)


@pytest.mark.parametrize("titulo", [
    "Placa Mãe Asus Prime B450M-A II AM4",                           # outro chipset
    "Kit Placa Mãe Asus Prime A520m-e Amd Ryzen 5 5600g Vega 7",     # kit com processador
    "Upgrade Amd Ryzen 5 5600gt, Placa A520m-k",
    "Processador Ryzen 5 5600 para placa A520",                      # não tem "placa mae"... e é processador
])
def test_placa_nao_combina(titulo):
    assert not pedidos.combina(PLACA, titulo)


@pytest.mark.parametrize("titulo", [
    "Memória RAM Desktop/Computador DDR4 16GB 3200mhz 8bit Infinity Memory",
    "Memória Ram Samsung 16 GB 2666 Mhz Ddr4",
    "Kit Memória Ddr4 2x8gb 3200mhz Desktop",
    "2 Memórias Kingston Ddr4 8gb (2x8gb=16gb)3200mhz Desktop",
    "Memória Gamer Fury Beast 16gb Ddr4 Cl16 Kingston",
])
def test_ram_combina(titulo):
    assert pedidos.combina(RAM, titulo)


@pytest.mark.parametrize("titulo", [
    "Memória RAM color verde 16GB 1 DDR4 2133Mhz Crucial CT16G4SFD8213",   # é de notebook (SODIMM), 2133 MHz
    "Memória Ram Ddr4 16gb Para Notebook 3200mhz",
    "Memória Ram 16gb Ddr4 SO-DIMM 2666mhz",
    "Memória Ram Ddr4 32gb 3200mhz Desktop",
    "Memória Ram Ddr3 16gb 1600mhz Desktop",                                # DDR3
    "Memória Ram Ddr4 8gb 3200mhz Desktop",                                 # 8 GB, não 16
    "Memória Ram Ddr4 16gb Ecc Servidor",
])
def test_ram_nao_combina(titulo):
    assert not pedidos.combina(RAM, titulo)


def test_importado_no_titulo_desqualifica_qualquer_pedido():
    assert not pedidos.combina(CPU, "Processador Ryzen 5 5600 Importado dos EUA")


# ── leitura do arquivo ───────────────────────────────────────────────

@pytest.fixture
def arquivo(monkeypatch, tmp_path):
    def gravar(texto):
        caminho = tmp_path / "pedidos.yaml"
        caminho.write_text(texto, encoding="utf-8")
        monkeypatch.setattr(pedidos.config, "pedidos_arquivo", str(caminho))
        return caminho
    return gravar


def test_carregar_le_os_pedidos(arquivo):
    arquivo("""
pedidos:
  - nome: Ryzen 5 5600
    buscas: [ryzen 5 5600]
    preco: [500, 600]
    deve_ter: [ryzen, "5600"]
    qualquer_de: ["a", "b"]
    nao_deve_ter: [kit]
""")
    p, = pedidos.carregar()
    assert (p.nome, p.buscas, p.preco_min, p.preco_max, p.deve_ter, p.qualquer_de, p.nao_deve_ter) == \
        ("Ryzen 5 5600", ["ryzen 5 5600"], 500.0, 600.0, ["ryzen", "5600"], ["a", "b"], ["kit"])


def test_faixa_com_os_valores_invertidos_e_corrigida(arquivo):
    arquivo("pedidos:\n  - {nome: X, buscas: [x], preco: [600, 500]}\n")
    p, = pedidos.carregar()
    assert (p.preco_min, p.preco_max) == (500.0, 600.0)


def test_aceita_busca_como_texto_simples(arquivo):
    arquivo("pedidos:\n  - {nome: X, buscas: fone bluetooth, preco: [10, 20]}\n")
    assert pedidos.carregar()[0].buscas == ["fone bluetooth"]


def test_pedido_invalido_e_ignorado_com_aviso_e_os_outros_continuam(arquivo, caplog):
    arquivo("""
pedidos:
  - {nome: Sem faixa, buscas: [x]}
  - {nome: Faixa errada, buscas: [x], preco: [1, 2, 3]}
  - {nome: Preço não numérico, buscas: [x], preco: [abc, 5]}
  - {buscas: [x], preco: [1, 2]}
  - {nome: Bom, buscas: [x], preco: [1, 2]}
""")
    with caplog.at_level(logging.WARNING, logger="ofertas.pedidos"):
        assert [p.nome for p in pedidos.carregar()] == ["Bom"]
    assert caplog.text.count("ignorado") == 4


def test_arquivo_ausente_ou_desligado_nao_tem_pedidos(arquivo, monkeypatch, tmp_path):
    monkeypatch.setattr(pedidos.config, "pedidos_arquivo", str(tmp_path / "nao_existe.yaml"))
    assert pedidos.carregar() == []
    arquivo("pedidos:\n  - {nome: X, buscas: [x], preco: [1, 2]}\n")
    monkeypatch.setattr(pedidos.config, "pedidos_ativo", False)
    assert pedidos.carregar() == []


def test_yaml_quebrado_nao_derruba_o_bot(arquivo, caplog):
    arquivo("pedidos: [isto: não: é: yaml: válido")
    with caplog.at_level(logging.ERROR, logger="ofertas.pedidos"):
        assert pedidos.carregar() == []
    assert "Não consegui ler" in caplog.text


def test_o_pedidos_yaml_que_acompanha_o_projeto_esta_correto(monkeypatch):
    """Guarda contra erro de digitação no arquivo que o usuário edita: as 3 faixas pedidas."""
    monkeypatch.setattr(pedidos.config, "pedidos_arquivo", str(BASE_DIR / "pedidos.yaml"))
    lista = {p.nome: (p.preco_min, p.preco_max) for p in pedidos.carregar()}
    assert lista == {"Ryzen 5 5600": (500.0, 600.0), "Placa-mãe A520 ou B550 (AM4)": (300.0, 600.0),
                     "Memória RAM 16GB DDR4 (desktop)": (500.0, 1000.0),
                     "Ar-condicionado inverter (canal pessoal)": (1000.0, 1900.0)}


# ── marcação por faixa de preço ──────────────────────────────────────

def test_so_o_que_esta_na_faixa_vira_candidato_e_o_relatorio_diz_o_resto():
    achadas = [oferta("Processador AMD Ryzen 5 5600 Box", 897.35, id_produto="a"),
               oferta("Processador Amd Ryzen 5 5600 AM4", 1149.0, id_produto="b")]
    todas: list = []
    relatorio = pedidos.marcar(achadas, [CPU], todas)
    assert todas == []
    assert "nenhum na faixa R$ 500–600" in relatorio[0] and "897,35" in relatorio[0] and "aguardando" in relatorio[0]


def test_anuncio_na_faixa_vira_candidato_com_o_nome_do_pedido():
    achadas = [oferta("Processador AMD Ryzen 5 5600 Box", 580.0, id_produto="a"),
               oferta("Processador Amd Ryzen 5 5600 AM4", 950.0, id_produto="b")]
    todas: list = []
    relatorio = pedidos.marcar(achadas, [CPU], todas)
    assert [(o.id_produto, o.pedido) for o in todas] == [("a", "Ryzen 5 5600")]
    assert "1 de 2 anúncios na faixa" in relatorio[0] and "580,00" in relatorio[0]


def test_limites_da_faixa_sao_inclusivos():
    todas: list = []
    pedidos.marcar([oferta("Ryzen 5 5600 A", 500.0, id_produto="a"), oferta("Ryzen 5 5600 B", 600.0, id_produto="b"),
                    oferta("Ryzen 5 5600 C", 499.99, id_produto="c"), oferta("Ryzen 5 5600 D", 600.01, id_produto="d")],
                   [CPU], todas)
    assert sorted(o.id_produto for o in todas) == ["a", "b"]


def test_oferta_que_ja_esta_no_pool_e_marcada_no_lugar_sem_duplicar():
    ja_no_pool = oferta("Processador AMD Ryzen 5 5600 Box", 580.0, id_produto="a")
    todas = [ja_no_pool]
    pedidos.marcar([oferta("Processador AMD Ryzen 5 5600 Box", 580.0, id_produto="a")], [CPU], todas)
    assert len(todas) == 1 and todas[0] is ja_no_pool and ja_no_pool.pedido == "Ryzen 5 5600"


def test_mesmo_anuncio_achado_em_duas_buscas_conta_uma_vez():
    todas: list = []
    pedidos.marcar([oferta("Ryzen 5 5600 Box", 580.0, id_produto="a")] * 2, [CPU], todas)
    assert len(todas) == 1


def test_pedido_sem_nenhum_anuncio_que_combine():
    relatorio = pedidos.marcar([oferta("Notebook Dell", 3000.0)], [CPU], [])
    assert "nenhum anúncio combina" in relatorio[0]


def test_anuncio_sem_preco_e_ignorado():
    todas: list = []
    pedidos.marcar([oferta("Ryzen 5 5600 Box", None, id_produto="a")], [CPU], todas)
    assert todas == []


# ── coleta (busca) ───────────────────────────────────────────────────

@pytest.fixture
def buscas(monkeypatch, arquivo):
    arquivo("""
pedidos:
  - {nome: Ryzen 5 5600, buscas: [ryzen 5 5600], preco: [500, 600], deve_ter: [ryzen, "5600"]}
  - {nome: Outro Ryzen, buscas: [ryzen 5 5600, ryzen 5 5500], preco: [400, 500], deve_ter: [ryzen, "5500"]}
""")
    monkeypatch.setattr(pedidos.config, "fonte_ml", {"ativa": True})
    monkeypatch.setattr(pedidos.config, "fonte_amazon", {"ativa": False})
    monkeypatch.setattr(pedidos.config, "fonte_shopee", {"ativa": False})   # pipeline.coletar() também busca na Shopee
    monkeypatch.setattr(mercadolivre, "tem_sessao", lambda: True)
    chamadas = []
    monkeypatch.setattr(mercadolivre, "buscar_termos",
                        lambda t: chamadas.append(list(t)) or [oferta("Processador Ryzen 5 5600 Box", 570.0, id_produto="a")])
    return chamadas


def test_coleta_pesquisa_cada_termo_uma_vez_e_marca_os_candidatos(buscas):
    todas: list = []
    relatorio = pedidos.coletar(todas)
    assert buscas == [["ryzen 5 5600", "ryzen 5 5500"]]                # o termo repetido entre pedidos vai uma vez só
    assert [(o.id_produto, o.pedido) for o in todas] == [("a", "Ryzen 5 5600")]
    assert len(relatorio) == 2


def test_falha_na_busca_nao_derruba_a_coleta(buscas, monkeypatch, caplog):
    def falha(_):
        raise RuntimeError("sessão expirou")
    monkeypatch.setattr(mercadolivre, "buscar_termos", falha)
    with caplog.at_level(logging.ERROR, logger="ofertas.pedidos"):
        assert len(pedidos.coletar([])) == 2                          # ainda relata cada pedido
    assert "sessão expirou" in caplog.text


def test_sem_pedidos_nao_faz_busca_nenhuma(monkeypatch, tmp_path):
    monkeypatch.setattr(pedidos.config, "pedidos_arquivo", str(tmp_path / "nao_existe.yaml"))
    monkeypatch.setattr(mercadolivre, "buscar_termos", lambda t: pytest.fail("não deveria buscar"))
    assert pedidos.coletar([]) == []


def test_o_pipeline_coleta_os_pedidos_junto_com_as_ofertas(monkeypatch, buscas):
    monkeypatch.setattr(pipeline.mercadolivre, "buscar_ofertas", lambda: [oferta("Panela Tramontina", 90.0, id_produto="g1")])
    assert sorted(o.id_produto for o in pipeline.coletar()) == ["a", "g1"]


# ── avaliação de um pedido ───────────────────────────────────────────

def pedido(titulo, preco=550.0, nome="Ryzen 5 5600", **kw):
    return oferta(titulo, preco, id_produto=kw.pop("id_produto", titulo), pedido=nome, **kw)


def test_pedido_no_ml_sem_nota_passa_como_faixa_pedido_com_selo():
    o = pedido("Processador AMD Ryzen 5 5600 Box")
    assert selecao.avaliar(o, None) is None
    assert o.faixa == "pedido" and "📌 Pedido de cliente" in o.selos


def test_oferta_normal_sem_nota_continua_sendo_rejeitada():
    assert selecao.avaliar(oferta("Processador AMD Ryzen 5 5600 Box"), None) == "sem avaliação"


def test_pedido_na_amazon_precisa_de_nota_porque_la_nao_se_confere_o_vendedor():
    assert selecao.avaliar(pedido("Processador AMD Ryzen 5 5600", plataforma="amazon"), None) == "sem avaliação"
    ok = pedido("Processador AMD Ryzen 5 5600", plataforma="amazon", nota=4.8)
    assert selecao.avaliar(ok, None) is None and ok.faixa == "pedido"


def test_pedido_com_nota_baixa_e_rejeitado():
    assert selecao.avaliar(pedido("Processador AMD Ryzen 5 5600", nota=3.9), None).startswith("nota baixa")


def test_pedido_nao_exige_vendas_nem_queda_de_preco():
    o = pedido("Processador AMD Ryzen 5 5600", nota=4.6, vendas=3)      # poucas vendas, sem histórico
    assert selecao.avaliar(o, None) is None


def test_pedido_ainda_passa_pelos_portoes_de_preco_fora_da_curva():
    o = pedido("Processador AMD Ryzen 5 5600 Box", 550.0)
    o.preco_mercado = 1200.0                                             # 550 é menos da metade dos anúncios iguais
    assert selecao.avaliar(o, None).startswith("preço muito abaixo")


def test_sem_selo_configurado_o_post_nao_traz_a_linha(monkeypatch):
    monkeypatch.setattr(selecao.config, "pedidos_selo", "")
    o = pedido("Processador AMD Ryzen 5 5600 Box")
    selecao.avaliar(o, None)
    assert o.selos == []


def test_post_de_pedido_mostra_o_selo():
    o = pedido("Processador AMD Ryzen 5 5600 Box", nota=4.8)
    selecao.avaliar(o, None)
    assert "📌 Pedido de cliente" in montar_caption(o)


# ── produto internacional ────────────────────────────────────────────

PAGINA_IMPORTADA = '<script>{"tags":["kvs_primary","immediate_payment","cbt_item","cbt_ids"],"other":1}</script>'
PAGINA_NACIONAL = '<script>{"tags":["kvs_primary","immediate_payment","cart_eligible"],"cbt_taxes_summary":{"id":"x"}}</script>'


def test_detecta_anuncio_de_importacao_pela_tag_cbt_item():
    assert mercadolivre.parse_internacional(PAGINA_IMPORTADA) is True


def test_pagina_nacional_nao_e_marcada_mesmo_citando_cbt_em_outro_lugar():
    """A página nacional também tem componentes 'cbt_*' (carrossel, resumo de impostos): só a TAG conta."""
    assert mercadolivre.parse_internacional(PAGINA_NACIONAL) is False
    assert mercadolivre.parse_internacional("<html>compra internacional</html>") is False


def test_avaliar_internacional_respeita_a_configuracao(monkeypatch):
    o = oferta("Produto", internacional=True)
    assert selecao.avaliar_internacional(o) == "produto internacional"
    assert selecao.avaliar_internacional(oferta("Produto", internacional=False)) is None
    assert selecao.avaliar_internacional(oferta("Produto", internacional=None)) is None
    monkeypatch.setattr(selecao.config, "evitar_internacional", False)
    assert selecao.avaliar_internacional(o) is None


def test_pedido_sem_dados_do_vendedor_nao_passa_mas_oferta_normal_sim():
    p = pedido("Processador AMD Ryzen 5 5600 Box")
    p.vendedor_checado = True                                           # a página foi lida, mas sem dados do vendedor
    assert selecao.avaliar_vendedor(p) == "vendedor não verificado"
    assert selecao.avaliar_vendedor(oferta("Produto normal")) is None


def test_pedido_com_vendedor_de_reputacao_baixa_e_reprovado():
    p = pedido("Processador AMD Ryzen 5 5600 Box")
    p.vendedor_nivel = 3
    assert selecao.avaliar_vendedor(p).startswith("vendedor com reputação baixa")


# ── prioridade no pipeline ───────────────────────────────────────────

def aprovado(titulo, preco, score, nome=None, plataforma="mercadolivre", id_produto=None, faixa="campeao"):
    return Oferta(plataforma, id_produto or titulo, titulo, "x", url_produto="https://p", preco=preco, score=score,
                  faixa="pedido" if nome else faixa, pedido=nome)


def test_um_anuncio_por_pedido_o_mais_barato():
    ofertas = [aprovado("Processador Ryzen 5 5600 Box", 590.0, 0.5, "cpu", id_produto="a"),
               aprovado("Processador AMD Ryzen 5 5600 Cooler Wraith", 540.0, 0.5, "cpu", id_produto="b")]
    assert [o.id_produto for o in pipeline.escolher_pedidos(ofertas, 5)] == ["b"]


def test_respeita_o_limite_e_prefere_o_pedido_de_maior_score():
    ofertas = [aprovado("Processador Ryzen 5 5600 Box", 550.0, 0.40, "cpu"),
               aprovado("Placa Mãe Msi A520M Pro", 400.0, 0.60, "placa"),
               aprovado("Memória Ddr4 16gb Kingston Fury", 600.0, 0.50, "ram")]
    assert [o.pedido for o in pipeline.escolher_pedidos(ofertas, 2)] == ["placa", "ram"]


def test_ofertas_sem_pedido_nao_entram_em_escolher_pedidos():
    assert pipeline.escolher_pedidos([aprovado("Panela Tramontina", 90.0, 0.9)], 5) == []


def test_pedidos_ganham_a_vaga_mesmo_com_score_menor_e_o_resto_completa():
    from ofertas import destinos
    ofertas = [aprovado("Processador Ryzen 5 5600 Box", 550.0, 0.10, "cpu"),
               aprovado("Panela Tramontina Antiaderente", 90.0, 0.90),
               aprovado("Tênis Kappa Park Masculino", 90.0, 0.80),
               aprovado("Cadeira Escritório Ergonômica", 90.0, 0.70)]
    escolhidas = pipeline._montar(ofertas, 3, Counter(), destinos.geral())
    assert escolhidas[0].pedido == "cpu" and len(escolhidas) == 3


def test_limite_de_pedidos_por_ciclo_e_o_da_configuracao(monkeypatch):
    from ofertas import destinos
    monkeypatch.setattr(pipeline.config, "pedidos_max_por_ciclo", 1)
    ofertas = [aprovado("Processador Ryzen 5 5600 Box", 550.0, 0.5, "cpu"),
               aprovado("Placa Mãe Msi A520M Pro", 400.0, 0.5, "placa")]
    escolhidas = pipeline._montar(ofertas, 5, Counter(), destinos.geral())
    assert len(escolhidas) == 1                     # o pedido que não coube espera o próximo ciclo, não vira oferta normal


def test_o_anuncio_normal_parecido_com_um_pedido_escolhido_nao_repete():
    from ofertas import destinos
    ofertas = [aprovado("Processador Ryzen 5 5600 Box", 550.0, 0.5, "cpu", id_produto="a"),
               aprovado("Processador Ryzen 5 5600 Box Oferta", 560.0, 0.9, id_produto="b")]
    assert [o.id_produto for o in pipeline._montar(ofertas, 5, Counter(), destinos.geral())] == ["a"]


# ── selecionar: pedido passa pelo mix/variedade e pela conferência da página ──

def ml_pedido(id_produto, titulo, preco, nome="Ryzen 5 5600", **kw):
    return Oferta("mercadolivre", id_produto, titulo, "x", url_produto=f"https://ml/{id_produto}", preco=preco,
                  pedido=nome, **kw)


def leitor_de_paginas(mapa):
    """Troca a leitura da página do ML: mapa {id_produto: (nível do vendedor, internacional)}."""
    def verificar(ofertas):
        for o in ofertas:
            o.vendedor_checado = True
            o.vendedor_nivel, o.internacional = mapa[o.id_produto]
            o.vendedor_status, o.loja_oficial = "gold", False
    return verificar


def test_pedido_de_vendedor_confiavel_e_nacional_e_postado(monkeypatch):
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", leitor_de_paginas({"a": (5, False)}))
    escolhidas, _ = pipeline.selecionar([ml_pedido("a", "Processador AMD Ryzen 5 5600 Box", 580.0)], 3)
    assert [o.id_produto for o in escolhidas] == ["a"] and escolhidas[0].faixa == "pedido"


def test_pedido_importado_sai_e_a_vaga_vai_para_o_proximo_anuncio_do_mesmo_pedido(monkeypatch):
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores",
                        leitor_de_paginas({"imp": (5, True), "nac": (5, False)}))
    brutas = [ml_pedido("imp", "Processador AMD Ryzen 5 5600 Box", 520.0),
              ml_pedido("nac", "Processador AMD Ryzen 5 5600 Cooler Wraith", 560.0)]
    escolhidas, rejeicoes = pipeline.selecionar(brutas, 3)
    assert [o.id_produto for o in escolhidas] == ["nac"]
    assert rejeicoes["produto internacional"] == 1


def test_pedido_com_vendedor_de_reputacao_baixa_nao_e_postado(monkeypatch):
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", leitor_de_paginas({"a": (3, False)}))
    escolhidas, rejeicoes = pipeline.selecionar([ml_pedido("a", "Processador AMD Ryzen 5 5600 Box", 580.0)], 3)
    assert escolhidas == [] and rejeicoes["vendedor com reputação baixa"] == 1


def test_se_a_pagina_nao_puder_ser_lida_o_pedido_do_ml_nao_e_postado(monkeypatch):
    """Sem nota para se apoiar, o pedido só sai com o vendedor conferido (ao contrário de uma oferta comum)."""
    def falha(_):
        raise RuntimeError("navegador indisponível")
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", falha)
    escolhidas, _ = pipeline.selecionar([ml_pedido("a", "Processador AMD Ryzen 5 5600 Box", 580.0)], 3)
    assert escolhidas == []


def test_pedido_confere_a_pagina_mesmo_com_a_verificacao_de_vendedor_desligada(monkeypatch):
    monkeypatch.setattr(pipeline.config, "verificar_vendedor", False)
    monkeypatch.setattr(pipeline.config, "buscar_cupons", False)
    monkeypatch.setattr(pipeline.config, "evitar_internacional", False)
    lidas = []
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores",
                        lambda os: lidas.extend(o.id_produto for o in os) or leitor_de_paginas({"a": (5, False)})(os))
    escolhidas, _ = pipeline.selecionar([ml_pedido("a", "Processador AMD Ryzen 5 5600 Box", 580.0)], 3)
    assert lidas == ["a"] and len(escolhidas) == 1


def test_pedido_na_amazon_nao_abre_a_pagina_do_ml(monkeypatch):
    def nao_deveria_abrir(_):
        raise AssertionError("Amazon não tem página do ML")
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", nao_deveria_abrir)
    o = Oferta("amazon", "B0ABC12345", "Processador AMD Ryzen 5 5600 Box", "x", url_produto="https://a", preco=580.0,
               nota=4.7, pedido="Ryzen 5 5600")
    assert len(pipeline.selecionar([o], 3)[0]) == 1


def test_pedido_ignora_o_mix_de_categorias_e_a_variedade(monkeypatch):
    monkeypatch.setattr(pipeline.mix, "motivo_de_exclusao", lambda o: "tecnologia acima do teto de R$ 1")
    monkeypatch.setattr(pipeline, "tipo_do_produto", lambda titulo, uid=None: "processador")
    monkeypatch.setattr(pipeline, "_tipos_recentes", lambda destino: {"processador"})
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", leitor_de_paginas({"a": (5, False)}))
    normal = Oferta("mercadolivre", "n", "Processador Outro Modelo", "x", url_produto="https://n", preco=100.0,
                    nota=4.9, vendas=50_000)
    escolhidas, rejeicoes = pipeline.selecionar([ml_pedido("a", "Processador AMD Ryzen 5 5600 Box", 580.0), normal], 3)
    assert [o.id_produto for o in escolhidas] == ["a"]            # o normal tomou as duas barreiras; o pedido, nenhuma
    assert rejeicoes.get("tipo postado há pouco") == 1


def test_pedido_ja_postado_recentemente_nao_repete(monkeypatch):
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", leitor_de_paginas({"a": (5, False)}))
    db.registrar(ml_pedido("a", "Processador AMD Ryzen 5 5600 Box", 580.0))
    escolhidas, rejeicoes = pipeline.selecionar([ml_pedido("a", "Processador AMD Ryzen 5 5600 Box", 580.0)], 3)
    assert escolhidas == [] and rejeicoes["já postada"] == 1


# ── pausar um pedido (ativo: false) ──────────────────────────────────

def test_pedido_pausado_no_arquivo_e_lido_como_inativo(arquivo):
    arquivo("""
pedidos:
  - {nome: Ligado, buscas: [x], preco: [1, 2]}
  - {nome: Explicito, buscas: [x], preco: [1, 2], ativo: true}
  - {nome: Pausado, buscas: [x], preco: [1, 2], ativo: false}
  - {nome: Pausado com no, buscas: [x], preco: [1, 2], ativo: no}
  - {nome: Pausado em portugues, buscas: [x], preco: [1, 2], ativo: não}
  - {nome: Pausado por texto, buscas: [x], preco: [1, 2], ativo: pausado}
""")
    assert {p.nome: p.ativo for p in pedidos.carregar()} == {
        "Ligado": True, "Explicito": True, "Pausado": False, "Pausado com no": False,
        "Pausado em portugues": False, "Pausado por texto": False}


def test_pedido_pausado_nao_e_pesquisado_nem_prioriza_e_aparece_no_relatorio(monkeypatch, arquivo):
    arquivo("""
pedidos:
  - {nome: Ryzen 5 5600, buscas: [ryzen 5 5600], preco: [500, 600], deve_ter: [ryzen, "5600"]}
  - {nome: Placa pausada, buscas: [placa mae a520], preco: [300, 600], deve_ter: [placa], ativo: false}
""")
    monkeypatch.setattr(pedidos.config, "fonte_ml", {"ativa": True})
    monkeypatch.setattr(pedidos.config, "fonte_amazon", {"ativa": False})
    monkeypatch.setattr(mercadolivre, "tem_sessao", lambda: True)
    buscados = []
    monkeypatch.setattr(mercadolivre, "buscar_termos", lambda t: buscados.append(list(t)) or [
        oferta("Processador Ryzen 5 5600 Box", 570.0, id_produto="a"),
        oferta("Placa Mãe Msi A520M Pro", 400.0, id_produto="b")])
    todas: list = []
    relatorio = pedidos.coletar(todas)
    assert buscados == [["ryzen 5 5600"]]                                # o termo da placa nem foi pesquisado
    assert [o.id_produto for o in todas] == ["a"]
    assert len(relatorio) == 2 and "pausado" in relatorio[1] and "Placa pausada" in relatorio[1]


def test_todos_pausados_nao_busca_nada_e_so_relata(monkeypatch, arquivo):
    arquivo("pedidos:\n  - {nome: Só um, buscas: [x], preco: [1, 2], ativo: false}\n")
    monkeypatch.setattr(mercadolivre, "buscar_termos", lambda t: pytest.fail("não deveria buscar"))
    relatorio = pedidos.coletar([])
    assert len(relatorio) == 1 and "pausado" in relatorio[0]


# ── ar-condicionado e destino próprio (canal pessoal) ─────────────────

@pytest.fixture
def ar_condicionado(monkeypatch):
    """O pedido de ar-condicionado como está no pedidos.yaml que acompanha o projeto."""
    monkeypatch.setattr(pedidos.config, "pedidos_arquivo", str(BASE_DIR / "pedidos.yaml"))
    return next(p for p in pedidos.carregar() if p.nome.startswith("Ar-condicionado"))


def test_o_ar_condicionado_do_arquivo_vai_para_o_canal_pessoal_com_teto_de_1900(ar_condicionado):
    assert ar_condicionado.ativo and ar_condicionado.destino == "pessoal" and ar_condicionado.preco_max == 1900.0


@pytest.mark.parametrize("titulo", [
    "Ar Condicionado Split Hi Wall Inverter Gree G-top 12000 Btus Frio 220v",
    "Ar-condicionado Split Inverter Philco 9000 Btus Quente E Frio",
    "Ar Condicionado Inverter Midea Ai Ecomaster 12000 Btu/h Frio",
    "Ar Condicionado Split Inversor Elgin Eco 9000 Btus",
])
def test_ar_condicionado_inverter_combina(ar_condicionado, titulo):
    assert pedidos.combina(ar_condicionado, titulo)


@pytest.mark.parametrize("titulo", [
    "Ar Condicionado Split Hi Wall 12000 Btus Só Frio",                   # não é inverter
    "Ar Condicionado Portátil 12000 Btus Quente E Frio",
    "Suporte Ar Condicionado Split Inverter 9000 A 18000 Btus",
    "Controle Remoto Universal Para Ar Condicionado Inverter",
    "Placa Eletrônica Ar Condicionado Split Inverter Samsung",
    "Cortina De Ar Condicionado Inverter",
    "Ar Condicionado Split Inverter Consul 12000 Btus Usado",
])
def test_ar_condicionado_que_nao_e_o_pedido(ar_condicionado, titulo):
    assert not pedidos.combina(ar_condicionado, titulo)


def test_hifen_vale_como_espaco_nas_regras_de_titulo():
    assert pedidos.normalizar("Ar-Condicionado SO-DIMM") == "ar condicionado so dimm"
    assert pedidos.combina(RAM, "Memória DDR4 16GB SO-DIMM") is False          # "so-dimm" continua barrando


def test_destino_do_pedido_e_lido_e_validado(arquivo, caplog):
    arquivo("""
pedidos:
  - {nome: Sem destino, buscas: [x], preco: [1, 2]}
  - {nome: Canal pessoal, buscas: [x], preco: [1, 2], destino: Pessoal}
  - {nome: Grupo Apple, buscas: [x], preco: [1, 2], destino: apple}
  - {nome: Geral, buscas: [x], preco: [1, 2], destino: geral}
  - {nome: Errado, buscas: [x], preco: [1, 2], destino: telegram}
""")
    with caplog.at_level(logging.WARNING, logger="ofertas.pedidos"):
        lido = {p.nome: p.destino for p in pedidos.carregar()}
    assert lido == {"Sem destino": None, "Canal pessoal": "pessoal", "Grupo Apple": "apple", "Geral": "geral"}
    assert "destino 'telegram' inválido" in caplog.text


def test_anuncio_marcado_leva_o_destino_do_pedido():
    p = pedidos.Pedido("Ar", ["ar"], 1000, 1900, ["ar condicionado"], [], [], True, "pessoal")
    todas: list = []
    pedidos.marcar([oferta("Ar Condicionado Split Inverter 12000 Btus", 1700.0, id_produto="a")], [p], todas)
    assert todas[0].destino == "pessoal"


CANAL_PESSOAL = "-1001234567890"


@pytest.fixture
def com_canal_pessoal(monkeypatch):
    monkeypatch.setattr(destinos.config, "chat_id_pessoal", CANAL_PESSOAL)


def test_pedido_com_destino_pessoal_vai_para_o_canal_pessoal(com_canal_pessoal):
    ar = oferta("Ar Condicionado Split Inverter 12000 Btus", 1700.0, id_produto="ar", destino="pessoal", pedido="Ar")
    fone = oferta("Fone Bluetooth", 50.0, id_produto="fone")
    iphone = oferta("iPhone 15 128gb Apple", 3500.0, id_produto="iph")   # sem grupo Apple configurado: cai no geral
    grupos = destinos.dividir([ar, fone, iphone])
    assert [o.id_produto for o in grupos["pessoal"]] == ["ar"]
    assert [o.id_produto for o in grupos["geral"]] == ["fone", "iph"]


def test_destino_geral_forcado_vence_o_produto_apple():
    iphone = oferta("iPhone 15 128gb Apple", 3500.0, id_produto="iph", destino="geral")
    assert [o.id_produto for o in destinos.dividir([iphone])["geral"]] == ["iph"]


def test_sem_canal_pessoal_configurado_o_pedido_cai_no_geral():
    ar = oferta("Ar Condicionado Split Inverter 12000 Btus", 1700.0, id_produto="ar", destino="pessoal", pedido="Ar")
    assert [o.id_produto for o in destinos.dividir([ar])["geral"]] == ["ar"]
    assert destinos.chat_para(ar)[0] == "geral"


def test_chat_para_leva_o_pedido_ao_canal_pessoal(com_canal_pessoal):
    ar = oferta("Ar Condicionado Split Inverter 12000 Btus", 1700.0, id_produto="ar", destino="pessoal")
    assert destinos.chat_para(ar) == ("pessoal", CANAL_PESSOAL)


def test_pedido_de_ar_condicionado_e_postado_no_canal_pessoal_mesmo_sem_queda_de_preco(monkeypatch, com_canal_pessoal):
    """O canal pessoal não tem faixa de campeões nem de queda; o pedido passa só pela faixa de preço."""
    monkeypatch.setattr(pipeline.mercadolivre, "verificar_vendedores", leitor_de_paginas({"ar": (5, False)}))
    ar = ml_pedido("ar", "Ar Condicionado Split Inverter Gree 12000 Btus", 1799.0, nome="Ar", destino="pessoal")
    grupos = destinos.dividir([ar])
    escolhidas, _ = pipeline.selecionar(grupos["pessoal"], 3, True, destinos.pessoal())
    assert [o.id_produto for o in escolhidas] == ["ar"]


def test_canal_pessoal_e_grupo_apple_nao_se_confundem(monkeypatch, com_canal_pessoal):
    """Configurados os dois ao mesmo tempo: pedido pessoal vai para um, produto Apple para o outro."""
    monkeypatch.setattr(destinos.config, "chat_id_apple", "-1009999999999")
    ar = oferta("Ar Condicionado Split Inverter 12000 Btus", 1700.0, id_produto="ar", destino="pessoal", pedido="Ar")
    iphone = oferta("iPhone 15 128gb Apple", 3500.0, id_produto="iph")
    grupos = destinos.dividir([ar, iphone])
    assert [o.id_produto for o in grupos["pessoal"]] == ["ar"]
    assert [o.id_produto for o in grupos["apple"]] == ["iph"]
    assert destinos.chat_para(ar) == ("pessoal", CANAL_PESSOAL)
    assert destinos.chat_para(iphone) == ("apple", "-1009999999999")
