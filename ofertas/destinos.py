"""Para onde cada oferta vai: canal geral ou grupo Apple.

Produto Apple vai para o grupo Apple (TELEGRAM_CHAT_ID_APPLE); o resto, para o canal geral. Cada destino tem
suas próprias regras de seleção: o geral usa o mix de categorias e as duas faixas (campeões e queda de preço);
o Apple só posta quedas de preço COMPROVADAS (Apple raramente entra em promoção grande, então o limite é
menor que o do geral) e ignora recondicionados/seminovos. Variedade e mix de cada destino são contados só
sobre os posts dele. Sem o grupo configurado, tudo continua indo para o canal geral.
"""
import re
from dataclasses import dataclass

from .config import config
from .models import Oferta


@dataclass(frozen=True)
class Destino:
    nome: str                                  # "geral" | "apple" (gravado no banco)
    chat_id: str
    max_posts: int
    usar_mix: bool = True                      # mix de categorias (só o canal geral)
    queda_minima: int | None = None            # None = filtros.desconto_minimo
    so_queda: bool = False                     # sem faixa de "campeões": só queda de preço comprovada
    palavras_bloqueadas: tuple[str, ...] = ()  # além de filtros.palavras_bloqueadas


def geral() -> Destino:
    return Destino("geral", config.chat_id, config.max_posts_por_ciclo)


def apple() -> Destino | None:
    """O grupo Apple, se estiver ligado e com o ID configurado."""
    if not (config.apple_ativo and config.chat_id_apple):
        return None
    return Destino("apple", config.chat_id_apple, config.apple_max_posts, usar_mix=False,
                   queda_minima=config.apple_queda_minima, so_queda=config.apple_so_queda,
                   palavras_bloqueadas=tuple(config.apple_palavras_bloqueadas))


def ativos() -> list[Destino]:
    return [geral()] + ([d] if (d := apple()) else [])


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


def dividir(ofertas: list[Oferta]) -> dict[str, list[Oferta]]:
    """{destino: ofertas}. Sem grupo Apple configurado, tudo vai para o geral."""
    grupos: dict[str, list[Oferta]] = {"geral": [], "apple": []}
    apple_ligado = apple() is not None
    for o in ofertas:
        grupos["apple" if apple_ligado and e_apple(o) else "geral"].append(o)
    return grupos


def chat_para(o: Oferta) -> tuple[str, str]:
    """(destino, chat_id) de uma oferta avulsa (conversor manual)."""
    d = apple()
    if d and e_apple(o):
        return d.nome, d.chat_id
    return "geral", config.chat_id
