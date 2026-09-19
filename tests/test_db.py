import datetime as dt

from ofertas import db
from ofertas.models import Oferta

AGORA = dt.datetime(2026, 9, 19, 12, 0)


def oferta(preco, id_produto="MLB1"):
    return Oferta(plataforma="mercadolivre", id_produto=id_produto, titulo="Produto", url_afiliado="x", preco=preco)


def test_snapshot_so_grava_quando_muda_ou_passa_o_batimento():
    assert db.registrar_precos([oferta(100.0)], AGORA) == 1
    assert db.registrar_precos([oferta(100.2)], AGORA + dt.timedelta(hours=1)) == 0   # variação < 0,5%
    assert db.registrar_precos([oferta(90.0)], AGORA + dt.timedelta(hours=2)) == 1    # mudou
    assert db.registrar_precos([oferta(90.0)], AGORA + dt.timedelta(hours=9)) == 1    # batimento de 6h


def test_historico_usa_mediana_ponderada_pelo_tempo():
    # 10 dias a R$ 100, depois 1 dia a R$ 60 (promoção): o preço "normal" é 100, o mínimo é 60
    db.registrar_precos([oferta(100.0)], AGORA - dt.timedelta(days=11))
    db.registrar_precos([oferta(100.0)], AGORA - dt.timedelta(days=6))
    db.registrar_precos([oferta(60.0)], AGORA - dt.timedelta(days=1))
    h = db.historico(["mercadolivre:MLB1"], dias=30, agora=AGORA)["mercadolivre:MLB1"]
    assert h.referencia == 100.0
    assert h.minimo == 60.0
    assert h.dias > 10
    assert h.suficiente(min_amostras=3)


def test_historico_ignora_registros_fora_da_janela_e_produto_sem_registro():
    db.registrar_precos([oferta(100.0)], AGORA - dt.timedelta(days=50))
    assert db.historico(["mercadolivre:MLB1", "mercadolivre:MLB2"], dias=30, agora=AGORA) == {}


def test_historico_novo_nao_e_suficiente():
    db.registrar_precos([oferta(100.0)], AGORA - dt.timedelta(hours=2))
    h = db.historico(["mercadolivre:MLB1"], agora=AGORA)["mercadolivre:MLB1"]
    assert not h.suficiente()


def test_ultimas_postagens_e_registro():
    o = oferta(50.0)
    assert db.ultimas_postagens([o.uid]) == {}
    db.registrar(o)
    (quando, preco), = db.ultimas_postagens([o.uid]).values()
    assert preco == 50.0
    assert db.ja_postada(o.uid, 7)
    assert db.total_postadas() == 1
