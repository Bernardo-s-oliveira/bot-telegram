"""Variedade (não repetir o mesmo tipo de produto) e post periódico de divulgação."""
import asyncio
import datetime as dt

from ofertas import db, pipeline
from ofertas.models import Oferta


def oferta(titulo, id_produto=None, score=0.8, faixa="campeao", plataforma="mercadolivre"):
    return Oferta(plataforma, id_produto or titulo, titulo, "x", preco=100.0, preco_original=200.0,
                  nota=4.9, vendas=50_000, score=score, faixa=faixa)


def aprovavel(titulo, id_produto):
    """Passa nos filtros de qualidade (-50% anunciado, nota e vendas altas)."""
    return Oferta("mercadolivre", id_produto, titulo, "x", preco=100.0, preco_original=200.0, nota=4.9, vendas=50_000)


def postada_ha(o, horas):
    db.registrar(o)
    with db._conn() as c:
        quando = (dt.datetime.now() - dt.timedelta(hours=horas)).isoformat(timespec="seconds")
        c.execute("UPDATE postadas SET postada_em = ? WHERE uid = ?", (quando, o.uid))


# ── dentro do ciclo ──────────────────────────────────────────────────

def test_no_maximo_um_produto_de_cada_tipo_por_ciclo():
    """O caso do feedback: 3 cremes de creatina, 2 chuveiros, 2 balanças e 2 fones entre os melhores."""
    ofertas = [
        oferta("Creatina Monohidratada Dark Lab 500g", score=0.95),
        oferta("Creatina Pura Growth Supplements 1kg", score=0.94),
        oferta("Creatina Creapure Max Titanium 300g", score=0.93),
        oferta("Chuveiro Lorenzetti Advanced 7500W", score=0.92),
        oferta("Chuveiro Hydra Ducha Corona 5500W", score=0.91),
        oferta("Balança Digital de Cozinha Multilaser 10kg", score=0.90),
        oferta("Balança Corporal Bioimpedância Bluetooth", score=0.89),
        oferta("Fone Bluetooth JBL Tune 510BT Preto", score=0.88),
        oferta("Anker Soundcore P20i Fone de Ouvido", score=0.87),
    ]
    escolhidas = pipeline.escolher(ofertas, 8)
    tipos = [pipeline.tipo_do_produto(o.titulo) for o in escolhidas]
    assert sorted(tipos) == ["balanca", "chuveiro", "creatina", "fone"]
    assert escolhidas[0].titulo.startswith("Creatina Monohidratada")      # de cada tipo, o melhor


def test_tipo_vale_entre_as_faixas_do_ciclo():
    ofertas = [oferta("Creatina Monohidratada Dark Lab 500g", faixa="campeao"),
               oferta("Creatina Pura Growth Supplements 1kg", faixa="desconto")]
    assert len(pipeline.escolher(ofertas, 4)) == 1


def test_produto_sem_tipo_conhecido_nao_sofre_a_regra():
    ofertas = [oferta("Sociedade do cansaço", score=0.9), oferta("Murdoku: 80 mistérios para resolver", score=0.8)]
    assert len(pipeline.escolher(ofertas, 4)) == 2


# ── entre ciclos (janela de horas) ───────────────────────────────────

def test_tipo_postado_ha_pouco_fica_de_fora():
    postada_ha(aprovavel("Creatina Monohidratada Dark Lab 500g", "1"), horas=1)
    aprovadas, rejeicoes = pipeline.filtrar_detalhado([
        aprovavel("Creatina Pura Growth Supplements 1kg", "2"),
        aprovavel("Chuveiro Lorenzetti Advanced 7500W", "3"),
    ])
    assert [o.id_produto for o in aprovadas] == ["3"]
    assert rejeicoes["tipo postado há pouco"] == 1


def test_tipo_liberado_depois_da_janela():
    postada_ha(aprovavel("Creatina Monohidratada Dark Lab 500g", "1"), horas=5)      # janela é de 4h
    aprovadas, _ = pipeline.filtrar_detalhado([aprovavel("Creatina Pura Growth Supplements 1kg", "2")])
    assert [o.id_produto for o in aprovadas] == ["2"]


def test_janela_zero_desliga_a_regra_entre_ciclos(monkeypatch):
    monkeypatch.setattr(pipeline.config, "variedade_janela_horas", 0)
    postada_ha(aprovavel("Creatina Monohidratada Dark Lab 500g", "1"), horas=0.1)
    aprovadas, _ = pipeline.filtrar_detalhado([aprovavel("Creatina Pura Growth Supplements 1kg", "2")])
    assert len(aprovadas) == 1


def test_repostagem_por_queda_de_preco_e_excecao_a_regra_de_tipo(monkeypatch):
    """O mesmo produto volta mais barato: é o mesmo tipo, mas o que importa é o preço."""
    monkeypatch.setattr(pipeline.config, "repostar_queda_pct", 10)
    antigo = Oferta("mercadolivre", "1", "Creatina Monohidratada Dark Lab 500g", "x", preco=100.0)
    postada_ha(antigo, horas=30)      # fora da janela de tipo, mas dentro dos 7 dias de "já postada"
    postada_ha(Oferta("mercadolivre", "9", "Creatina Outra Marca 300g", "x", preco=50.0), horas=1)  # tipo na janela
    barato = Oferta("mercadolivre", "1", "Creatina Monohidratada Dark Lab 500g", "x", preco=80.0, preco_original=130.0,
                    nota=4.9, vendas=50_000)      # -38%: dentro do limite para desconto sem histórico
    aprovadas, _ = pipeline.filtrar_detalhado([barato])
    assert [o.id_produto for o in aprovadas] == ["1"]


