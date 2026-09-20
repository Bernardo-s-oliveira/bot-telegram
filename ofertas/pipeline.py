import asyncio
import datetime as dt
import itertools
import logging
import math
import re
import statistics
from collections import Counter

from telegram import Bot

from . import db, destinos, mix, selecao
from .config import config, dentro_do_horario
from .formatter import preco_br
from .models import Oferta
from .sources import amazon, mercadolivre, shopee
from .tipos import tipo_do_produto
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

    # Grupo Apple: as páginas de ofertas quase nunca trazem produto Apple, então há buscas próprias (ML e Amazon).
    # As buscas também trazem capas, smartwatches de outras marcas etc.: só entra o que é Apple (o resto não
    # vira oferta do canal geral).
    if destinos.apple() and config.apple_buscas:
        achadas: list[Oferta] = []
        if config.fonte_ml.get("ativa") and mercadolivre.tem_sessao():
            try:
                achadas += mercadolivre.buscar_termos(config.apple_buscas)
            except Exception as e:
                log.error("Busca Apple no Mercado Livre: %s", e)
        if config.fonte_amazon.get("ativa") and config.amazon_tag:
            try:
                achadas += amazon.buscar_termos(config.apple_buscas, config.apple_amazon_termos_por_ciclo)
            except Exception as e:
                log.error("Busca Apple na Amazon: %s", e)
        ja = {o.uid for o in todas}
        apple = [o for o in achadas if destinos.e_apple(o) and o.uid not in ja]
        todas += list({o.uid: o for o in apple}.values())
        log.info("Busca Apple: %d produtos Apple", len(apple))

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


def _tipos_recentes(destino: str = "geral") -> set[str]:
    """Tipos de produto (tipos.py) postados no `destino` nas últimas `variedade.janela_horas` horas."""
    if config.variedade_janela_horas <= 0:
        return set()
    postados = db.titulos_postados_desde(config.variedade_janela_horas, destino=destino)
    return {t for t in (tipo_do_produto(titulo, uid) for uid, titulo in postados) if t}


def filtrar_detalhado(ofertas: list[Oferta], destino: destinos.Destino | None = None) -> tuple[list[Oferta], dict[str, int]]:
    """Filtros básicos + avaliação de qualidade (selecao.py) com as regras do `destino` (padrão: canal geral).
    Retorna (aprovadas, motivos de rejeição)."""
    destino = destino or destinos.geral()
    bloqueadas = [*config.palavras_bloqueadas, *destino.palavras_bloqueadas]
    rejeicoes: dict[str, int] = {}

    def rejeitar(motivo: str) -> None:
        rejeicoes[motivo] = rejeicoes.get(motivo, 0) + 1

    candidatas = []
    for o in ofertas:
        titulo = o.titulo.lower() if o.titulo else ""
        if not titulo:
            rejeitar("sem título")
        elif any(p in titulo for p in bloqueadas):
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

    # variedade: tipo de produto já postado há pouco fica de fora (repostagem por queda de preço é exceção)
    recentes = _tipos_recentes(destino.nome)
    if recentes:
        variadas = [o for o in nao_repetidas
                    if liberadas.get(o.uid) or tipo_do_produto(o.titulo, o.uid) not in recentes]
        if len(variadas) < len(nao_repetidas):
            rejeicoes["tipo postado há pouco"] = len(nao_repetidas) - len(variadas)
        nao_repetidas = variadas

    historicos = db.historico([o.uid for o in nao_repetidas], config.historico_dias)
    aprovadas, motivos = selecao.avaliar_todas(nao_repetidas, historicos, destino.queda_minima, destino.so_queda)
    for motivo, qtd in motivos.items():
        rejeicoes[motivo] = rejeicoes.get(motivo, 0) + qtd

    # categorias com restrição (mix.py): tecnologia com teto de preço, eletrodoméstico só com queda comprovada
    liberadas_mix = []
    for o in aprovadas:
        motivo = mix.motivo_de_exclusao(o) if destino.usar_mix else None
        if motivo:
            rejeicoes[motivo.split(" de R$")[0]] = rejeicoes.get(motivo.split(" de R$")[0], 0) + 1
        else:
            liberadas_mix.append(o)
    aprovadas = liberadas_mix

    for o in aprovadas:
        anterior = liberadas.get(o.uid)
        if anterior:
            o.selos.append(f"🔁 De volta e mais barato: estava {preco_br(anterior)} no último post")
    return aprovadas, rejeicoes


