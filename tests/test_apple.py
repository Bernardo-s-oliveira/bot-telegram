"""Grupo Apple: produto Apple vai para o grupo Apple, o resto para o canal geral."""
import asyncio
import datetime as dt
import sqlite3

import pytest

from ofertas import db, destinos, mix, pipeline
from ofertas.db import Historico
from ofertas.models import Oferta

CANAL, GRUPO_APPLE = "@canal", "-1001234567890"


@pytest.fixture
def com_grupo_apple(monkeypatch):
    monkeypatch.setattr(destinos.config, "chat_id_apple", GRUPO_APPLE)


def oferta(titulo, preco=100.0, plataforma="mercadolivre", id_produto=None, vendedor=None, **kw):
    return Oferta(plataforma, id_produto or titulo, titulo, "https://afiliado/x", preco=preco, nota=4.9,
                  vendas=50_000, vendedor=vendedor, **kw)


# ── o que é produto Apple ────────────────────────────────────────────

@pytest.mark.parametrize("titulo", [
    "Apple iPhone 15 128GB Preto",
    "iPhone 15 Pro Max 256GB - Titânio Natural",
    "Smartphone Apple iPhone 13 128GB",
    "Apple Watch Series 9 GPS 41mm",
    "AirPods Pro 2ª Geração com Estojo MagSafe",
    "MacBook Air 13 M2 256GB",
    "iPad 10ª Geração 64GB Wi-Fi",
    "Apple AirTag 1 Unidade",
    "Carregador Apple USB-C 20W",                    # acessório da própria Apple
    "Apple Pencil 2ª Geração",
    "Magic Keyboard Apple com Touch ID",
    "Apple iPhone 15 com Capa e Película",           # começa por "Apple": é o produto, a capa é brinde
    "iPhone 14 128GB Recondicionado",                # é Apple (o filtro de recondicionados vem depois)
])
def test_e_produto_apple(titulo):
    assert destinos.e_apple(oferta(titulo)) is True


@pytest.mark.parametrize("titulo", [
    "Capa para iPhone 15 Pro Silicone",              # acessório de terceiros
    "Película de Vidro para iPad 10ª Geração",
    "Pulseira para Apple Watch 45mm Silicone",
    "Suporte Magnético Compatível com iPhone",
    "Cabo USB-C para iPhone 15 1 Metro",
    "Carregador Turbo 20W Compatível com iPhone",
    "Samsung Galaxy S24 256GB",
    "Fone Bluetooth Xiaomi Redmi Buds 5",
    "Notebook Dell Inspiron 15",
    "Capa Silicone iPhone 15 Original",              # limitação conhecida: começa por "Capa", tratada como de terceiros
    "",
])
def test_nao_e_produto_apple(titulo):
    assert destinos.e_apple(oferta(titulo)) is False


def test_vendedor_apple_conta_como_produto_apple():
    assert destinos.e_apple(oferta("Cabo USB-C Lightning 1 Metro", vendedor="Apple")) is True
    assert destinos.e_apple(oferta("Cabo USB-C Lightning 1 Metro", vendedor="Loja do Zé")) is False


# ── divisão entre destinos ───────────────────────────────────────────

def test_sem_grupo_configurado_tudo_vai_para_o_canal_geral():
    grupos = destinos.dividir([oferta("Apple iPhone 15 128GB"), oferta("Panela Tramontina")])
    assert len(grupos["geral"]) == 2 and grupos["apple"] == []
    assert [d.nome for d in destinos.ativos()] == ["geral"]


def test_com_grupo_apple_divide_por_produto(com_grupo_apple):
    grupos = destinos.dividir([oferta("Apple iPhone 15 128GB"), oferta("Panela Tramontina"),
                               oferta("Capa para iPhone 15"), oferta("AirPods Pro 2")])
    assert [o.titulo for o in grupos["apple"]] == ["Apple iPhone 15 128GB", "AirPods Pro 2"]
    assert [o.titulo for o in grupos["geral"]] == ["Panela Tramontina", "Capa para iPhone 15"]
    assert [d.nome for d in destinos.ativos()] == ["geral", "apple"]


