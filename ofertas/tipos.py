"""Tipo de produto a partir do título (creatina, chuveiro, balança…), para variar o canal.

As fontes não entregam categoria de forma confiável, então o tipo vem de um dicionário de palavras.
Vale a primeira palavra do título que casar (o tipo costuma vir no começo: "Balança Digital…",
"Marca Fone Bluetooth…"). Produto que não casa com nada fica sem tipo e não sofre a regra de variedade.

Para cobrir mais produtos, acrescente linhas em TIPOS (ou use `variedade.tipos_extras` no config.yaml).
Palavras sem acento e no singular; expressões de até 3 palavras separadas por espaço.
"""
import re
import unicodedata
from functools import lru_cache

TIPOS: dict[str, list[str]] = {
    # áudio e vídeo
    "fone": ["fone", "headphone", "headset", "earbud", "earphone", "airpod", "tws", "galaxy buds"],
    "caixa de som": ["caixa de som", "soundbar", "speaker", "boombox"],
    "tv": ["tv", "televisao", "smart tv"],
    "camera": ["camera", "webcam", "gopro"],
    # celular e informática
    "celular": ["celular", "smartphone", "iphone", "galaxy", "redmi"],
    "tablet": ["tablet", "ipad", "galaxy tab"],
    "notebook": ["notebook", "laptop", "macbook"],
    "monitor": ["monitor", "projetor"],
    "teclado": ["teclado"],
    "mouse": ["mouse"],
    "ssd": ["ssd", "hd externo", "pendrive", "cartao de memoria", "placa mae", "placa de video", "memoria ram"],
    "carregador": ["carregador", "power bank", "powerbank"],
    "cabo": ["cabo"],
    "capinha": ["capinha", "capa", "case", "pelicula"],
    "suporte": ["suporte"],
    "roteador": ["roteador", "repetidor"],
    "impressora": ["impressora"],
    "controle": ["controle", "joystick", "gamepad"],
    "console": ["playstation", "ps5", "ps4", "xbox", "nintendo"],
    "smartwatch": ["smartwatch", "smartband", "smart band", "relogio inteligente", "watch", "galaxy watch"],
    # casa e cozinha
    "airfryer": ["airfryer", "air fryer", "fritadeira"],
    "liquidificador": ["liquidificador", "mixer", "processador de alimentos"],
    "batedeira": ["batedeira"],
    "cafeteira": ["cafeteira", "nespresso", "dolce gusto"],
    "sanduicheira": ["sanduicheira", "grill", "torradeira"],
    "microondas": ["microondas", "micro ondas"],
    "geladeira": ["geladeira", "refrigerador", "freezer", "frigobar"],
    "fogao": ["fogao", "cooktop"],
    "lavadora": ["lavadora", "maquina de lavar", "lava e seca", "lava roupas", "secadora"],
    "aspirador": ["aspirador", "robo aspirador"],
    "ventilador": ["ventilador", "circulador", "climatizador"],
    "ar condicionado": ["ar condicionado", "ar split", "split inverter"],
    "aquecedor": ["aquecedor"],
    "purificador": ["purificador", "bebedouro", "filtro de agua"],
    "chuveiro": ["chuveiro", "ducha", "torneira"],
    "balanca": ["balanca"],
    "panela": ["panela", "frigideira", "cacarola", "jogo de panelas"],
    "garrafa": ["garrafa", "copo", "caneca", "squeeze"],
    "marmita": ["marmita", "pote", "lancheira"],
    "organizador": ["organizador", "lixeira", "prateleira"],
    "cama": ["colchao", "cama", "travesseiro", "edredom", "cobertor", "lencol", "manta"],
    "toalha": ["toalha", "tapete", "cortina"],
    "moveis": ["cadeira", "mesa", "sofa", "poltrona", "estante", "rack", "armario", "guarda roupa"],
    "iluminacao": ["lampada", "luminaria", "fita led", "refletor"],
    "ferramenta": ["furadeira", "parafusadeira", "alicate", "serra", "jogo de ferramentas", "solda", "fita dupla face"],
    "limpeza": ["sabao", "detergente", "amaciante", "desinfetante", "papel higienico", "vaporizador", "pano"],
    # moda
    "tenis": ["tenis", "sapatenis"],
    "calcado": ["sandalia", "chinelo", "bota", "sapato", "sapatilha"],
    "camiseta": ["camiseta", "camisa", "blusa", "regata", "polo"],
    "calca": ["calca", "bermuda", "short", "legging"],
    "agasalho": ["jaqueta", "moletom", "casaco"],
    "meia": ["meia", "cueca", "calcinha", "sutia"],
    "vestido": ["vestido", "saia", "macacao"],
    "bolsa": ["bolsa", "mochila", "mala", "carteira", "pochete"],
    "relogio": ["relogio"],
    "oculos": ["oculos"],
    # beleza e saúde
    "perfume": ["perfume", "colonia", "deo parfum", "deodorant"],
    "shampoo": ["shampoo", "condicionador", "mascara capilar"],
    "hidratante": ["hidratante", "creme", "protetor solar", "serum", "sabonete", "locao"],
    "maquiagem": ["batom", "rimel", "paleta", "maquiagem", "esmalte", "corretivo"],
    "secador": ["secador", "chapinha", "prancha", "modelador", "escova modeladora", "escova"],
    "barbeador": ["barbeador", "aparador", "depilador", "maquina de cortar"],
    "creatina": ["creatina", "creatine"],
    "whey": ["whey", "proteina", "albumina", "hipercalorico"],
    "vitamina": ["vitamina", "colageno", "omega", "multivitaminico", "magnesio"],
    "suplemento": ["suplemento", "pre treino", "bcaa", "termogenico", "glutamina"],
    # esporte, bebê, pet, auto, outros
    "fitness": ["halter", "esteira", "bicicleta", "bike", "elastico", "tapete yoga", "corda de pular"],
    "racao": ["racao", "petisco", "areia higienica", "coleira"],
    "fralda": ["fralda", "lenco umedecido", "mamadeira", "carrinho de bebe"],
    "brinquedo": ["brinquedo", "lego", "boneca", "quebra cabeca", "boneco"],
    "automotivo": ["capacete", "pneu", "som automotivo"],
    "livro": ["livro", "kindle"],
    "cafe": ["cafe", "capsula", "chocolate", "azeite"],
}