def filtrar(ofertas: list[Oferta], destino: destinos.Destino | None = None) -> list[Oferta]:
    return filtrar_detalhado(ofertas, destino)[0]


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
                por_plataforma: Counter, teto: int, tipos: set[str],
                contagem: Counter, alvo: dict[str, float]) -> list[Oferta]:
    """Até `k` ofertas. A cada vaga leva a de maior score + bônus do mix de categorias (mix.py), que
    recalcula com o que já foi escolhido no ciclo (`contagem`). Sem repetir produto (`ja`), sem repetir tipo
    de produto (`tipos`, vale também para as outras chamadas do ciclo) e sem deixar uma plataforma passar de
    `teto` posts — o teto de plataforma só é relaxado se faltar oferta; o de tipo, nunca."""
    achadas: list[Oferta] = []
    adiadas: list[tuple[Oferta, frozenset[str]]] = []
    restantes = list(candidatas)

    def tomar(o: Oferta, t: frozenset[str]) -> None:
        ja.append(t)
        por_plataforma[o.plataforma] += 1
        if tipo := tipo_do_produto(o.titulo, o.uid):
            tipos.add(tipo)
        contagem[mix.categoria(o)] += 1
        achadas.append(o)

    def tipo_repetido(o: Oferta) -> bool:
        return tipo_do_produto(o.titulo, o.uid) in tipos

    while restantes and len(achadas) < k:
        i = max(range(len(restantes)), key=lambda j: restantes[j].score + mix.bonus(restantes[j], contagem, alvo))
        o = restantes.pop(i)
        t = _tokens(o.titulo)
        if any(_parecido(t, v) for v in ja) or tipo_repetido(o):
            continue
        if por_plataforma[o.plataforma] >= teto:
            adiadas.append((o, t))
        else:
            tomar(o, t)
    for o, t in adiadas:
        if len(achadas) >= k:
            break
        if not any(_parecido(t, v) for v in ja) and not tipo_repetido(o):
            tomar(o, t)
    return achadas


def escolher(ofertas: list[Oferta], n: int, contagem_recente: Counter | None = None,
             usar_mix: bool = True) -> list[Oferta]:
    """Até N ofertas: parte da cota vai para os "campeões de venda", o resto para as quedas de preço
    comprovadas. Faixa sem candidatas cede a vaga à outra; os posts saem intercalados. `contagem_recente`
    (posts por categoria nos últimos posts) alimenta o mix de categorias; não é modificada."""
    contagem = Counter(contagem_recente or {})
    alvo = mix.metas() if usar_mix else {}
    ja: list[frozenset[str]] = []
    por_plataforma: Counter = Counter()
    tipos: set[str] = set()
    teto = max(1, math.ceil(n * 0.6))

    campeoes = _selecionar([o for o in ofertas if o.faixa == "campeao"],
                           round(n * config.campeoes_pct_posts / 100), ja, por_plataforma, teto, tipos, contagem, alvo)
    descontos = _selecionar([o for o in ofertas if o.faixa == "desconto"],
                            n - len(campeoes), ja, por_plataforma, teto, tipos, contagem, alvo)
    faltam = n - len(campeoes) - len(descontos)
    if faltam > 0:  # sobrou vaga: qualquer faixa pode preenchê-la
        usadas = {id(o) for o in campeoes + descontos}
        descontos += _selecionar([o for o in ofertas if id(o) not in usadas],
                                 faltam, ja, por_plataforma, teto, tipos, contagem, alvo)

    intercaladas: list[Oferta] = []
    for par in itertools.zip_longest(campeoes, descontos):
        intercaladas += [o for o in par if o]
    return intercaladas


def selecionar(brutas: list[Oferta], n: int, checar_vendedores: bool = True,
               destino: destinos.Destino | None = None) -> tuple[list[Oferta], dict[str, int]]:
    """Tudo entre a coleta e o post: filtros, qualidade, escolha e checagem de vendedor.

    O vendedor (ML) só é conferido nas escolhidas, porque exige abrir a página do produto. Se algum
    reprovar, sai da disputa e a vaga é refeita com o próximo melhor. Retorna (escolhidas, rejeições)."""
    destino = destino or destinos.geral()
    anotar_preco_mercado(brutas)
    boas, rejeicoes = filtrar_detalhado(brutas, destino)
    recentes = mix.contagem_recente(destino.nome) if (config.mix_ativo and destino.usar_mix) else Counter()
    escolhidas = escolher(boas, n, recentes, destino.usar_mix)
    if not (checar_vendedores and (config.verificar_vendedor or config.buscar_cupons)):
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
            motivo = selecao.avaliar_vendedor(o) if config.verificar_vendedor else None
            if motivo:
                log.info("Vendedor reprovado — %s: %s", o.titulo[:50], motivo)
                chave = motivo.split(" (")[0]
                rejeicoes[chave] = rejeicoes.get(chave, 0) + 1
                reprovadas.append(o)
        if reprovadas:
            fora = {id(o) for o in reprovadas}
            boas = [o for o in boas if id(o) not in fora]
        escolhidas = escolher(boas, n, recentes, destino.usar_mix)
        if not reprovadas:
            break
    # esgotou as rodadas com candidatos ainda sem checagem: os suspeitos não passam sem ela
    if config.verificar_vendedor:
        escolhidas = [o for o in escolhidas
                      if not (o.suspeita and o.plataforma == "mercadolivre" and not o.vendedor_checado)]
    return escolhidas, rejeicoes