def test_grupo_apple_desligado_no_config_volta_tudo_para_o_geral(com_grupo_apple, monkeypatch):
    monkeypatch.setattr(destinos.config, "apple_ativo", False)
    assert destinos.apple() is None
    assert len(destinos.dividir([oferta("Apple iPhone 15 128GB")])["geral"]) == 1


def test_chat_para_oferta_avulsa(com_grupo_apple):
    assert destinos.chat_para(oferta("Apple iPhone 15 128GB")) == ("apple", GRUPO_APPLE)
    assert destinos.chat_para(oferta("Panela Tramontina")) == ("geral", CANAL)


def test_chat_para_sem_grupo_apple_usa_o_canal():
    assert destinos.chat_para(oferta("Apple iPhone 15 128GB")) == ("geral", CANAL)


def test_regras_do_destino_apple(com_grupo_apple):
    d = destinos.apple()
    assert (d.nome, d.chat_id, d.max_posts, d.usar_mix, d.queda_minima, d.so_queda) == \
        ("apple", GRUPO_APPLE, 3, False, 5, True)
    assert "recondicionado" in d.palavras_bloqueadas


# ── seleção do destino Apple: só queda de preço comprovada ───────────

def registrar_historico(o, preco_normal, dias=(11, 8, 5, 2)):
    agora = dt.datetime.now()
    for d in dias:
        db.registrar_precos([Oferta(o.plataforma, o.id_produto, o.titulo, "x", preco=preco_normal)],
                            agora - dt.timedelta(days=d))


def test_apple_sem_historico_nao_posta_nada(com_grupo_apple):
    o = oferta("Apple iPhone 15 128GB Preto", preco=4500.0)
    aprovadas, rejeicoes = pipeline.filtrar_detalhado([o], destinos.apple())
    assert aprovadas == [] and rejeicoes["critérios não atingidos"] == 1     # sem "campeão": só queda comprovada


def test_apple_posta_queda_comprovada_a_partir_do_limite_proprio(com_grupo_apple):
    o = oferta("Apple iPhone 15 128GB Preto", preco=4500.0)                   # queda de 10% (limite do Apple: 5%)
    registrar_historico(o, 5000.0)
    aprovadas, _ = pipeline.filtrar_detalhado([o], destinos.apple())
    assert [a.faixa for a in aprovadas] == ["desconto"] and aprovadas[0].desconto == 10


def test_apple_queda_pequena_nao_passa(com_grupo_apple):
    o = oferta("Apple iPhone 15 128GB Preto", preco=4800.0)                   # -4%
    registrar_historico(o, 5000.0)
    assert pipeline.filtrar_detalhado([o], destinos.apple())[0] == []


def test_o_limite_de_queda_do_canal_geral_continua_o_mesmo(com_grupo_apple):
    o = oferta("Panela Tramontina Antiaderente", preco=90.0)                  # -10% no geral: abaixo dos 25%...
    registrar_historico(o, 100.0)
    aprovadas, _ = pipeline.filtrar_detalhado([o], destinos.geral())
    assert [a.faixa for a in aprovadas] == ["campeao"]                        # ...mas entra como campeão (muito vendida)


def test_apple_bloqueia_recondicionados_e_seminovos(com_grupo_apple):
    for titulo in ("iPhone 14 128GB Recondicionado", "Apple iPhone 13 Seminovo", "iPhone 12 Usado Vitrine",
                   "Apple iPhone 15 128 GB Cor Rosa (Novo com caixa aberta)", "iPhone 15 Open Box"):
        o = oferta(titulo, preco=3000.0)
        registrar_historico(o, 4000.0)
        aprovadas, rejeicoes = pipeline.filtrar_detalhado([o], destinos.apple())
        assert aprovadas == [] and rejeicoes["palavra bloqueada"] == 1, titulo


def test_palavras_bloqueadas_do_apple_nao_valem_para_o_geral(com_grupo_apple):
    o = oferta("Notebook Usado Vitrine Dell", preco=100.0)
    assert len(pipeline.filtrar_detalhado([o], destinos.geral())[0]) == 1


