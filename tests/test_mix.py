"""Mix de categorias: fatia-alvo dos posts por categoria (Casa e Cozinha 22%, Moda 20%, Beleza 15%…)."""
import datetime as dt
from collections import Counter

import pytest

from ofertas import db, mix, pipeline
from ofertas.models import Oferta
from ofertas.tipos import CATEGORIAS, TIPOS, categoria_do_produto

METAS = {"casa_e_cozinha": 22, "moda": 20, "beleza": 15, "limpeza_e_higiene": 10, "tecnologia": 10,
         "esporte": 6, "saude": 5, "brinquedos_e_bebes": 5, "eletrodomesticos": 5, "outros": 2}


@pytest.fixture(autouse=True)
def mix_ligado(monkeypatch):
    for chave, valor in dict(mix_ativo=True, mix_janela_posts=40, mix_forca=1.5, mix_tecnologia_preco_max=300.0,
                             mix_eletro_exige_queda=True, mix_metas=dict(METAS), mix_sem_categoria=0).items():
        monkeypatch.setattr(mix.config, chave, valor)


def oferta(titulo, preco=100.0, score=0.7, faixa="campeao", verificado=False, id_produto=None):
    return Oferta("mercadolivre", id_produto or titulo, titulo, "x", preco=preco, score=score, faixa=faixa,
                  nota=4.9, vendas=50_000, desconto_verificado=verificado)


# ── categoria de cada produto ────────────────────────────────────────

@pytest.mark.parametrize("titulo, categoria", [
    ("Chuveiro Lorenzetti Advanced 7500W", "casa_e_cozinha"),
    ("Panela de Pressão Tramontina 4,5L", "casa_e_cozinha"),
    ("Tênis Masculino Kappa Park 2.0", "moda"),
    ("Perfume Natura Kaiak 100ml", "beleza"),
    ("Papel Higiênico Supra Folha Tripla 24 Rolos", "limpeza_e_higiene"),
    ("Amaciante Comfort 2L", "limpeza_e_higiene"),
    ("Anker Soundcore P20i Fone de Ouvido Bluetooth", "tecnologia"),
    ("TP-Link Tapo C200 Câmera de Segurança Wifi", "tecnologia"),
    ("Creatina Monohidratada Dark Lab 500g", "esporte"),
    ("Medidor de Pressão Arterial de Pulso Digital", "saude"),
    ("Fralda Pampers Confort Sec M 30 unidades", "brinquedos_e_bebes"),
    ("Air Fryer Mondial 4 Litros", "eletrodomesticos"),
    ("Samsung Galaxy A57 5G 128GB", "outros"),                     # celular: comparado demais
    ("Smart TV 50 polegadas LG", "outros"),                        # TV/notebook/monitor/tablet não são "acessórios"
    ("Sociedade do cansaço", None),                                # sem tipo: sem categoria
])
def test_categoria_do_produto(titulo, categoria):
    assert categoria_do_produto(titulo) == categoria


def test_todo_tipo_tem_categoria_e_nenhuma_categoria_aponta_para_tipo_inexistente():
    de_tipos = {t for ts in CATEGORIAS.values() for t in ts}
    assert set(TIPOS) <= de_tipos
    assert de_tipos <= set(TIPOS)


# ── metas e bônus ────────────────────────────────────────────────────

def test_metas_normalizadas_somam_um():
    m = mix.metas()
    assert pytest.approx(sum(m.values())) == 1.0
    assert m["casa_e_cozinha"] == pytest.approx(0.22)


def test_metas_normalizam_mesmo_que_nao_somem_100(monkeypatch):
    monkeypatch.setattr(mix.config, "mix_metas", {"moda": 30, "beleza": 10})
    assert mix.metas() == {"moda": pytest.approx(0.75), "beleza": pytest.approx(0.25)}


def test_bonus_positivo_abaixo_da_meta_e_negativo_acima():
    casa = oferta("Panela Tramontina")
    assert mix.bonus(casa, Counter()) == pytest.approx(1.5 * 0.22)                       # nada postado: bônus cheio
    assert mix.bonus(casa, Counter(casa_e_cozinha=8, moda=2)) < 0                        # 80% de casa: passou da meta
    assert mix.bonus(casa, Counter(casa_e_cozinha=22, moda=78)) == pytest.approx(0)      # exatamente na meta


def test_produto_sem_categoria_sem_fatia_configurada_nao_ganha_nem_perde():
    assert mix.bonus(oferta("Sociedade do cansaço"), Counter(moda=10)) == 0


def test_sem_categoria_tem_fatia_propria_e_limitada(monkeypatch):
    monkeypatch.setattr(mix.config, "mix_sem_categoria", 8)
    m = mix.metas()
    assert m[mix.SEM_CATEGORIA] == pytest.approx(8 / 108) and pytest.approx(sum(m.values())) == 1.0
    livro = oferta("Sociedade do cansaço")
    assert mix.bonus(livro, Counter(moda=10)) > 0                                  # nenhum ainda: abaixo da fatia
    assert mix.bonus(livro, Counter(moda=5, sem_categoria=5)) < 0                  # 50% de sem categoria: passou


