import pytest

from ofertas import db
from ofertas.config import config


@pytest.fixture(autouse=True)
def banco_isolado(tmp_path, monkeypatch):
    """Cada teste usa um SQLite próprio (nunca toca em data/ofertas.db)."""
    monkeypatch.setattr(db, "_DB", tmp_path / "teste.db")


@pytest.fixture(autouse=True)
def config_padrao(monkeypatch):
    """Valores fixos, independentes do config.yaml de quem roda os testes."""
    for chave, valor in dict(
        exigir_avaliacao=True, nota_minima=4.3, prova_social_minima=20, rejeitar_desconto_falso=True,
        historico_dias=30, repostar_queda_pct=10, vendas_mensal_para_total=6,
        campeoes_vendas_minimas=1000, campeoes_nota_minima=4.5, campeoes_desconto_minimo=10,
        campeoes_pct_posts=50, desconto_minimo=25, preco_minimo=0, preco_maximo=0,
        desconto_suspeito=60, desconto_max_sem_historico=50, suspeita_vendas_minimas=5000,
        preco_minimo_vs_mercado_pct=50, verificar_vendedor=True, vendedor_nivel_minimo=4, buscar_cupons=True,
        variedade_janela_horas=4, divulgacao_ativa=True, divulgacao_a_cada_horas=24, divulgacao_fixar=False,
        divulgacao_texto="Divulgação https://t.me/cacador_promo #cacador_promo", chat_id="@canal",
        palavras_bloqueadas=[], nao_repetir_dias=7,
    ).items():
        monkeypatch.setattr(config, chave, valor)
