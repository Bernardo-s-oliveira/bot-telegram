"""Seleção orientada ao comprador: só passa oferta que se sustenta nos dados.

O desconto que a loja anuncia NÃO qualifica nem pontua uma oferta (na página de ofertas do ML a mediana
anunciada é ~42%, com muito preço "De" inflado ou permanente). Só vale o que o histórico de preços do bot
comprova. Duas faixas:
- "campeao": muitas vendas + boa nota + preço em conta e não acima do normal. Não exige desconto;
- "desconto": queda de preço COMPROVADA no histórico (>= filtros.desconto_minimo) + alguma prova social.

Antes de pontuar, `avaliar` rejeita o que engana o comprador: sem avaliação, nota baixa, preço acima do
normal do produto (ou de anúncios iguais) e preço fora da curva (erro de preço, golpe, vendedor duvidoso).
"""
import math

from .config import config
from .db import Historico
from .models import Oferta

# Pesos por faixa (somam 1). O campeão dá mais peso a vendas e a preço baixo.
PESOS = {
    "desconto": {"pop": 0.20, "qual": 0.20, "desc": 0.35, "hist": 0.15, "conta": 0.10},
    "campeao":  {"pop": 0.35, "qual": 0.20, "desc": 0.10, "hist": 0.15, "conta": 0.20},
}

_DESCONTO_TETO = 60          # acima disso o % deixa de somar (desconto absurdo é suspeito)
_VENDAS_REF = 100_000        # vendas que já valem popularidade máxima
_NOTA_PRIOR, _PESO_PRIOR = 4.2, 20   # encolhe a nota de quem tem poucas avaliações
_QUEDA_MINIMA_SELO = 10      # abaixo disso a queda não é anunciada no post
_ACIMA_DO_NORMAL = 1.05      # preço > 5% acima do normal (mediana do histórico) não é boa hora de comprar
_ACIMA_DE_IGUAIS = 1.10      # preço > 10% acima da mediana de anúncios iguais: o comprador acha mais barato


def vendas_totais(o: Oferta) -> int | None:
    """Vendas em base "total": a Amazon informa só o último mês, então multiplica por um fator."""
    if o.vendas is None:
        return None
    return round(o.vendas * (config.vendas_mensal_para_total if o.vendas_mensal else 1))


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _pop(vendas: int | None) -> float:
    return _clamp(math.log10(1 + vendas) / math.log10(1 + _VENDAS_REF)) if vendas else 0.0


def _qual(o: Oferta, vendas: int | None) -> float:
    if o.nota is None:
        return 0.5
    # ML não informa nº de avaliações: estima ~1 avaliação a cada 20 vendas
    n = o.avaliacoes if o.avaliacoes is not None else (vendas or 0) / 20
    ajustada = (o.nota * n + _NOTA_PRIOR * _PESO_PRIOR) / (n + _PESO_PRIOR)
    return _clamp((ajustada - 4.0) / 0.9)


def _hist(o: Oferta, h: Historico | None, suficiente: bool) -> float:
    if not (h and suficiente):
        return 0.4  # sem histórico ainda: neutro
    return _clamp(1 - (o.preco / h.minimo - 1) * 10)   # no menor preço = 1; 10% acima = 0


def _conta(preco: float) -> float:
    """R$ 30 ou menos = 1; R$ 300 = 0,5; R$ 3.000 ou mais = 0."""
    return _clamp(1 - math.log10(max(preco, 1) / 30) / 2)


def _dias_txt(h: Historico) -> int:
    return max(1, min(config.historico_dias, int(h.dias)))