def test_livro_da_amazon_e_reconhecido_pelo_asin():
    livro = Oferta("amazon", "8543111536", "As vacas não me olham mais na cara", "x", preco=30.0)
    assert categoria_do_produto(livro.titulo, livro.uid) == "outros"               # ISBN-10: é livro
    assert categoria_do_produto(livro.titulo) is None                              # só pelo título não dá
    produto = Oferta("amazon", "B077C3VFR5", "As vacas não me olham mais na cara", "x", preco=30.0)
    assert categoria_do_produto(produto.titulo, produto.uid) is None               # ASIN normal: não é livro


def test_contagem_recente_classifica_livros_da_amazon_pelo_uid(monkeypatch):
    db.registrar(Oferta("amazon", "8543111536", "As vacas não me olham mais na cara", "x", preco=30.0))
    assert mix.contagem_recente() == Counter(outros=1)


def test_mix_desligado_nao_mexe_em_nada(monkeypatch):
    monkeypatch.setattr(mix.config, "mix_ativo", False)
    assert mix.bonus(oferta("Panela Tramontina"), Counter()) == 0
    assert mix.motivo_de_exclusao(oferta("Fone Bluetooth JBL", preco=9999)) is None


# ── regras das categorias com restrição ──────────────────────────────

def test_tecnologia_tem_teto_de_preco():
    assert mix.motivo_de_exclusao(oferta("Fone Bluetooth JBL Tune 510BT", preco=299.0)) is None
    assert mix.motivo_de_exclusao(oferta("Fone Bluetooth JBL Tune 510BT", preco=650.0)).startswith("tecnologia acima do teto")


def test_teto_de_tecnologia_nao_vale_para_outras_categorias():
    assert mix.motivo_de_exclusao(oferta("Panela de Pressão Tramontina", preco=650.0)) is None


def test_eletrodomestico_so_com_queda_comprovada():
    assert mix.motivo_de_exclusao(oferta("Air Fryer Mondial 4 Litros", verificado=False)) == \
        "eletrodoméstico sem queda de preço comprovada"
    assert mix.motivo_de_exclusao(oferta("Air Fryer Mondial 4 Litros", verificado=True)) is None


def test_eletrodomestico_sem_a_exigencia_configurada(monkeypatch):
    monkeypatch.setattr(mix.config, "mix_eletro_exige_queda", False)
    assert mix.motivo_de_exclusao(oferta("Air Fryer Mondial 4 Litros")) is None


def test_filtro_aplica_as_regras_e_conta_os_motivos():
    ofertas = [
        Oferta("mercadolivre", "1", "Fone Bluetooth JBL Tune 510BT", "x", preco=650.0, nota=4.9, vendas=50_000),
        Oferta("mercadolivre", "2", "Air Fryer Mondial 4 Litros", "x", preco=300.0, nota=4.9, vendas=50_000),
        Oferta("mercadolivre", "3", "Panela de Pressão Tramontina", "x", preco=90.0, nota=4.9, vendas=50_000),
    ]
    aprovadas, rejeicoes = pipeline.filtrar_detalhado(ofertas)
    assert [o.id_produto for o in aprovadas] == ["3"]
    assert rejeicoes["tecnologia acima do teto"] == 1
    assert rejeicoes["eletrodoméstico sem queda de preço comprovada"] == 1


# ── escolha guiada pelo mix ──────────────────────────────────────────

def test_sem_historico_as_categorias_maiores_vem_primeiro():
    ofertas = [oferta("Perfume Natura Kaiak 100ml"), oferta("Tênis Kappa Park"), oferta("Panela Tramontina"),
               oferta("Creatina Dark Lab 500g")]
    escolhidas = pipeline.escolher(ofertas, 3)
    # os posts saem intercalados entre as faixas, então a ordem final não é a da escolha: o que vale é quem entra
    assert {categoria_do_produto(o.titulo) for o in escolhidas} == {"casa_e_cozinha", "moda", "beleza"}


def test_categoria_saturada_perde_a_vez():
    ofertas = [oferta("Panela Tramontina", score=0.75), oferta("Tênis Kappa Park", score=0.70)]
    recentes = Counter(casa_e_cozinha=30, moda=1)                 # casa já passou muito da meta
    assert pipeline.escolher(ofertas, 1, recentes)[0].titulo == "Tênis Kappa Park"


def test_score_muito_melhor_ainda_vence_o_mix():
    """O mix é preferência, não cota rígida: uma oferta bem melhor de categoria em excesso não é descartada."""
    ofertas = [oferta("Panela Tramontina", score=0.95), oferta("Tênis Kappa Park", score=0.30)]
    recentes = Counter(casa_e_cozinha=25, moda=15)                # casa levemente acima da meta
    assert pipeline.escolher(ofertas, 1, recentes)[0].titulo == "Panela Tramontina"


