"""Para onde cada oferta vai: canal geral, canal pessoal ou grupo Apple.

Produto Apple (detectado pelo título/vendedor) vai para o grupo Apple (TELEGRAM_CHAT_ID_APPLE); o resto, para
o canal geral. O canal pessoal (TELEGRAM_CHAT_ID_PESSOAL) não recebe nada automaticamente: só os pedidos de
cliente (pedidos.yaml) marcados com `destino: pessoal` vão para lá — é o canal para pedidos que não são Apple
(ex.: ar-condicionado) e que você quer manter fora do canal geral. Cada destino tem suas próprias regras de
seleção: o geral usa o mix de categorias e as duas faixas (campeões e queda de preço); o Apple só posta quedas
de preço COMPROVADAS (Apple raramente entra em promoção grande, então o limite é menor que o do geral) e
ignora recondicionados/seminovos. Variedade e mix de cada destino são contados só sobre os posts dele. Sem um
destino configurado, tudo que iria para ele cai no canal geral.
"""
import re
from dataclasses import dataclass

from .config import config
from .models import Oferta

_NOMES = ("geral", "pessoal", "apple")


@dataclass(frozen=True)
class Destino:
    nome: str                                  # "geral" | "pessoal" | "apple" (gravado no banco)
    chat_id: str
    max_posts: int
    usar_mix: bool = True                      # mix de categorias (só o canal geral)
    queda_minima: int | None = None            # None = filtros.desconto_minimo
    so_queda: bool = False                     # sem faixa de "campeões": só queda de preço comprovada
    palavras_bloqueadas: tuple[str, ...] = ()  # além de filtros.palavras_bloqueadas


def geral() -> Destino:
    return Destino("geral", config.chat_id, config.max_posts_por_ciclo)


def pessoal() -> Destino | None:
    """Canal de pedidos de cliente que não são Apple, se estiver ligado e com o ID configurado. Só recebe
    o que um pedido (pedidos.yaml) marcar com `destino: pessoal` — nenhum produto cai aqui sozinho."""
    if not (config.pessoal_ativo and config.chat_id_pessoal):
        return None
    return Destino("pessoal", config.chat_id_pessoal, config.pessoal_max_posts, usar_mix=False)


def apple() -> Destino | None:
    """O grupo Apple, se estiver ligado e com o ID configurado."""
    if not (config.apple_ativo and config.chat_id_apple):
        return None
    return Destino("apple", config.chat_id_apple, config.apple_max_posts, usar_mix=False,
                   queda_minima=config.apple_queda_minima, so_queda=config.apple_so_queda,
                   palavras_bloqueadas=tuple(config.apple_palavras_bloqueadas))


_FABRICAS = {"geral": geral, "pessoal": pessoal, "apple": apple}


def ativos() -> list[Destino]:
    return [d for nome in _NOMES if (d := _FABRICAS[nome]())]


# ── produto Apple ────────────────────────────────────────────────────

_LINHAS = (r"iphone|ipad|macbook|imac|mac\s?mini|mac\s?studio|mac\s?pro|apple\s+watch|airpods?|airtag|apple\s+tv|"
           r"apple\s+pencil|homepod|magic\s+(?:keyboard|mouse|trackpad)|vision\s+pro")
_RE_LINHA = re.compile(rf"\b(?:{_LINHAS})\b", re.I)
_RE_APPLE = re.compile(r"\bapple\b", re.I)
_RE_COMECA = re.compile(r"^\s*(?:apple|iphone|ipad|macbook|imac|airpods?|airtag|mac\s?mini|mac\s?studio|homepod)\b", re.I)
# Acessório de terceiros ("Capa para iPhone", "Película compatível com Apple Watch"): não é produto Apple
_RE_TERCEIROS = re.compile(r"\b(?:para|p/|compat[ií]vel|capa|capinha|case|pel[ií]cula|pulseira|correia|protetor|vidro|"
                           r"suporte|refil|adaptador)\b", re.I)


def e_apple(o: Oferta) -> bool:
    """Produto da Apple (e não acessório de terceiros para produto Apple). O título decide; o vendedor
    "Apple" (loja oficial no ML) também."""
    if (o.vendedor or "").strip().lower() in {"apple", "apple brasil"}:
        return True
    titulo = o.titulo or ""
    if not (_RE_LINHA.search(titulo) or _RE_APPLE.search(titulo)):
        return False
    return bool(_RE_COMECA.match(titulo)) or not _RE_TERCEIROS.search(titulo)


def _nome_do_destino(o: Oferta, ligados: dict[str, bool]) -> str:
    """Pedido com destino próprio (pedidos.yaml) vai para ele, se estiver configurado; senão cai no geral.
    Sem pedido com destino, o produto decide (Apple -> grupo Apple; "pessoal" nunca é automático)."""
    if o.destino in _NOMES:
        return o.destino if (o.destino == "geral" or ligados.get(o.destino)) else "geral"
    return "apple" if ligados.get("apple") and e_apple(o) else "geral"


def dividir(ofertas: list[Oferta]) -> dict[str, list[Oferta]]:
    """{destino: ofertas}. Sem um destino configurado, o que iria para ele cai no geral."""
    destinos = {nome: _FABRICAS[nome]() for nome in _NOMES}
    ligados = {nome: d is not None for nome, d in destinos.items()}
    grupos: dict[str, list[Oferta]] = {nome: [] for nome in _NOMES}
    for o in ofertas:
        grupos[_nome_do_destino(o, ligados)].append(o)
    return grupos


def chat_para(o: Oferta) -> tuple[str, str]:
    """(destino, chat_id) de uma oferta avulsa (conversor manual)."""
    destinos = {nome: _FABRICAS[nome]() for nome in _NOMES}
    ligados = {nome: d is not None for nome, d in destinos.items()}
    nome = _nome_do_destino(o, ligados)
    return nome, (destinos[nome].chat_id if nome != "geral" else config.chat_id)