def avaliar(o: Oferta, h: Historico | None) -> str | None:
    """Aprova ou rejeita a oferta. Aprovada: preenche score/faixa/selos e retorna None.
    Rejeitada: retorna o motivo (texto curto, "categoria (detalhe)")."""
    if not o.preco or o.preco <= 0:
        return "sem preço"

    if o.nota is None:
        if config.exigir_avaliacao:
            return "sem avaliação"
    elif o.nota < config.nota_minima:
        return f"nota baixa ({o.nota:.1f})"

    vendas = vendas_totais(o)
    prova = max(o.avaliacoes or 0, vendas or 0)

    # Anúncios iguais coletados agora: fora da curva para baixo é erro de preço/golpe; para cima, o
    # comprador acha o mesmo produto mais barato (o mais em conta entre os iguais é o que passa).
    if o.preco_mercado:
        if o.preco < o.preco_mercado * config.preco_minimo_vs_mercado_pct / 100:
            return (f"preço muito abaixo de anúncios iguais (R$ {o.preco:.2f} "
                    f"vs. R$ {o.preco_mercado:.2f})")
        if o.preco > o.preco_mercado * _ACIMA_DE_IGUAIS:
            return (f"mais caro que anúncios iguais (R$ {o.preco:.2f} "
                    f"vs. R$ {o.preco_mercado:.2f})")

    # Queda real: só o histórico do bot comprova. Sem histórico suficiente, não há queda conhecida.
    suficiente = bool(h and h.suficiente())
    queda = None
    if suficiente:
        if o.preco > h.referencia * _ACIMA_DO_NORMAL:
            return f"preço acima do normal (R$ {o.preco:.2f} vs. média R$ {h.referencia:.2f})"
        queda = max(0, round(100 * (1 - o.preco / h.referencia)))

    # Preço muito abaixo do normal: liquidação de verdade, erro de preço ou vendedor duvidoso. Sem histórico,
    # o desconto anunciado serve só de ALERTA (não é exibido nem pontua): descontos enormes exigem vendedor
    # confiável (ML: conferido na página do produto) ou prova forte de vendas.
    sinal = queda if suficiente else (o.desconto or 0)
    suspeita = sinal >= config.desconto_suspeito
    if suspeita:
        prova_forte = (vendas or 0) >= config.suspeita_vendas_minimas and (o.nota or 0) >= 4.6
        vendedor_sera_checado = config.verificar_vendedor and o.plataforma == "mercadolivre"
        if not (vendedor_sera_checado or prova_forte):
            return f"desconto suspeito (-{sinal}%, sem como comprovar vendedor)"
    o.suspeita = suspeita

    campeao = (vendas is not None and vendas >= config.campeoes_vendas_minimas
               and o.nota is not None and o.nota >= config.campeoes_nota_minima)
    desconto = (queda or 0) >= config.desconto_minimo and prova >= config.prova_social_minima
    if not (campeao or desconto):
        return "critérios não atingidos"

    componentes = {
        "pop": _pop(vendas), "qual": _qual(o, vendas), "desc": _clamp((queda or 0) / _DESCONTO_TETO),
        "hist": _hist(o, h, suficiente), "conta": _conta(o.preco),
    }
    scores = {f: sum(PESOS[f][k] * v for k, v in componentes.items())
              for f, ok in (("campeao", campeao), ("desconto", desconto)) if ok}
    o.faixa = max(scores, key=scores.get)
    o.score = round(scores[o.faixa], 4)

    # O post nunca mostra o "De"/percentual da loja: só uma queda comprovada, contra o preço médio do histórico.
    o.selos = []
    o.desconto_verificado = False
    if suficiente and queda >= _QUEDA_MINIMA_SELO:
        o.preco_original, o.desconto_pct = round(h.referencia, 2), queda
        o.desconto_verificado = True
        o.selos.append(f"🔻 Caiu {queda}% em relação ao preço médio dos últimos {_dias_txt(h)} dias")
    # "menor preço" só faz sentido se o preço já esteve mais alto (produto de preço fixo estaria sempre no mínimo)
    if suficiente and h.dias >= 7 and o.preco <= h.minimo * 1.01 and h.referencia > h.minimo * 1.02:
        o.selos.append(f"📉 Menor preço dos últimos {_dias_txt(h)} dias")
    return None


def avaliar_vendedor(o: Oferta) -> str | None:
    """Confere a reputação do vendedor (já lida da página do produto). Retorna o motivo da
    rejeição ou None. Sem dados do vendedor: só reprova oferta de desconto suspeito."""
    if o.vendedor_nivel is None:
        return "vendedor não verificado" if o.suspeita else None
    if o.vendedor_nivel < config.vendedor_nivel_minimo:
        return f"vendedor com reputação baixa (nível {o.vendedor_nivel}/5)"
    confiavel = o.loja_oficial or o.vendedor_status in ("gold", "platinum")
    if o.suspeita and not confiavel:
        return "desconto suspeito (vendedor sem selo MercadoLíder Gold/Platinum nem loja oficial)"

    # "Loja oficial" vai na linha de nota e vendas (formatter); loja oficial não repete o selo de MercadoLíder
    if not o.loja_oficial and o.vendedor_status in ("gold", "platinum"):
        o.selos.append(f"🏅 Vendedor MercadoLíder {o.vendedor_status.capitalize()}")
    return None


def avaliar_todas(ofertas: list[Oferta], historicos: dict[str, Historico]) -> tuple[list[Oferta], dict[str, int]]:
    """Aplica `avaliar` a todas. Retorna (aprovadas, {motivo de rejeição: quantidade})."""
    aprovadas: list[Oferta] = []
    rejeicoes: dict[str, int] = {}
    for o in ofertas:
        motivo = avaliar(o, historicos.get(o.uid))
        if motivo is None:
            aprovadas.append(o)
        else:
            chave = motivo.split(" (")[0]
            rejeicoes[chave] = rejeicoes.get(chave, 0) + 1
    return aprovadas, rejeicoes
