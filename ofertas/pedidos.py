"""Pedidos de clientes: produtos que clientes pediram, listados em pedidos.yaml.

A cada ciclo o bot procura cada pedido (Mercado Livre e Amazon). O anúncio que combina com as regras de título
e está DENTRO da faixa de preço do pedido vira candidato prioritário: passa na frente das ofertas normais e
ignora o mix de categorias e a regra de variedade, mas continua passando pelos portões de qualidade (nota,
vendedor confiável, sem importado). Anúncio fora da faixa não é postado; o bot só relata o menor preço achado.

O arquivo é relido a cada ciclo: dá para editar com o bot rodando.
"""
import logging
import re
import unicodedata
from dataclasses import dataclass, field

import yaml

from .config import BASE_DIR, config
from .formatter import preco_br
from .models import Oferta

log = logging.getLogger("ofertas.pedidos")

# Palavras que desqualificam qualquer pedido (produto usado/defeituoso não é o que o cliente pediu)
EXCLUIR_SEMPRE = ("usado", "usada", "seminovo", "recondicionado", "defeito", "sucata", "retirada de pecas",
                  "para pecas", "importado")


@dataclass
class Pedido:
    nome: str
    buscas: list[str]
    preco_min: float
    preco_max: float
    deve_ter: list[str] = field(default_factory=list)       # TODAS estas palavras têm de estar no título
    qualquer_de: list[str] = field(default_factory=list)    # pelo menos UMA destas
    nao_deve_ter: list[str] = field(default_factory=list)   # NENHUMA destas
    ativo: bool = True                                      # False: pausado (não procura, não prioriza)


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, e unidades coladas ao número ("16 GB" -> "16gb", "2 x 8" -> "2x8")."""
    t = unicodedata.normalize("NFKD", (texto or "").lower()).encode("ascii", "ignore").decode()
    t = re.sub(r"(\d)\s+(gb|mb|tb|mhz|ghz|w|v)\b", r"\1\2", t)
    t = re.sub(r"(\d)\s*x\s*(\d)", r"\1x\2", t)
    return re.sub(r"\s+", " ", t).strip()


def _tem(termo: str, titulo_norm: str) -> bool:
    """A palavra/expressão está no título como palavra inteira. Termo terminado em `*` aceita qualquer
    continuação: "a520*" casa "a520", "a520m" e "a520m-k"; "5600" NÃO casa "5600x" (é outro produto)."""
    prefixo = termo.endswith("*")
    base = normalizar(termo.rstrip("*"))
    if not base:
        return False
    fim = "" if prefixo else r"(?![a-z0-9])"
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(base) + fim, titulo_norm))


def combina(p: Pedido, titulo: str) -> bool:
    """O título combina com as regras do pedido (sem olhar o preço)."""
    t = normalizar(titulo)
    if any(_tem(x, t) for x in EXCLUIR_SEMPRE) or any(_tem(x, t) for x in p.nao_deve_ter):
        return False
    if not all(_tem(x, t) for x in p.deve_ter):
        return False
    return not p.qualquer_de or any(_tem(x, t) for x in p.qualquer_de)


def _lista(valor) -> list[str]:
    if valor is None:
        return []
    return [str(x).strip() for x in (valor if isinstance(valor, list) else [valor]) if str(x).strip()]


def _ligado(valor) -> bool:
    """`ativo:` do pedido. Ausente = ligado; aceita false/no/off/0 e também "não" e "pausado"."""
    if isinstance(valor, str):
        return normalizar(valor) not in {"false", "no", "nao", "off", "0", "pausado", "pausar"}
    return True if valor is None else bool(valor)


def carregar() -> list[Pedido]:
    """Lê pedidos.yaml (inclusive os pausados, marcados com `ativo=False`). Arquivo ausente = sem pedidos;
    pedido inválido é ignorado com aviso no log."""
    caminho = BASE_DIR / config.pedidos_arquivo
    if not (config.pedidos_ativo and caminho.exists()):
        return []
    try:
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as e:
        log.error("Não consegui ler %s: %s", caminho.name, e)
        return []
    pedidos: list[Pedido] = []
    for i, e in enumerate(dados.get("pedidos") or [], 1):
        try:
            faixa = e.get("preco") or []
            nome, buscas = str(e.get("nome") or "").strip(), _lista(e.get("buscas"))
            if not nome or not buscas or len(faixa) != 2:
                raise ValueError("precisa de nome, buscas e preco: [mínimo, máximo]")
            baixo, alto = sorted(float(x) for x in faixa)
            pedidos.append(Pedido(nome, buscas, baixo, alto, _lista(e.get("deve_ter")),
                                  _lista(e.get("qualquer_de")), _lista(e.get("nao_deve_ter")),
                                  _ligado(e.get("ativo"))))
        except (AttributeError, TypeError, ValueError) as err:
            log.warning("%s: pedido #%d ignorado — %s", caminho.name, i, err)
    return pedidos


def marcar(achadas: list[Oferta], pedidos: list[Pedido], todas: list[Oferta]) -> list[str]:
    """Marca em `todas` as ofertas que atendem a algum pedido (título E faixa de preço) e devolve o relatório
    (uma linha por pedido). Oferta que já está em `todas` é marcada no lugar, sem duplicar."""
    por_uid = {o.uid: o for o in todas}
    relatorio = []
    for p in pedidos:
        combinam = [o for o in achadas if o.preco and combina(p, o.titulo)]
        vistos = list({o.uid: o for o in combinam}.values())
        na_faixa = [o for o in vistos if p.preco_min <= o.preco <= p.preco_max]
        for o in na_faixa:
            alvo = por_uid.get(o.uid)
            if alvo is None:
                todas.append(o)
                por_uid[o.uid] = alvo = o
            alvo.pedido = p.nome
        faixa = f"R$ {p.preco_min:g}–{p.preco_max:g}"
        if not vistos:
            relatorio.append(f"Pedido '{p.nome}': nenhum anúncio combina com as regras do título")
            continue
        menor = min(vistos, key=lambda o: o.preco)
        if na_faixa:
            baratos = min(na_faixa, key=lambda o: o.preco)
            relatorio.append(f"Pedido '{p.nome}': {len(na_faixa)} de {len(vistos)} anúncios na faixa {faixa} "
                             f"(menor {preco_br(baratos.preco)})")
        else:
            relatorio.append(f"Pedido '{p.nome}': {len(vistos)} anúncios, nenhum na faixa {faixa} — "
                             f"menor preço {preco_br(menor.preco)}; aguardando o preço cair")
    return relatorio


def coletar(todas: list[Oferta]) -> list[str]:
    """Busca cada pedido nas fontes ativas e marca em `todas` os candidatos. Devolve o relatório por pedido."""
    from .sources import amazon, mercadolivre    # import tardio: evita ciclo e só carrega o navegador se preciso

    todos = carregar()
    pausados = [f"Pedido '{p.nome}': pausado (ativo: false) — não está sendo procurado" for p in todos if not p.ativo]
    pedidos = [p for p in todos if p.ativo]
    if not pedidos:
        return pausados
    termos = list(dict.fromkeys(t for p in pedidos for t in p.buscas))
    achadas: list[Oferta] = []
    if config.fonte_ml.get("ativa") and mercadolivre.tem_sessao():
        try:
            achadas += mercadolivre.buscar_termos(termos)
        except Exception as e:
            log.error("Busca de pedidos no Mercado Livre: %s", e)
    if config.fonte_amazon.get("ativa") and config.amazon_tag:
        try:
            achadas += amazon.buscar_termos(termos, config.apple_amazon_termos_por_ciclo)
        except Exception as e:
            log.error("Busca de pedidos na Amazon: %s", e)
    return marcar(achadas, pedidos, todas) + pausados
