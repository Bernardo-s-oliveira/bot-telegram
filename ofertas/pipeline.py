import asyncio
import datetime as dt
import itertools
import logging
import math
import re
import statistics
from collections import Counter

from telegram import Bot

from . import db, selecao
from .config import config, dentro_do_horario
from .formatter import preco_br
from .models import Oferta
from .sources import amazon, mercadolivre, shopee
from .telegram_poster import postar_oferta

log = logging.getLogger("ofertas.pipeline")


def coletar() -> list[Oferta]:
    """Busca ofertas nas fontes automáticas ativas (sem link de afiliado ainda, no caso do ML)."""
    todas: list[Oferta] = []

    if config.fonte_shopee.get("ativa"):
        if config.shopee_app_id and config.shopee_app_secret:
            try:
                todas += shopee.buscar_ofertas(int(config.fonte_shopee.get("limite", 30)))
            except Exception as e:
                log.error("Shopee: %s", e)
        else:
            log.warning("Shopee ativa no config.yaml mas sem credenciais no .env — pulando")

    if config.fonte_amazon.get("ativa"):
        if config.amazon_tag:
            try:
                todas += amazon.buscar_ofertas()
            except Exception as e:
                log.error("Amazon: %s", e)
        else:
            log.warning("Amazon ativa no config.yaml mas sem AMAZON_TAG no .env — pulando")

    if config.fonte_ml.get("ativa"):
        if mercadolivre.tem_sessao():
            try:
                todas += mercadolivre.buscar_ofertas()
            except Exception as e:
                log.error("Mercado Livre: %s", e)
        else:
            log.warning("Mercado Livre ativo mas sem sessão de afiliado — rode: uv run python -m ofertas ml-login")

    return todas


def _repostagens_permitidas(ofertas: list[Oferta]) -> dict[str, float | None]:
    """Ofertas que podem ser postadas: {uid: preço do post anterior, ou None se nunca postada}.

    Produto já postado só volta antes do prazo `nao_repetir_dias` se o preço caiu
    `repostar_se_queda_pct` ou mais desde o último post (e passou ao menos 1 dia)."""
    ultimas = db.ultimas_postagens([o.uid for o in ofertas])
    agora = dt.datetime.now()
    liberadas: dict[str, float | None] = {}
    for o in ofertas:
        ultima = ultimas.get(o.uid)
        if not ultima:
            liberadas[o.uid] = None
            continue
        quando, preco_anterior = ultima
        idade = agora - quando
        if idade >= dt.timedelta(days=config.nao_repetir_dias):
            liberadas[o.uid] = None
        elif (config.repostar_queda_pct and preco_anterior and o.preco
              and idade >= dt.timedelta(days=1)
              and o.preco <= preco_anterior * (1 - config.repostar_queda_pct / 100)):
            liberadas[o.uid] = preco_anterior
    return liberadas


def filtrar_detalhado(ofertas: list[Oferta]) -> tuple[list[Oferta], dict[str, int]]:
    """Filtros básicos + avaliação de qualidade (selecao.py). Retorna (aprovadas, motivos de rejeição)."""
    rejeicoes: dict[str, int] = {}

    def rejeitar(motivo: str) -> None:
        rejeicoes[motivo] = rejeicoes.get(motivo, 0) + 1

    candidatas = []
    for o in ofertas:
        titulo = o.titulo.lower() if o.titulo else ""
        if not titulo:
            rejeitar("sem título")
        elif any(p in titulo for p in config.palavras_bloqueadas):
            rejeitar("palavra bloqueada")
        elif o.preco is not None and (
                (config.preco_minimo and o.preco < config.preco_minimo)
                or (config.preco_maximo and o.preco > config.preco_maximo)):
            rejeitar("fora da faixa de preço")
        else:
            candidatas.append(o)

    liberadas = _repostagens_permitidas(candidatas)
    nao_repetidas = [o for o in candidatas if o.uid in liberadas]
    if len(nao_repetidas) < len(candidatas):
        rejeicoes["já postada"] = len(candidatas) - len(nao_repetidas)

    historicos = db.historico([o.uid for o in nao_repetidas], config.historico_dias)
    aprovadas, motivos = selecao.avaliar_todas(nao_repetidas, historicos)
    for motivo, qtd in motivos.items():
        rejeicoes[motivo] = rejeicoes.get(motivo, 0) + qtd

    for o in aprovadas:
        anterior = liberadas.get(o.uid)
        if anterior:
            o.selos.append(f"🔁 De volta e mais barato: estava {preco_br(anterior)} no último post")
    return aprovadas, rejeicoes