def test_apple_ignora_as_regras_do_mix_de_categorias(com_grupo_apple, monkeypatch):
    """AirPods é "tecnologia" (teto de R$ 300 no canal geral), mas o grupo Apple não usa o mix."""
    monkeypatch.setattr(mix.config, "mix_ativo", True)
    fone = oferta("AirPods Pro 2ª Geração", preco=1900.0)
    registrar_historico(fone, 2200.0)
    assert len(pipeline.filtrar_detalhado([fone], destinos.apple())[0]) == 1
    geral = oferta("Fone Bluetooth JBL Tune", preco=1900.0)
    registrar_historico(geral, 2200.0)
    aprovadas, rejeicoes = pipeline.filtrar_detalhado([geral], destinos.geral())
    assert aprovadas == [] and rejeicoes["tecnologia acima do teto"] == 1


# ── banco: cada destino conta só os seus posts ───────────────────────

def _postada_ha(o, horas, destino):
    db.registrar(o, destino)
    with db._conn() as c:
        quando = (dt.datetime.now() - dt.timedelta(hours=horas)).isoformat(timespec="seconds")
        c.execute("UPDATE postadas SET postada_em = ? WHERE uid = ?", (quando, o.uid))


def test_variedade_e_separada_por_destino():
    _postada_ha(oferta("Apple iPhone 15 128GB", id_produto="a1"), 1, "apple")
    assert pipeline._tipos_recentes("apple") == {"celular"}
    assert pipeline._tipos_recentes("geral") == set()             # iPhone no grupo Apple não bloqueia o canal geral


def test_mix_e_separado_por_destino(monkeypatch):
    _postada_ha(oferta("Panela Tramontina", id_produto="g1"), 1, "geral")
    _postada_ha(oferta("Apple iPhone 15", id_produto="a1"), 1, "apple")
    assert dict(mix.contagem_recente("geral")) == {"casa_e_cozinha": 1}
    assert dict(mix.contagem_recente("apple")) == {"outros": 1}


def test_registrar_sem_destino_grava_geral():
    o = oferta("Panela Tramontina", id_produto="g1")
    db.registrar(o)
    with db._conn() as c:
        assert c.execute("SELECT destino FROM postadas WHERE uid = ?", (o.uid,)).fetchone()[0] == "geral"


def test_banco_antigo_sem_coluna_destino_e_migrado_e_o_historico_vira_geral():
    banco = db._DB
    with sqlite3.connect(banco) as c:                              # esquema anterior ao grupo Apple
        c.execute("CREATE TABLE postadas (uid TEXT PRIMARY KEY, plataforma TEXT, titulo TEXT, preco REAL, postada_em TEXT)")
        c.execute("INSERT INTO postadas VALUES ('mercadolivre:1', 'mercadolivre', 'Panela Antiga', 50.0, ?)",
                  (dt.datetime.now().isoformat(timespec="seconds"),))
    assert [t for _, t in db.titulos_postados_desde(24, destino="geral")] == ["Panela Antiga"]
    assert db.titulos_postados_desde(24, destino="apple") == []
    db.registrar(oferta("Apple iPhone 15", id_produto="a1"), "apple")     # e continua aceitando posts novos
    assert db.total_postadas() == 2


# ── o ciclo publica cada oferta no chat certo ────────────────────────

class BotFalso:
    def __init__(self):
        self.mensagens = []

    async def send_message(self, chat_id, texto, **kw):
        self.mensagens.append((chat_id, texto))
        return type("Msg", (), {"message_id": 1})()


def _rodar_ciclo(monkeypatch, coletadas, escolhidas_por_destino):
    enviados = []

    async def postar_falso(bot, o, chat_id, teclado=None):
        enviados.append((chat_id, o.titulo))

    def selecionar_falso(brutas, n, checar, destino):
        return escolhidas_por_destino.get(destino.nome, []), {}

    monkeypatch.setattr(pipeline, "coletar", lambda: coletadas)
    monkeypatch.setattr(pipeline, "dentro_do_horario", lambda: True)
    monkeypatch.setattr(pipeline, "selecionar", selecionar_falso)
    monkeypatch.setattr(pipeline, "postar_oferta", postar_falso)
    monkeypatch.setattr(pipeline.config, "espacamento_segundos", 0)
    bot = BotFalso()
    total = asyncio.run(pipeline.executar_ciclo(bot))
    return total, enviados, bot


