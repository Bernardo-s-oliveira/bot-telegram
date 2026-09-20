"""Mix de categorias: cada categoria tem uma fatia-alvo dos posts (config.yaml `mix.metas`).

O bot olha os últimos `mix.janela_posts` posts, vê quais categorias estão abaixo da meta e dá um bônus ao
score das ofertas delas (e desconta as que já passaram da meta). O mix é uma preferência, não uma cota rígida:
sem candidato bom numa categoria, a vaga vai para outra. Duas categorias têm regras próprias:
- tecnologia: só até `mix.tecnologia_preco_maximo` (acessórios, fones e casa inteligente; TV, notebook,
  monitor e tablet caem em "outros" — ver tipos.CATEGORIAS);
- eletrodomésticos: só com queda de preço comprovada no histórico (`desconto_verificado`), porque o
  tíquete é alto e o risco de "de" inflado é maior.
Produto sem tipo conhecido (livros de outras lojas, itens raros) forma a categoria "sem_categoria", com
fatia própria e pequena (`mix.sem_categoria`): continua podendo ser postado, mas não engole a composição.
"""
from collections import Counter

from . import db
from .config import config
from .models import Oferta
from .tipos import categoria_do_produto

SEM_CATEGORIA = "sem_categoria"   # produto que o dicionário de tipos não reconhece


def metas() -> dict[str, float]:
    """Metas normalizadas (somam 1), incluindo a fatia de "sem_categoria"."""
    brutas = {**config.mix_metas, SEM_CATEGORIA: config.mix_sem_categoria}
    total = sum(v for v in brutas.values() if v > 0)
    return {c: v / total for c, v in brutas.items() if v > 0} if total else {}


def categoria(o: Oferta) -> str:
    """Categoria da oferta para o mix ("sem_categoria" se o tipo é desconhecido)."""
    return categoria_do_produto(o.titulo, o.uid) or SEM_CATEGORIA


def contagem_recente() -> Counter:
    """Posts por categoria entre os últimos `janela_posts`."""
    contagem: Counter = Counter()
    for uid, titulo in db.ultimos_titulos(config.mix_janela_posts):
        contagem[categoria_do_produto(titulo, uid) or SEM_CATEGORIA] += 1
    return contagem


def bonus(o: Oferta, contagem: Counter, alvo: dict[str, float] | None = None) -> float:
    """Bônus (ou desconto) no score: força × (meta − fatia atual da categoria)."""
    if not config.mix_ativo:
        return 0.0
    alvo = metas() if alvo is None else alvo
    cat = categoria(o)
    if cat not in alvo:
        return 0.0
    total = sum(contagem.values())
    fatia = contagem[cat] / total if total else 0.0
    return config.mix_forca * (alvo[cat] - fatia)


def motivo_de_exclusao(o: Oferta) -> str | None:
    """Regras das categorias com restrição. Retorna o motivo da exclusão ou None. Deve rodar DEPOIS de
    `selecao.avaliar` (usa `desconto_verificado`)."""
    if not config.mix_ativo:
        return None
    cat = categoria_do_produto(o.titulo, o.uid)
    if cat == "tecnologia" and config.mix_tecnologia_preco_max and o.preco and o.preco > config.mix_tecnologia_preco_max:
        return f"tecnologia acima do teto de R$ {config.mix_tecnologia_preco_max:g}"
    if cat == "eletrodomesticos" and config.mix_eletro_exige_queda and not o.desconto_verificado:
        return "eletrodoméstico sem queda de preço comprovada"
    return None