def filtrar(ofertas: list[Oferta]) -> list[Oferta]:
    return filtrar_detalhado(ofertas)[0]


def _tokens(titulo: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"\w+", titulo.lower()) if len(w) > 2)


def _parecido(a: frozenset[str], b: frozenset[str], limite: float = 0.6) -> bool:
    """Mesmo produto em outra cor/tamanho/loja: a maioria das palavras do título coincide."""
    return bool(a and b) and len(a & b) / len(a | b) >= limite


def _numeros(tokens: frozenset[str]) -> frozenset[str]:
    return frozenset(t for t in tokens if any(c.isdigit() for c in t))


def anotar_preco_mercado(ofertas: list[Oferta], min_pares: int = 2, similaridade: float = 0.7) -> None:
    """Preenche `preco_mercado`: mediana do preço de ao menos `min_pares` anúncios do MESMO produto
    (título quase igual e mesmos números — 43" vs 50", 5500W vs 7500W — de qualquer loja) coletados agora.
    Serve de referência independente quando ainda não há histórico de preço, e para achar preços fora da curva."""
    dados = [(o, _tokens(o.titulo)) for o in ofertas if o.preco and o.preco > 0]
    numeros = [_numeros(t) for _, t in dados]
    for i, (o, t) in enumerate(dados):
        precos = [p.preco for j, (p, tp) in enumerate(dados)
                  if j != i and p.uid != o.uid and numeros[j] == numeros[i] and _parecido(t, tp, similaridade)]
        o.preco_mercado = statistics.median(precos) if len(precos) >= min_pares else None


def _selecionar(candidatas: list[Oferta], k: int, ja: list[frozenset[str]],
                por_plataforma: Counter, teto: int) -> list[Oferta]:
    """Até `k` melhores por score, sem repetir produto (`ja`) e sem deixar uma plataforma
    passar de `teto` posts — o teto só é relaxado se faltar oferta."""
    achadas: list[Oferta] = []
    adiadas: list[tuple[Oferta, frozenset[str]]] = []

    def tomar(o: Oferta, t: frozenset[str]) -> None:
        ja.append(t)
        por_plataforma[o.plataforma] += 1
        achadas.append(o)

    for o in sorted(candidatas, key=lambda o: o.score, reverse=True):
        if len(achadas) >= k:
            break
        t = _tokens(o.titulo)
        if any(_parecido(t, v) for v in ja):
            continue
        if por_plataforma[o.plataforma] >= teto:
            adiadas.append((o, t))
        else:
            tomar(o, t)
    for o, t in adiadas:
        if len(achadas) >= k:
            break
        if not any(_parecido(t, v) for v in ja):
            tomar(o, t)
    return achadas


def escolher(ofertas: list[Oferta], n: int) -> list[Oferta]:
    """Até N ofertas: parte da cota vai para os "campeões de venda", o resto para os maiores
    descontos verificados. Faixa sem candidatas cede a vaga à outra; os posts saem intercalados."""
    ja: list[frozenset[str]] = []
    por_plataforma: Counter = Counter()
    teto = max(1, math.ceil(n * 0.6))

    campeoes = _selecionar([o for o in ofertas if o.faixa == "campeao"],
                           round(n * config.campeoes_pct_posts / 100), ja, por_plataforma, teto)
    descontos = _selecionar([o for o in ofertas if o.faixa == "desconto"],
                            n - len(campeoes), ja, por_plataforma, teto)
    faltam = n - len(campeoes) - len(descontos)
    if faltam > 0:  # sobrou vaga: qualquer faixa pode preenchê-la
        usadas = {id(o) for o in campeoes + descontos}
        descontos += _selecionar([o for o in ofertas if id(o) not in usadas],
                                 faltam, ja, por_plataforma, teto)

    intercaladas: list[Oferta] = []
    for par in itertools.zip_longest(campeoes, descontos):
        intercaladas += [o for o in par if o]
    return intercaladas


