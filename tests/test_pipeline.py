import datetime as dt

from ofertas import db, pipeline
from ofertas.models import Oferta


def aprovada(titulo, score, faixa, plataforma="mercadolivre", id_produto=None):
    return Oferta(plataforma=plataforma, id_produto=id_produto or titulo, titulo=titulo, url_afiliado="x",
                  preco=100.0, score=score, faixa=faixa)


# ── escolher ─────────────────────────────────────────────────────────

def test_metade_das_vagas_para_campeoes_e_posts_intercalados():
    ofertas = [aprovada(f"Campeão numero{i} exclusivo", 0.9 - i / 100, "campeao") for i in range(3)]
    ofertas += [aprovada(f"Desconto artigo{i} diferente", 0.8 - i / 100, "desconto") for i in range(3)]
    escolhidas = pipeline.escolher(ofertas, 4)
    assert [o.faixa for o in escolhidas] == ["campeao", "desconto", "campeao", "desconto"]


def test_faixa_sem_candidatas_cede_a_vaga():
    ofertas = [aprovada(f"Desconto artigo{i} diferente", 0.8 - i / 100, "desconto") for i in range(5)]
    assert len(pipeline.escolher(ofertas, 4)) == 4


def test_escolhe_menos_que_o_maximo_se_faltar_oferta_boa():
    assert len(pipeline.escolher([aprovada("Único produto bom", 0.9, "campeao")], 5)) == 1


def test_variacoes_do_mesmo_produto_so_entram_uma_vez():
    ofertas = [
        aprovada("Fone Bluetooth JBL Tune 510BT Preto", 0.9, "campeao", id_produto="a"),
        aprovada("Fone Bluetooth JBL Tune 510BT Branco", 0.8, "campeao", id_produto="b"),
        aprovada("Air Fryer Mondial 4 Litros Digital", 0.7, "campeao", id_produto="c"),
    ]
    titulos = [o.titulo for o in pipeline.escolher(ofertas, 3)]
    assert "Fone Bluetooth JBL Tune 510BT Preto" in titulos
    assert "Fone Bluetooth JBL Tune 510BT Branco" not in titulos


def test_produtos_genericos_diferentes_nao_sao_confundidos():
    ofertas = [
        aprovada("Fone de Ouvido Bluetooth Sem Fio TWS", 0.9, "campeao", id_produto="a"),
        aprovada("Fone de Ouvido Bluetooth Gamer RGB", 0.8, "campeao", id_produto="b"),
    ]
    assert len(pipeline.escolher(ofertas, 2)) == 2


def test_nenhuma_plataforma_domina_quando_ha_alternativa():
    ml = [aprovada(f"Item mercado{i} unico", 0.9 - i / 100, "desconto", "mercadolivre") for i in range(5)]
    amz = [aprovada(f"Item amazon{i} unico", 0.5 - i / 100, "desconto", "amazon") for i in range(3)]
    escolhidas = pipeline.escolher(ml + amz, 5)   # teto = 60% de 5 = 3 posts por plataforma
    assert sum(o.plataforma == "mercadolivre" for o in escolhidas) == 3
    assert sum(o.plataforma == "amazon" for o in escolhidas) == 2


def test_teto_por_plataforma_e_relaxado_se_faltar_oferta():
    ml = [aprovada(f"Item mercado{i} unico", 0.9 - i / 100, "desconto", "mercadolivre") for i in range(5)]
    assert len(pipeline.escolher(ml, 5)) == 5


# ── filtrar (repostagem) ─────────────────────────────────────────────

def boa(preco, id_produto="MLB1"):
    return Oferta(plataforma="mercadolivre", id_produto=id_produto, titulo="Produto muito bom", url_afiliado="x",
                  preco=preco, preco_original=preco * 2, nota=4.9, vendas=50_000)


def _postada_ha(o: Oferta, dias: float):
    db.registrar(o)
    with db._conn() as c:
        quando = (dt.datetime.now() - dt.timedelta(days=dias)).isoformat(timespec="seconds")
        c.execute("UPDATE postadas SET postada_em = ? WHERE uid = ?", (quando, o.uid))


def test_produto_recem_postado_nao_repete():
    o = boa(100.0)
    _postada_ha(o, 0.5)
    aprovadas, rejeicoes = pipeline.filtrar_detalhado([boa(100.0)])
    assert aprovadas == [] and rejeicoes["já postada"] == 1


def test_produto_volta_se_o_preco_caiu_bastante():
    _postada_ha(boa(100.0), 2)
    aprovadas, _ = pipeline.filtrar_detalhado([boa(85.0)])   # -15% desde o último post
    assert len(aprovadas) == 1
    assert any("De volta e mais barato" in s for s in aprovadas[0].selos)


def test_queda_pequena_nao_reposta():
    _postada_ha(boa(100.0), 2)
    assert pipeline.filtrar_detalhado([boa(95.0)])[0] == []


def test_produto_volta_depois_do_prazo():
    _postada_ha(boa(100.0), 8)
    aprovadas, _ = pipeline.filtrar_detalhado([boa(100.0)])
    assert len(aprovadas) == 1 and not any("De volta" in s for s in aprovadas[0].selos)


def test_filtros_basicos_e_motivos(monkeypatch):
    monkeypatch.setattr(pipeline.config, "palavras_bloqueadas", ["capinha"])
    monkeypatch.setattr(pipeline.config, "preco_maximo", 500.0)
    ofertas = [
        Oferta("mercadolivre", "1", "Capinha de celular", "x", preco=10.0, nota=4.9, vendas=9999),
        Oferta("mercadolivre", "2", "Notebook caro", "x", preco=5000.0, nota=4.9, vendas=9999),
        Oferta("mercadolivre", "3", "Produto sem nota", "x", preco=50.0, preco_original=100.0),
        boa(80.0, id_produto="4"),
    ]
    aprovadas, rejeicoes = pipeline.filtrar_detalhado(ofertas)
    assert [o.id_produto for o in aprovadas] == ["4"]
    assert rejeicoes == {"palavra bloqueada": 1, "fora da faixa de preço": 1, "sem avaliação": 1}