_STOP_PLURAL = {"mais", "gas", "seis", "dois", "tres", "pais", "lapis", "oculos", "atlas"}


def _norm(texto: str) -> list[str]:
    """Minúsculas, sem acento, em palavras; plural simples ("fones" -> "fone")."""
    t = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    palavras = re.findall(r"[a-z0-9]+", t)
    return [p[:-1] if len(p) > 3 and p.endswith("s") and p not in _STOP_PLURAL and not p.endswith("ss") else p
            for p in palavras]


def _indice(tipos: dict[str, list[str]]) -> dict[str, str]:
    """{expressão normalizada: tipo}."""
    return {" ".join(_norm(kw)): tipo for tipo, kws in tipos.items() for kw in kws}


_INDICE = _indice(TIPOS)
_EXTRAS: dict[str, str] = {}


def adicionar_extras(extras: dict[str, list[str]]) -> None:
    """Palavras extras vindas do config.yaml: {tipo: [palavras]}."""
    _EXTRAS.clear()
    _EXTRAS.update(_indice({str(k): [str(p) for p in (v or [])] for k, v in (extras or {}).items()}))
    tipo_do_produto.cache_clear()


@lru_cache(maxsize=4096)
def tipo_do_produto(titulo: str) -> str | None:
    """Tipo do produto (ex.: "chuveiro") ou None se nada no título casar com o dicionário."""
    palavras = _norm(titulo)
    for i in range(len(palavras)):
        for n in (3, 2, 1):                       # expressões maiores primeiro: "caixa de som" antes de "caixa"
            expr = " ".join(palavras[i:i + n])
            if len(palavras[i:i + n]) == n:
                achado = _EXTRAS.get(expr) or _INDICE.get(expr)
                if achado:
                    return achado
    return None