def test_escolher_nao_modifica_a_contagem_recebida():
    recentes = Counter(moda=5)
    pipeline.escolher([oferta("Panela Tramontina")], 1, recentes)
    assert recentes == Counter(moda=5)


def test_produto_sem_categoria_so_entra_se_as_categorias_estiverem_em_dia():
    ofertas = [oferta("Sociedade do cansaço", score=0.72), oferta("Panela Tramontina", score=0.70)]
    assert pipeline.escolher(ofertas, 1)[0].titulo == "Panela Tramontina"     # casa tem bônus: passa na frente


def test_categorias_do_ciclo_se_ajustam_a_cada_vaga():
    """5 vagas, tudo com o mesmo score: a mistura do ciclo já acompanha as metas (não 5 da mesma categoria)."""
    ofertas = [oferta(t) for t in ("Panela Tramontina", "Chuveiro Lorenzetti", "Garrafa Térmica Stanley",
                                  "Tênis Kappa Park", "Camiseta Nike Dry", "Perfume Natura Kaiak",
                                  "Creatina Dark Lab", "Papel Higiênico 12 Rolos")]
    cats = Counter(categoria_do_produto(o.titulo) for o in pipeline.escolher(ofertas, 5))
    assert cats["casa_e_cozinha"] <= 2 and len(cats) >= 3


# ── o mix ao longo do tempo ──────────────────────────────────────────

def _pool_de_todas_as_categorias():
    """Um candidato por tipo do dicionário, todos com o mesmo score: só o mix decide."""
    pool = []
    for categoria, tipos in CATEGORIAS.items():
        for tipo in tipos:
            titulo = f"{TIPOS[tipo][0].capitalize()} Marca Modelo X"
            assert categoria_do_produto(titulo) == categoria, (titulo, categoria)     # o gerador acerta a categoria
            pool.append(oferta(titulo, id_produto=f"{categoria}-{tipo}"))
    return pool


def test_a_composicao_converge_para_a_tabela_de_metas():
    pool = _pool_de_todas_as_categorias()
    janela: list[str] = []
    total = Counter()
    for _ in range(160):
        recentes = Counter(janela[-40:])
        for o in pipeline.escolher(pool, 5, recentes):
            cat = categoria_do_produto(o.titulo)
            janela.append(cat)
            total[cat] += 1
    n = sum(total.values())
    for categoria, meta in METAS.items():
        fatia = 100 * total[categoria] / n
        assert abs(fatia - meta) <= 4, f"{categoria}: {fatia:.1f}% (meta {meta}%)"
    assert total["outros"] / n < 0.06                # os "comparados demais" ficam raros


# ── com o banco (posts reais registrados) ────────────────────────────

def _postada_ha(titulo, id_produto, horas):
    o = Oferta("mercadolivre", id_produto, titulo, "x", preco=50.0)
    db.registrar(o)
    with db._conn() as c:
        quando = (dt.datetime.now() - dt.timedelta(hours=horas)).isoformat(timespec="seconds")
        c.execute("UPDATE postadas SET postada_em = ? WHERE uid = ?", (quando, o.uid))


def test_contagem_recente_le_os_ultimos_posts_do_banco(monkeypatch):
    monkeypatch.setattr(mix.config, "mix_janela_posts", 3)
    _postada_ha("Panela Tramontina Antiaderente", "1", 30)         # fora da janela de 3 posts
    _postada_ha("Tênis Kappa Park", "2", 20)
    _postada_ha("Camiseta Nike Dry Fit", "3", 10)
    _postada_ha("Sociedade do cansaço", "4", 5)                    # sem tipo: conta como "sem_categoria"
    _postada_ha("Perfume Natura Kaiak", "5", 1)
    assert mix.contagem_recente() == Counter(moda=1, beleza=1, sem_categoria=1)


def test_selecionar_usa_o_historico_de_posts_para_equilibrar(monkeypatch):
    monkeypatch.setattr(pipeline.config, "mix_ativo", True)
    for i in range(12):                                            # o canal andou postando só casa e cozinha
        _postada_ha(f"Organizador de Gavetas Modelo {i}", f"c{i}", 50 + i)
    pool = [
        Oferta("mercadolivre", "n1", "Panela Tramontina Antiaderente", "x", preco=90.0, nota=4.9, vendas=50_000),
        Oferta("mercadolivre", "n2", "Tênis Masculino Kappa Park 2.0", "x", preco=90.0, nota=4.9, vendas=50_000),
    ]
    escolhidas, _ = pipeline.selecionar(pool, 1, checar_vendedores=False)
    assert escolhidas[0].id_produto == "n2"                        # moda está abaixo da meta; casa já passou