def test_ciclo_publica_no_canal_certo_e_registra_o_destino(monkeypatch, com_grupo_apple):
    iphone, panela = oferta("Apple iPhone 15 128GB", id_produto="a1"), oferta("Panela Tramontina", id_produto="g1")
    total, enviados, _ = _rodar_ciclo(monkeypatch, [iphone, panela], {"apple": [iphone], "geral": [panela]})
    assert total == 2
    assert sorted(enviados) == sorted([(GRUPO_APPLE, "Apple iPhone 15 128GB"), (CANAL, "Panela Tramontina")])
    with db._conn() as c:
        assert dict(c.execute("SELECT uid, destino FROM postadas").fetchall()) == \
            {"mercadolivre:a1": "apple", "mercadolivre:g1": "geral"}


def test_divulgacao_e_so_do_canal_geral(monkeypatch, com_grupo_apple):
    iphone = oferta("Apple iPhone 15 128GB", id_produto="a1")
    _, _, bot = _rodar_ciclo(monkeypatch, [iphone], {"apple": [iphone], "geral": []})
    assert bot.mensagens == []                                     # só o Apple postou: sem divulgação do canal geral
    panela = oferta("Panela Tramontina", id_produto="g1")
    _, _, bot = _rodar_ciclo(monkeypatch, [panela], {"apple": [], "geral": [panela]})
    assert [chat for chat, _ in bot.mensagens] == [CANAL]


def test_ciclo_sem_grupo_apple_tudo_no_canal_geral(monkeypatch):
    iphone = oferta("Apple iPhone 15 128GB", id_produto="a1")
    total, enviados, _ = _rodar_ciclo(monkeypatch, [iphone], {"geral": [iphone]})
    assert total == 1 and enviados == [(CANAL, "Apple iPhone 15 128GB")]


def test_ciclo_nao_falha_se_um_destino_nao_tem_ofertas(monkeypatch, com_grupo_apple):
    panela = oferta("Panela Tramontina", id_produto="g1")
    total, enviados, _ = _rodar_ciclo(monkeypatch, [panela], {"geral": [panela], "apple": []})
    assert total == 1 and enviados == [(CANAL, "Panela Tramontina")]


# ── coleta: a busca própria do grupo Apple ───────────────────────────

@pytest.fixture
def ml_ativo(monkeypatch):
    monkeypatch.setattr(pipeline.config, "fonte_ml", {"ativa": True})
    monkeypatch.setattr(pipeline.config, "fonte_shopee", {"ativa": False})
    monkeypatch.setattr(pipeline.config, "fonte_amazon", {"ativa": False})
    monkeypatch.setattr(pipeline.mercadolivre, "tem_sessao", lambda: True)
    monkeypatch.setattr(pipeline.config, "apple_buscas", ["apple iphone", "apple watch"])
    monkeypatch.setattr(pipeline.mercadolivre, "buscar_ofertas", lambda: [oferta("Panela Tramontina", id_produto="g1")])


def test_coleta_junta_a_busca_apple_mas_so_o_que_e_apple(monkeypatch, ml_ativo, com_grupo_apple):
    termos = []

    def busca(t):
        termos.append(list(t))
        return [oferta("Apple iPhone 16 128GB", id_produto="a1"), oferta("Capa para iPhone 16", id_produto="c1"),
                oferta("Smartwatch Haylou 4s", id_produto="h1")]
    monkeypatch.setattr(pipeline.mercadolivre, "buscar_termos", busca)
    coletadas = pipeline.coletar()
    assert termos == [["apple iphone", "apple watch"]]
    # a capa e o smartwatch de outra marca vieram na busca, mas NÃO entram (não viram oferta do canal geral)
    assert sorted(o.id_produto for o in coletadas) == ["a1", "g1"]


