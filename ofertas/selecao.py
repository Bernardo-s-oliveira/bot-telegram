"""Seleção orientada ao comprador: só passa oferta que se sustenta nos dados.

Duas faixas de oferta:
- "campeao": muitas vendas + boa nota + preço em conta (mesmo com desconto modesto);
- "desconto": desconto grande e *verificado* contra o histórico de preços que o bot
  guarda, e com alguma prova social (vendas/avaliações).

Antes de pontuar, `avaliar` rejeita o que engana o comprador: sem avaliação, nota baixa,
"desconto" inflado (preço "de" que o produto nunca teve) e preço acima do normal.
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
_QUEDA_MINIMA_SELO = 5       # abaixo disso não vale anunciar "abaixo do preço médio"


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

    # Anúncio com preço bem abaixo de todos os iguais coletados: típico de erro de preço ou golpe.
    if o.preco_mercado and o.preco < o.preco_mercado * config.preco_minimo_vs_mercado_pct / 100:
        return (f"preço muito abaixo de anúncios iguais (R$ {o.preco:.2f} "
                f"vs. R$ {o.preco_mercado:.2f})")

    # Desconto real: o anunciado, limitado pelo que o preço de fato caiu. A referência é o
    # histórico do bot; enquanto ele não existe, os anúncios iguais coletados no ciclo.
    reclamado = o.desconto or 0
    suficiente = bool(h and h.suficiente())
    referencia = h.referencia if suficiente else o.preco_mercado
    queda = None
    efetivo = reclamado
    if referencia:
        queda = max(0, round(100 * (1 - o.preco / referencia)))
        efetivo = min(reclamado, queda) if reclamado else queda
        if config.rejeitar_desconto_falso and reclamado >= 15 and efetivo < reclamado * 0.4:
            fonte = "histórico" if suficiente else "anúncios iguais"
            return f"desconto inflado (anuncia -{reclamado}%, real -{queda}% pelo {fonte})"
    if not suficiente and reclamado > config.desconto_max_sem_historico:
        return f"desconto alto sem histórico (anuncia -{reclamado}%)"  # reavaliado quando o histórico existir

    # Desconto muito alto, mesmo comprovado: ou é liquidação de verdade, ou preço errado/vendedor duvidoso.
    suspeita = efetivo >= config.desconto_suspeito
    if suspeita:
        prova_forte = (vendas or 0) >= config.suspeita_vendas_minimas and (o.nota or 0) >= 4.6
        vendedor_sera_checado = config.verificar_vendedor and o.plataforma == "mercadolivre"
        if not (suficiente and (vendedor_sera_checado or prova_forte)):
            return f"desconto suspeito (-{efetivo}%, sem como comprovar vendedor)"
    o.suspeita = suspeita

    campeao = (vendas is not None and vendas >= config.campeoes_vendas_minimas
               and o.nota is not None and o.nota >= config.campeoes_nota_minima
               and efetivo >= config.campeoes_desconto_minimo)
    desconto = efetivo >= config.desconto_minimo and prova >= config.prova_social_minima
    if not (campeao or desconto):
        return "critérios não atingidos"

    componentes = {
        "pop": _pop(vendas), "qual": _qual(o, vendas), "desc": _clamp(efetivo / _DESCONTO_TETO),
        "hist": _hist(o, h, suficiente), "conta": _conta(o.preco),
    }
    scores = {f: sum(PESOS[f][k] * v for k, v in componentes.items())
              for f, ok in (("campeao", campeao), ("desconto", desconto)) if ok}
    o.faixa = max(scores, key=scores.get)
    o.score = round(scores[o.faixa], 4)

    # Só mostra "De" como fato quando o histórico o comprova; do contrário o post diz que é a loja que anuncia.
    o.selos = []
    o.desconto_verificado = False
    if suficiente and queda is not None and queda >= _QUEDA_MINIMA_SELO and o.preco < h.referencia:
        if reclamado == 0 or reclamado > queda + 5:
            o.preco_original, o.desconto_pct = round(h.referencia, 2), queda
        o.desconto_verificado = True
        o.selos.append(f"✅ {queda}% abaixo do preço médio dos últimos {_dias_txt(h)} dias")
    if suficiente and h.dias >= 7 and o.preco <= h.minimo * 1.01:
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

    if o.loja_oficial:
        o.selos.append("🛡️ Loja oficial")
    elif o.vendedor_status in ("gold", "platinum"):
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
