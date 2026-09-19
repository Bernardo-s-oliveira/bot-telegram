import math
from html import escape

from .models import Oferta

_PLATAFORMA = {
    "mercadolivre": "💛 Mercado Livre",
    "shopee": "🧡 Shopee",
    "amazon": "📦 Amazon",
}


def preco_br(valor: float) -> str:
    return "R$ " + f"{valor:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def contagem_br(n: int) -> str:
    """1234 -> "1,2 mil"; 10000 -> "10 mil"; 1500000 -> "1,5 mi" (sempre arredonda para baixo)."""
    for base, sufixo in ((1_000_000, "mi"), (1_000, "mil")):
        if n >= base:
            valor = math.floor(n / base * 10) / 10
            return f"{valor:g}".replace(".", ",") + f" {sufixo}"
    return str(n)


def _linha_prova_social(o: Oferta) -> str:
    partes = []
    if o.nota:
        partes.append(f"⭐ {o.nota:.1f}".replace(".", ","))
    if o.vendas:
        rotulo = "compras no último mês" if o.vendas_mensal else "vendidos"
        partes.append(f"🏆 +{contagem_br(o.vendas)} {rotulo}")
    return " · ".join(partes)


def montar_caption(o: Oferta) -> str:
    linhas = [f"🔥 <b>{escape(o.titulo[:180])}</b>", ""]

    if o.preco and o.preco_original and o.preco_original > o.preco:
        if o.desconto_verificado:
            linhas.append(f"❌ De: <s>{preco_br(o.preco_original)}</s>")
            selo = f"  🔻 <b>-{o.desconto}%</b>" if o.desconto else ""
            linhas.append(f"✅ Por: <b>{preco_br(o.preco)}</b>{selo}")
        else:
            # sem histórico que comprove, o "De" é só a palavra da loja: não é apresentado como fato
            linhas.append(f"✅ <b>{preco_br(o.preco)}</b>")
            linhas.append(f"🏷 Loja anuncia -{o.desconto}% (de {preco_br(o.preco_original)})")
    elif o.preco:
        selo = f"  🔻 <b>-{o.desconto}%</b>" if o.desconto else ""
        linhas.append(f"✅ <b>{preco_br(o.preco)}</b>{selo}")

    social = _linha_prova_social(o)
    if social:
        linhas.append(social)
    linhas += [escape(s) for s in o.selos]
    if o.extra:
        linhas.append(escape(o.extra))

    linhas += ["", _PLATAFORMA.get(o.plataforma, o.plataforma)]
    return "\n".join(linhas)