def test_coleta_nao_duplica_produto_que_ja_veio_da_pagina_de_ofertas(monkeypatch, ml_ativo, com_grupo_apple):
    monkeypatch.setattr(pipeline.mercadolivre, "buscar_ofertas", lambda: [oferta("Apple iPhone 16 128GB", id_produto="a1")])
    monkeypatch.setattr(pipeline.mercadolivre, "buscar_termos", lambda t: [oferta("Apple iPhone 16 128GB", id_produto="a1")])
    assert [o.id_produto for o in pipeline.coletar()] == ["a1"]


def test_coleta_sem_grupo_apple_nao_faz_a_busca(monkeypatch, ml_ativo):
    def nao_deveria(_):
        raise AssertionError("não deveria buscar Apple")
    monkeypatch.setattr(pipeline.mercadolivre, "buscar_termos", nao_deveria)
    assert [o.id_produto for o in pipeline.coletar()] == ["g1"]


def test_falha_na_busca_apple_nao_derruba_a_coleta_normal(monkeypatch, ml_ativo, com_grupo_apple):
    def falha(_):
        raise RuntimeError("sessão expirou")
    monkeypatch.setattr(pipeline.mercadolivre, "buscar_termos", falha)
    assert [o.id_produto for o in pipeline.coletar()] == ["g1"]


def test_sem_termos_de_busca_nao_busca(monkeypatch, ml_ativo, com_grupo_apple):
    monkeypatch.setattr(pipeline.config, "apple_buscas", [])
    monkeypatch.setattr(pipeline.mercadolivre, "buscar_termos", lambda t: pytest.fail("não deveria buscar"))
    assert [o.id_produto for o in pipeline.coletar()] == ["g1"]


# ── Amazon: busca de termos do grupo Apple ───────────────────────────

from ofertas.sources import amazon  # noqa: E402


@pytest.fixture
def amazon_falsa(monkeypatch):
    """Troca a raspagem por um gravador de tarefas, e zera o rodízio."""
    monkeypatch.setattr(amazon, "_rodizio_termos", 0)
    chamadas = []

    def raspar(tarefas):
        chamadas.append([t["params"]["k"] for t in tarefas])
        return [oferta(f"Apple iPhone {t['params']['k']}", plataforma="amazon", id_produto=f"B{i}{len(chamadas)}")
                for i, t in enumerate(tarefas)]
    monkeypatch.setattr(amazon, "_buscar_por_scraping", raspar)
    return chamadas


def test_amazon_busca_de_termos_em_rodizio(amazon_falsa):
    termos = ["a", "b", "c"]
    for _ in range(3):
        amazon.buscar_termos(termos, por_ciclo=2)
    assert amazon_falsa == [["a", "b"], ["c", "a"], ["b", "c"]]           # todos os termos são cobertos, dois por vez


def test_amazon_busca_de_termos_zero_significa_todos(amazon_falsa):
    amazon.buscar_termos(["a", "b", "c"], por_ciclo=0)
    assert amazon_falsa == [["a", "b", "c"]]


def test_amazon_busca_de_termos_vazia_nao_raspa(amazon_falsa):
    assert amazon.buscar_termos([], por_ciclo=2) == [] and amazon_falsa == []


def test_amazon_busca_de_termos_com_menos_termos_que_o_rodizio(amazon_falsa):
    amazon.buscar_termos(["a"], por_ciclo=5)
    assert amazon_falsa == [["a"]]


@pytest.fixture
def so_amazon(monkeypatch):
    monkeypatch.setattr(pipeline.config, "fonte_ml", {"ativa": False})
    monkeypatch.setattr(pipeline.config, "fonte_shopee", {"ativa": False})
    monkeypatch.setattr(pipeline.config, "fonte_amazon", {"ativa": True})
    monkeypatch.setattr(pipeline.config, "amazon_tag", "tag-20")
    monkeypatch.setattr(pipeline.config, "apple_buscas", ["apple iphone"])
    monkeypatch.setattr(pipeline.amazon, "buscar_ofertas",
                        lambda: [oferta("Panela Tramontina", plataforma="amazon", id_produto="G1")])


