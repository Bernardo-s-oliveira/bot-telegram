import math
import re
from html import escape

from .models import Oferta
from .tipos import tipo_do_produto

_PLATAFORMA = {
    "mercadolivre": "💛 Mercado Livre",
    "shopee": "🧡 Shopee",
    "amazon": "📦 Amazon",
}


TITULO_MAX = 75   # títulos de loja passam de 150 caracteres: o post mostra só o começo, em fim de palavra

_RE_SEPARADOR = re.compile(r"\s+[-–—|]\s+|,\s+|\s+\(")
# Código de modelo colado no começo: letras + hífen/sublinhado + números ("Eps-6905", "TB-21PX") ou
# letras + 4 ou mais números ("EPS6905"). Nomes de modelo curtos ("S24", "A36") NÃO entram: são o produto.
_RE_CODIGO = re.compile(r"^[A-Za-z]{1,6}(?:[-_]\d{2,}|\d{4,})[A-Za-z0-9-]{0,6}$")
_PENDURADOS = {"de", "da", "do", "das", "dos", "e", "com", "sem", "para", "por", "em", "a", "o", "as", "os",
               "no", "na", "nos", "nas", "ou", "c/", "p/", "+", "-", "–", "—", "|", "&", "kit"}


def _sem_codigo_inicial(titulo: str) -> str:
    """Tira o código de modelo que abre alguns títulos ("Eps-6905 Balança Digital…"), que não diz nada a
    quem lê. Só quando o resto do título já diz o que é o produto."""
    primeira, _, resto = titulo.partition(" ")
    if _RE_CODIGO.match(primeira) and tipo_do_produto(resto):
        return resto.lstrip(" -–—|,")
    return titulo


def _limpar_ponta(texto: str) -> str:
    """Remove pontuação, conectivos e número solto (de "2 Baterias") no fim: "… Fone sem" -> "… Fone"."""
    while True:
        antes = texto
        texto = texto.rstrip(" ,;:-–—|/(&+")
        palavras = texto.rsplit(" ", 1)
        if len(palavras) == 2 and (palavras[1].lower() in _PENDURADOS or re.fullmatch(r"\d{1,2}", palavras[1])):
            texto = palavras[0]
        if texto == antes:
            return texto


def titulo_curto(titulo: str, limite: int = TITULO_MAX) -> str:
    """Título legível: sem código de modelo no início e, se longo, cortado em fim de palavra.
    Prefere cortar num separador natural ("Marca Modelo Fone Bluetooth, 30h de bateria…" -> até a vírgula)."""
    t = _sem_codigo_inicial(re.sub(r"\s+", " ", titulo).strip())
    if len(t) <= limite:
        return t
    sep = next((m for m in _RE_SEPARADOR.finditer(t) if 30 <= m.start() <= limite), None)
    if sep:
        corte = t[:sep.start()]
    else:
        corte = t[:limite]
        if t[limite] != " " and " " in corte:
            corte = corte.rsplit(" ", 1)[0]
    return _limpar_ponta(corte) or t[:limite]


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
    linhas = [f"🔥 <b>{escape(titulo_curto(o.titulo))}</b>", ""]

    if o.preco and o.preco_original and o.preco_original > o.preco:
        if o.desconto_verificado:
            linhas.append(f"❌ De: <s>{preco_br(o.preco_original)}</s>")
            selo = f"  🔻 <b>-{o.desconto}%</b>" if o.desconto else ""
            linhas.append(f"💰 Por: <b>{preco_br(o.preco)}</b>{selo}")
        else:
            # sem histórico que comprove, o "De" é só a palavra da loja: não é apresentado como fato
            linhas.append(f"💰 <b>{preco_br(o.preco)}</b>")
            linhas.append(f"🏷 Loja anuncia -{o.desconto}% (de {preco_br(o.preco_original)})")
    elif o.preco:
        selo = f"  🔻 <b>-{o.desconto}%</b>" if o.desconto else ""
        linhas.append(f"💰 <b>{preco_br(o.preco)}</b>{selo}")

    social = _linha_prova_social(o)
    if social:
        linhas.append(social)
    linhas += [escape(s) for s in o.selos]
    if o.cupom:
        linhas.append(escape(o.cupom))
    if o.extra:
        linhas.append(escape(o.extra))

    linhas += ["", _PLATAFORMA.get(o.plataforma, o.plataforma)]
    return "\n".join(linhas)