async def avisar_dono(bot: Bot, texto: str) -> None:
    """Manda um aviso no privado do dono (se configurado) — para operação sem supervisão."""
    if not config.owner_id:
        return
    try:
        await bot.send_message(config.owner_id, texto)
    except Exception as e:
        log.warning("Não consegui avisar o dono: %s", e)


_CHAVE_DIVULGACAO = "divulgacao_em"


async def divulgar_canal(bot: Bot, agora: dt.datetime | None = None) -> bool:
    """Publica o texto de divulgação do canal (config `divulgacao`) se já passou `a_cada_horas` desde a
    última vez. Assim link e hashtag aparecem uma vez por período, e não em todo post. Retorna se publicou."""
    if not (config.divulgacao_ativa and config.divulgacao_texto and config.chat_id):
        return False
    agora = agora or dt.datetime.now()
    ultima = db.ler_estado(_CHAVE_DIVULGACAO)
    if ultima and agora - dt.datetime.fromisoformat(ultima) < dt.timedelta(hours=config.divulgacao_a_cada_horas):
        return False
    try:
        msg = await bot.send_message(config.chat_id, config.divulgacao_texto, disable_web_page_preview=True)
    except Exception as e:
        log.warning("Não consegui publicar o post de divulgação: %s", e)
        return False
    db.gravar_estado(_CHAVE_DIVULGACAO, agora.isoformat(timespec="seconds"))
    if config.divulgacao_fixar:
        try:
            await bot.pin_chat_message(config.chat_id, msg.message_id, disable_notification=True)
        except Exception as e:  # o bot precisa da permissão de fixar mensagens no canal
            log.warning("Não consegui fixar o post de divulgação: %s", e)
    return True


async def _postar_lista(bot: Bot, escolhidas: list[Oferta], destino: destinos.Destino) -> int:
    """Publica as ofertas no chat do destino, com pausa entre os posts. Retorna quantas saíram."""
    postadas = 0
    for o in escolhidas:
        if not o.url_afiliado:
            log.warning("Sem link de afiliado, pulando: %s", o.titulo[:60])
            continue
        try:
            await postar_oferta(bot, o, destino.chat_id)
        except Exception as e:
            log.error("Falha ao postar '%s' [%s]: %s", o.titulo[:60], destino.nome, e)
            continue
        db.registrar(o, destino.nome)
        log.info("Postada [%s · %s %.2f] %s", destino.nome, o.faixa, o.score, o.titulo[:60])
        postadas += 1
        if o is not escolhidas[-1]:
            await asyncio.sleep(config.espacamento_segundos)
    return postadas


async def executar_ciclo(bot: Bot) -> int:
    """Um ciclo completo: coletar -> dividir por destino -> filtrar/escolher -> gerar links -> postar.
    Produtos Apple vão para o grupo Apple (se configurado), o resto para o canal geral; cada destino tem as
    suas regras e os seus posts recentes. Retorna o nº total de posts."""
    if not dentro_do_horario():
        log.info("Fora do horário ativo (%s) — ciclo pulado", config.horario_ativo)
        return 0

    brutas = await asyncio.to_thread(coletar)
    await asyncio.to_thread(db.registrar_precos, brutas)  # alimenta o histórico usado para validar descontos
    grupos = destinos.dividir(brutas)

    selecionadas: list[tuple[destinos.Destino, list[Oferta]]] = []
    for d in destinos.ativos():   # em sequência: a checagem de vendedor usa o navegador do ML
        escolhidas, rejeicoes = await asyncio.to_thread(selecionar, grupos[d.nome], d.max_posts, True, d)
        if rejeicoes:
            log.info("[%s] Rejeitadas: %s", d.nome,
                     ", ".join(f"{m}: {q}" for m, q in sorted(rejeicoes.items(), key=lambda x: -x[1])))
        selecionadas.append((d, escolhidas))

    # Mercado Livre: gerar link de afiliado só das escolhidas (linkbuilder é caro), de todos os destinos de uma vez
    ml_pendentes = [o for _, lista in selecionadas for o in lista
                    if o.plataforma == "mercadolivre" and not o.url_afiliado]
    if ml_pendentes:
        try:
            await asyncio.to_thread(mercadolivre.gerar_links_afiliado, ml_pendentes)
        except Exception as e:
            log.error("Linkbuilder ML falhou: %s", e)
            if "Sessão" in str(e):
                await avisar_dono(bot, f"⚠️ Mercado Livre parou de gerar links: {e}")

    # cada destino publica no seu ritmo (a pausa entre posts é por chat)
    contagens = await asyncio.gather(*(_postar_lista(bot, lista, d) for d, lista in selecionadas))
    por_destino = {d.nome: n for (d, _), n in zip(selecionadas, contagens)}

    if por_destino.get("geral"):
        await divulgar_canal(bot)   # a divulgação é do canal geral
    log.info("Ciclo: %d coletadas, %s postadas", len(brutas),
             ", ".join(f"{n} em {nome}" for nome, n in por_destino.items()))
    return sum(contagens)