def test_coleta_amazon_junta_so_produtos_apple_da_busca(monkeypatch, so_amazon, com_grupo_apple):
    monkeypatch.setattr(pipeline.amazon, "buscar_termos", lambda t, n: [
        oferta("Apple iPhone 16 128GB", plataforma="amazon", id_produto="A1"),
        oferta("Capa para iPhone 16", plataforma="amazon", id_produto="C1"),          # acessório: fica de fora
        oferta("Apple iPhone 16 128GB", plataforma="amazon", id_produto="A1"),        # repetido na busca
    ])
    assert sorted(o.id_produto for o in pipeline.coletar()) == ["A1", "G1"]


def test_coleta_amazon_usa_o_rodizio_configurado(monkeypatch, so_amazon, com_grupo_apple):
    monkeypatch.setattr(pipeline.config, "apple_amazon_termos_por_ciclo", 3)
    recebido = []
    monkeypatch.setattr(pipeline.amazon, "buscar_termos", lambda t, n: recebido.append((list(t), n)) or [])
    pipeline.coletar()
    assert recebido == [(["apple iphone"], 3)]


def test_amazon_sem_tag_nao_e_consultada(monkeypatch, so_amazon, com_grupo_apple):
    monkeypatch.setattr(pipeline.config, "amazon_tag", "")
    monkeypatch.setattr(pipeline.amazon, "buscar_termos", lambda t, n: pytest.fail("não deveria buscar"))
    assert pipeline.coletar() == []                                    # sem tag: nem ofertas nem busca Apple


def test_falha_na_busca_apple_da_amazon_nao_derruba_a_coleta(monkeypatch, so_amazon, com_grupo_apple):
    def bloqueada(t, n):
        raise RuntimeError("captcha")
    monkeypatch.setattr(pipeline.amazon, "buscar_termos", bloqueada)
    assert [o.id_produto for o in pipeline.coletar()] == ["G1"]


def test_produto_apple_da_amazon_vai_para_o_grupo_apple(com_grupo_apple):
    iphone = oferta("Apple iPhone 16 128GB", plataforma="amazon", id_produto="A1")
    grupos = destinos.dividir([iphone, oferta("Panela Tramontina", plataforma="amazon", id_produto="G1")])
    assert [o.id_produto for o in grupos["apple"]] == ["A1"] and [o.id_produto for o in grupos["geral"]] == ["G1"]


# ── Amazon: cabeçalhos de navegador e nova tentativa no 503 ──────────

class RespostaFalsa:
    def __init__(self, status, texto=""):
        self.status_code, self.text = status, texto


class SessaoFalsa:
    def __init__(self, *respostas):
        self.respostas, self.pedidos = list(respostas), 0

    def get(self, url, params=None, timeout=None):
        self.pedidos += 1
        return self.respostas.pop(0)


CARD_AMAZON_OK = ('<html><title>Amazon.com.br : apple iphone</title><div data-component-type="s-search-result" '
                  'data-asin="B0ABC12345"><h2><a class="a-link-normal s-no-outline" href="/dp/B0ABC12345"><span>Apple iPhone 16</span></a></h2>'
                  '<span class="a-price"><span class="a-offscreen">R$ 4.999,00</span></span></div></html>')


@pytest.fixture
def sem_pausas(monkeypatch):
    monkeypatch.setattr(amazon.time, "sleep", lambda s: None)


def test_sessao_da_amazon_usa_cabecalhos_de_navegador(monkeypatch):
    monkeypatch.setattr(amazon, "_sessao_amz", None)
    h = amazon._sessao().headers
    assert h["Accept"].startswith("text/html") and h["Sec-Fetch-Dest"] == "document"
    assert "sec-ch-ua" not in {k.lower() for k in h}                   # com ele a Amazon voltou a responder 503


def test_503_e_repetido_uma_vez_e_o_segundo_pedido_vale(sem_pausas):
    s = SessaoFalsa(RespostaFalsa(503), RespostaFalsa(200, "ok"))
    r = amazon._pegar(s, {"k": "x"})
    assert (r.status_code, s.pedidos) == (200, 2)