def selecionar(brutas: list[Oferta], n: int, checar_vendedores: bool = True) -> tuple[list[Oferta], dict[str, int]]:
    """Tudo entre a coleta e o post: filtros, qualidade, escolha e checagem de vendedor.

    O vendedor (ML) só é conferido nas escolhidas, porque exige abrir a página do produto. Se algum
    reprovar, sai da disputa e a vaga é refeita com o próximo melhor. Retorna (escolhidas, rejeições)."""
    anotar_preco_mercado(brutas)
    boas, rejeicoes = filtrar_detalhado(brutas)
    escolhidas = escolher(boas, n)
    if not (checar_vendedores and config.verificar_vendedor):
        return escolhidas, rejeicoes

    for _ in range(4):
        pendentes = [o for o in escolhidas if o.plataforma == "mercadolivre" and not o.vendedor_checado]
        if not pendentes:
            break
        try:
            mercadolivre.verificar_vendedores(pendentes)
        except Exception as e:
            log.warning("Não consegui conferir os vendedores do ML: %s", e)
            for o in pendentes:
                o.vendedor_checado = True   # sem dados: só desconto suspeito é barrado
        reprovadas = []
        for o in pendentes:
            motivo = selecao.avaliar_vendedor(o)
            if motivo:
                log.info("Vendedor reprovado — %s: %s", o.titulo[:50], motivo)
                chave = motivo.split(" (")[0]
                rejeicoes[chave] = rejeicoes.get(chave, 0) + 1
                reprovadas.append(o)
        if reprovadas:
            fora = {id(o) for o in reprovadas}
            boas = [o for o in boas if id(o) not in fora]
        escolhidas = escolher(boas, n)
        if not reprovadas:
            break
    # esgotou as rodadas com candidatos ainda sem checagem: os suspeitos não passam sem ela
    escolhidas = [o for o in escolhidas if not (o.suspeita and o.plataforma == "mercadolivre" and not o.vendedor_checado)]
    return escolhidas, rejeicoes


async def avisar_dono(bot: Bot, texto: str) -> None:
    """Manda um aviso no privado do dono (se configurado) — para operação sem supervisão."""
    if not config.owner_id:
        return
    try:
        await bot.send_message(config.owner_id, texto)
    except Exception as e:
        log.warning("Não consegui avisar o dono: %s", e)


async def executar_ciclo(bot: Bot) -> int:
    """Um ciclo completo: coletar -> filtrar -> escolher -> gerar links -> postar. Retorna nº de posts."""
    if not dentro_do_horario():
        log.info("Fora do horário ativo (%s) — ciclo pulado", config.horario_ativo)
        return 0

    brutas = await asyncio.to_thread(coletar)
    await asyncio.to_thread(db.registrar_precos, brutas)  # alimenta o histórico usado para validar descontos
    escolhidas, rejeicoes = await asyncio.to_thread(selecionar, brutas, config.max_posts_por_ciclo)
    if rejeicoes:
        log.info("Rejeitadas: %s", ", ".join(f"{m}: {q}" for m, q in sorted(rejeicoes.items(), key=lambda x: -x[1])))

    # Mercado Livre: gerar link de afiliado só das escolhidas (linkbuilder é caro)
    ml_pendentes = [o for o in escolhidas if o.plataforma == "mercadolivre" and not o.url_afiliado]
    if ml_pendentes:
        try:
            await asyncio.to_thread(mercadolivre.gerar_links_afiliado, ml_pendentes)
        except Exception as e:
            log.error("Linkbuilder ML falhou: %s", e)
            if "Sessão" in str(e):
                await avisar_dono(bot, f"⚠️ Mercado Livre parou de gerar links: {e}")

    postadas = 0
    for o in escolhidas:
        if not o.url_afiliado:
            log.warning("Sem link de afiliado, pulando: %s", o.titulo[:60])
            continue
        try:
            await postar_oferta(bot, o, config.chat_id)
        except Exception as e:
            log.error("Falha ao postar '%s': %s", o.titulo[:60], e)
            continue
        db.registrar(o)
        log.info("Postada [%s %.2f] %s", o.faixa, o.score, o.titulo[:60])
        postadas += 1
        if o is not escolhidas[-1]:
            await asyncio.sleep(config.espacamento_segundos)

    log.info("Ciclo: %d coletadas, %d escolhidas, %d postadas", len(brutas), len(escolhidas), postadas)
    return postadas