# ── divulgação periódica ─────────────────────────────────────────────

class BotFalso:
    def __init__(self, falha_envio=False, falha_pin=False):
        self.enviadas, self.fixadas = [], []
        self.falha_envio, self.falha_pin = falha_envio, falha_pin

    async def send_message(self, chat_id, texto, **kw):
        if self.falha_envio:
            raise RuntimeError("telegram fora do ar")
        self.enviadas.append((chat_id, texto, kw))
        return type("Msg", (), {"message_id": 42})()

    async def pin_chat_message(self, chat_id, message_id, **kw):
        if self.falha_pin:
            raise RuntimeError("sem permissão")
        self.fixadas.append(message_id)


AGORA = dt.datetime(2026, 9, 19, 12, 0)


def divulgar(bot, agora=AGORA):
    return asyncio.run(pipeline.divulgar_canal(bot, agora))


def test_divulgacao_sai_uma_vez_por_periodo():
    bot = BotFalso()
    assert divulgar(bot) is True
    assert divulgar(bot, AGORA + dt.timedelta(hours=23)) is False
    assert divulgar(bot, AGORA + dt.timedelta(hours=25)) is True
    assert len(bot.enviadas) == 2
    chat, texto, kw = bot.enviadas[0]
    assert chat == "@canal" and "t.me/cacador_promo" in texto and kw["disable_web_page_preview"] is True


def test_divulgacao_desligada_ou_sem_texto_nao_envia(monkeypatch):
    bot = BotFalso()
    monkeypatch.setattr(pipeline.config, "divulgacao_ativa", False)
    assert divulgar(bot) is False
    monkeypatch.setattr(pipeline.config, "divulgacao_ativa", True)
    monkeypatch.setattr(pipeline.config, "divulgacao_texto", "")
    assert divulgar(bot) is False and bot.enviadas == []


def test_falha_no_envio_nao_marca_como_publicada():
    assert divulgar(BotFalso(falha_envio=True)) is False
    bot = BotFalso()
    assert divulgar(bot, AGORA + dt.timedelta(minutes=5)) is True      # tenta de novo no próximo ciclo


def test_divulgacao_pode_fixar_e_falha_ao_fixar_nao_derruba(monkeypatch):
    monkeypatch.setattr(pipeline.config, "divulgacao_fixar", True)
    bot = BotFalso()
    assert divulgar(bot) is True and bot.fixadas == [42]
    assert divulgar(BotFalso(falha_pin=True), AGORA + dt.timedelta(hours=30)) is True


def _ciclo(monkeypatch, escolhidas):
    async def postar_falso(bot, o, chat_id, teclado=None):
        return None
    monkeypatch.setattr(pipeline, "coletar", lambda: [])
    monkeypatch.setattr(pipeline, "dentro_do_horario", lambda: True)
    monkeypatch.setattr(pipeline, "selecionar", lambda *a, **k: (escolhidas, {}))
    monkeypatch.setattr(pipeline, "postar_oferta", postar_falso)
    bot = BotFalso()
    return bot, asyncio.run(pipeline.executar_ciclo(bot))


def test_ciclo_com_posts_publica_a_divulgacao_depois_das_ofertas(monkeypatch):
    o = oferta("Chuveiro Lorenzetti Advanced 7500W")
    o.url_afiliado = "https://exemplo/afiliado"
    bot, postadas = _ciclo(monkeypatch, [o])
    assert postadas == 1 and len(bot.enviadas) == 1 and "t.me/cacador_promo" in bot.enviadas[0][1]


def test_ciclo_sem_posts_nao_publica_divulgacao(monkeypatch):
    bot, postadas = _ciclo(monkeypatch, [])
    assert postadas == 0 and bot.enviadas == []


# ── coleta fora do horário ativo ──────────────────────────────────────

def test_fora_do_horario_ainda_coleta_e_grava_o_historico_mas_nao_posta(monkeypatch):
    """O histórico de preço deve crescer o dia inteiro, não só na janela em que o bot posta."""
    o = aprovavel("Chuveiro Lorenzetti Advanced 7500W", "1")
    monkeypatch.setattr(pipeline, "coletar", lambda: [o])
    monkeypatch.setattr(pipeline, "dentro_do_horario", lambda: False)

    def selecionar_nao_deveria_rodar(*a, **k):
        raise AssertionError("fora do horário não deveria escolher nem checar vendedor")
    monkeypatch.setattr(pipeline, "selecionar", selecionar_nao_deveria_rodar)
    bot = BotFalso()

    assert asyncio.run(pipeline.executar_ciclo(bot)) == 0
    assert bot.enviadas == []
    assert db.historico([o.uid])[o.uid].amostras == 1