def test_503_duas_vezes_desiste_sem_repetir_para_sempre(sem_pausas):
    s = SessaoFalsa(RespostaFalsa(503), RespostaFalsa(503))
    assert amazon._pegar(s, {"k": "x"}).status_code == 503 and s.pedidos == 2


def test_resposta_normal_e_captcha_nao_sao_repetidos(sem_pausas):
    s = SessaoFalsa(RespostaFalsa(200, "ok"))
    assert amazon._pegar(s, {"k": "x"}).status_code == 200 and s.pedidos == 1
    s = SessaoFalsa(RespostaFalsa(200, "<html>Digite os caracteres captcha</html>"))
    amazon._pegar(s, {"k": "x"})
    assert s.pedidos == 1                                              # captcha vem com 200: repetir só pioraria


def test_raspagem_se_recupera_de_um_503_inicial(monkeypatch, sem_pausas):
    monkeypatch.setattr(pipeline.config, "amazon_tag", "tag-20")
    monkeypatch.setattr(pipeline.config, "fonte_amazon", {"ativa": True, "paginas": 1})
    sessao = SessaoFalsa(RespostaFalsa(503), RespostaFalsa(200, CARD_AMAZON_OK))
    monkeypatch.setattr(amazon, "_sessao", lambda: sessao)
    ofertas = amazon._buscar_por_scraping([{"rotulo": "busca 'apple iphone'", "params": {"k": "apple iphone"}}])
    assert [o.id_produto for o in ofertas] == ["B0ABC12345"] and ofertas[0].url_afiliado.endswith("tag=tag-20")


# ── limite de queda do grupo Apple: 5%, e o post mostra essa queda ───

from ofertas.formatter import montar_caption  # noqa: E402


def test_apple_exatamente_no_limite_de_5_por_cento_passa(com_grupo_apple):
    o = oferta("Apple iPhone 15 128GB Preto", preco=4750.0)                   # -5,0%
    registrar_historico(o, 5000.0)
    aprovadas, _ = pipeline.filtrar_detalhado([o], destinos.apple())
    assert len(aprovadas) == 1 and aprovadas[0].desconto == 5


def test_apple_logo_abaixo_do_limite_nao_passa(com_grupo_apple):
    o = oferta("Apple iPhone 15 128GB Preto", preco=4800.0)                   # -4%
    registrar_historico(o, 5000.0)
    assert pipeline.filtrar_detalhado([o], destinos.apple())[0] == []


def test_queda_pequena_do_apple_aparece_no_post(com_grupo_apple):
    """Antes o post só mostrava quedas a partir de 10%: um iPhone que passou com -6% sairia sem dizer por quê."""
    o = oferta("Apple iPhone 15 128GB Preto", preco=4700.0)                   # -6%
    registrar_historico(o, 5000.0)
    aprovadas, _ = pipeline.filtrar_detalhado([o], destinos.apple())
    texto = montar_caption(aprovadas[0])
    assert aprovadas[0].desconto_verificado is True
    assert "Caiu 6%" in texto and "❌ De: <s>R$ 5.000,00</s>" in texto and "💰 Por: <b>R$ 4.700,00</b>" in texto


def test_canal_geral_continua_mostrando_a_queda_so_a_partir_de_10_por_cento(com_grupo_apple):
    pequena = oferta("Panela Tramontina Antiaderente", preco=94.0)            # -6% no canal geral (aprovada como campeã)
    registrar_historico(pequena, 100.0)
    aprovadas, _ = pipeline.filtrar_detalhado([pequena], destinos.geral())
    assert aprovadas[0].desconto_verificado is False and "Caiu" not in montar_caption(aprovadas[0])
    maior = oferta("Panela Inox Tramontina", preco=88.0, id_produto="m2")     # -12%
    registrar_historico(maior, 100.0)
    aprovadas, _ = pipeline.filtrar_detalhado([maior], destinos.geral())
    assert aprovadas[0].desconto_verificado is True and "Caiu 12%" in montar_caption(aprovadas[0])
