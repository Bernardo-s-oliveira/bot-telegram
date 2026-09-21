import pytest

from ofertas import db
from ofertas.config import config


@pytest.fixture(autouse=True)
def amazon_sem_pausa(monkeypatch, tmp_path):
    """Uma pausa por bloqueio da Amazon num teste não pode vazar para os outros."""
    from ofertas.sources import amazon
    monkeypatch.setattr(amazon, "_pausa_ate", 0.0)
    monkeypatch.setattr(amazon, "DATA_DIR", tmp_path)   # páginas de depuração vão para a pasta do teste, não para data/


@pytest.fixture(autouse=True)
def banco_isolado(tmp_path, monkeypatch):
    """Cada teste usa um SQLite próprio (nunca toca em data/ofertas.db)."""
    monkeypatch.setattr(db, "_DB", tmp_path / "teste.db")


@pytest.fixture(autouse=True)
def config_padrao(monkeypatch):
    """Valores fixos, independentes do config.yaml de quem roda os testes."""
    for chave, valor in dict(
        exigir_avaliacao=True, nota_minima=4.3, prova_social_minima=20,
        historico_dias=30, repostar_queda_pct=10, vendas_mensal_para_total=6,
        campeoes_vendas_minimas=1000, campeoes_nota_minima=4.5,
        campeoes_pct_posts=50, desconto_minimo=25, preco_minimo=0, preco_maximo=0,
        desconto_suspeito=60, suspeita_vendas_minimas=5000,
        preco_minimo_vs_mercado_pct=50, verificar_vendedor=True, vendedor_nivel_minimo=4, buscar_cupons=True, evitar_internacional=True, pedidos_ativo=True, pedidos_arquivo="nao_existe_nos_testes.yaml", pedidos_max_por_ciclo=2, pedidos_selo="📌 Pedido de cliente",
        variedade_janela_horas=4, apple_ativo=True, apple_max_posts=3, apple_queda_minima=5, apple_so_queda=True, apple_palavras_bloqueadas=["recondicionado", "seminovo", "usado", "vitrine", "open box", "caixa aberta", "swap"], chat_id_apple="", mix_ativo=False, mix_janela_posts=40, mix_forca=1.5, mix_tecnologia_preco_max=300.0, mix_eletro_exige_queda=True, divulgacao_ativa=True, divulgacao_a_cada_horas=24, divulgacao_fixar=False,
        divulgacao_texto="Divulgação https://t.me/cacador_promo #cacador_promo", chat_id="@canal",
        palavras_bloqueadas=[], nao_repetir_dias=7,
    ).items():
        monkeypatch.setattr(config, chave, valor)
