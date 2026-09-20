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
    "caixa de som": ["caixa de som", "soundbar", "speaker", "boombox", "boombox3", "boombox4", "partybox"],
    "tv": ["tv", "televisao", "smart tv"],
    "camera": ["camera", "webcam", "gopro"],
    # celular e informática
    "celular": ["celular", "smartphone", "iphone", "galaxy", "redmi", "moto g", "moto e", "poco x"],
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
    "impressora": ["impressora", "garrafa de tinta", "tinta para impressora", "cartucho", "toner"],
    "controle": ["controle", "joystick", "gamepad"],
    "console": ["playstation", "ps5", "ps4", "xbox", "nintendo"],
    "smartwatch": ["smartwatch", "smartband", "smart band", "relogio inteligente", "watch", "galaxy watch"],
    "casa inteligente": ["echo dot", "alexa", "tomada inteligente", "smart plug", "lampada inteligente",
                         "lampada smart", "fechadura digital", "camera de seguranca", "campainha inteligente"],
    # casa e cozinha
    "airfryer": ["airfryer", "air fryer", "fritadeira"],
    "liquidificador": ["liquidificador", "mixer", "processador de alimentos"],
    "batedeira": ["batedeira"],
    "cafeteira": ["cafeteira", "nespresso", "dolce gusto", "chaleira"],
    "sanduicheira": ["sanduicheira", "grill", "torradeira"],
    "microondas": ["microondas", "micro ondas"],
    "geladeira": ["geladeira", "refrigerador", "freezer", "frigobar"],
    "fogao": ["fogao", "cooktop"],
    "lavadora": ["lavadora", "maquina de lavar", "lava e seca", "lava roupas", "secadora"],
    "aspirador": ["aspirador", "robo aspirador", "extratora", "spot cleaner"],
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
    "papel": ["papel higienico", "papel toalha", "papel de cozinha", "guardanapo"],
    "higiene": ["absorvente", "escova de dente", "creme dental", "pasta de dente", "enxaguante", "fio dental",
                "cotonete", "desodorante"],
    "utensilios": ["varal", "escada", "cabide", "vassoura", "rodo", "balde", "tabua de corte", "jarra", "prato",
                   "talher", "faqueiro", "assadeira"],
    "limpeza": ["sabao", "detergente", "amaciante", "desinfetante", "vaporizador", "pano", "canfora", "naftalina",
                "saco para lavar"],
    # moda
    "tenis": ["tenis", "sapatenis"],
    "calcado": ["sandalia", "chinelo", "bota", "sapato", "sapatilha"],
    "camiseta": ["camiseta", "camisa", "blusa", "regata", "polo"],
    "calca": ["calca", "bermuda", "short", "legging"],
    "agasalho": ["jaqueta", "moletom", "casaco", "capa de chuva"],
    "meia": ["meia", "cueca", "calcinha", "sutia"],
    "vestido": ["vestido", "saia", "macacao"],
    "bolsa": ["bolsa", "mochila", "mala", "carteira", "pochete"],
    "relogio": ["relogio"],
    "oculos": ["oculos"],
    # beleza e saúde
    "perfume": ["perfume", "colonia", "deo parfum", "deodorant"],
    "shampoo": ["shampoo", "condicionador", "mascara capilar", "hair spray", "leave in"],
    "hidratante": ["hidratante", "protetor solar", "serum", "sabonete", "locao"],
    "maquiagem": ["batom", "rimel", "paleta", "maquiagem", "esmalte", "corretivo", "po facial", "retoque",
                  "magic retouch", "disco de algodao", "cuticula"],
    "secador": ["secador", "chapinha", "prancha", "modelador", "escova modeladora", "escova"],
    "barbeador": ["barbeador", "aparador", "depilador", "maquina de cortar", "navalha"],
    "creatina": ["creatina", "creatine"],
    "whey": ["whey", "proteina", "albumina", "hipercalorico"],
    "vitamina": ["vitamina", "colageno", "omega", "multivitaminico", "magnesio"],
    "saude": ["medidor", "massageador", "oximetro", "termometro", "tensiometro", "glicosimetro", "pressao arterial"],
    "suplemento": ["suplemento", "pre treino", "bcaa", "termogenico", "glutamina"],
    # esporte, bebê, pet, auto, outros
    "fitness": ["halter", "esteira", "bicicleta", "bike", "elastico", "tapete yoga", "corda de pular", "manguito",
                "ciclismo"],
    "racao": ["racao", "petisco", "areia higienica", "coleira"],
    "fralda": ["fralda", "lenco umedecido", "mamadeira", "carrinho de bebe", "cadeira para auto", "cadeirinha"],
    "brinquedo": ["brinquedo", "lego", "boneca", "quebra cabeca", "boneco", "jogo de cartas", "tabuleiro",
                "pelucia", "massinha"],
    "automotivo": ["capacete", "pneu", "som automotivo"],
    "livro": ["livro", "kindle"],
    "cafe": ["cafe", "capsula", "chocolate", "azeite", "nescau", "achocolatado", "biscoito", "cereal"],
}

# Emoji de abertura do post, por tipo (tipo sem emoji, ou produto sem tipo, usa 🔥).
EMOJIS: dict[str, str] = {
    "fone": "🎧", "caixa de som": "🔊", "tv": "📺", "camera": "📷", "celular": "📱", "tablet": "📱",
    "notebook": "💻", "monitor": "🖥️", "teclado": "⌨️", "mouse": "🖱️", "ssd": "💾", "carregador": "🔌",
    "cabo": "🔌", "capinha": "📱", "roteador": "📡", "impressora": "🖨️", "controle": "🎮", "console": "🎮",
    "smartwatch": "⌚", "airfryer": "🍟", "cafeteira": "☕", "sanduicheira": "🥪", "geladeira": "🧊",
    "fogao": "🍳", "panela": "🍳", "lavadora": "🧺", "aspirador": "🧹", "ventilador": "🌬️",
    "ar condicionado": "❄️", "aquecedor": "♨️", "purificador": "💧", "chuveiro": "🚿", "balanca": "⚖️",
    "garrafa": "🥤", "marmita": "🍱", "organizador": "🗄️", "cama": "🛏️", "toalha": "🧺", "moveis": "🪑",
    "iluminacao": "💡", "ferramenta": "🔧", "papel": "🧻", "limpeza": "🧼", "tenis": "👟", "calcado": "👡",
    "camiseta": "👕", "calca": "👖", "agasalho": "🧥", "meia": "🧦", "vestido": "👗", "bolsa": "👜",
    "relogio": "⌚", "oculos": "🕶️", "perfume": "🌸", "shampoo": "🧴", "hidratante": "🧴", "maquiagem": "💄",
    "secador": "💇", "barbeador": "🪒", "creatina": "💪", "whey": "💪", "vitamina": "💊", "suplemento": "💊",
    "fitness": "🏋️", "racao": "🐶", "fralda": "👶", "brinquedo": "🧸", "automotivo": "🚗", "livro": "📚",
    "cafe": "☕", "casa inteligente": "🏠", "saude": "🩺", "higiene": "🧼", "utensilios": "🏠",
}

# Categoria do canal de cada tipo (o mix de categorias está em mix.py e no config.yaml). Tipo que não está
# aqui, ou produto sem tipo, fica sem categoria e não entra na conta do mix.
CATEGORIAS: dict[str, list[str]] = {
    "casa_e_cozinha": ["panela", "garrafa", "marmita", "organizador", "cama", "toalha", "moveis", "iluminacao",
                       "ferramenta", "cafe", "chuveiro", "balanca", "utensilios"],
    "eletrodomesticos": ["airfryer", "liquidificador", "batedeira", "cafeteira", "sanduicheira", "microondas",
                         "geladeira", "fogao", "lavadora", "aspirador", "ventilador", "ar condicionado",
                         "aquecedor", "purificador"],
    "moda": ["tenis", "calcado", "camiseta", "calca", "agasalho", "meia", "vestido", "bolsa", "relogio", "oculos"],
    "beleza": ["perfume", "shampoo", "hidratante", "maquiagem", "secador", "barbeador"],
    "limpeza_e_higiene": ["papel", "limpeza", "higiene"],
    # só acessórios, fones e casa inteligente: TV, notebook, monitor e tablet são comparados demais (ver "outros")
    "tecnologia": ["fone", "caixa de som", "carregador", "cabo", "capinha", "suporte", "mouse", "teclado", "ssd",
                   "camera", "smartwatch", "roteador", "casa inteligente"],
    "esporte": ["creatina", "whey", "suplemento", "fitness"],
    "saude": ["vitamina", "saude"],
    "brinquedos_e_bebes": ["brinquedo", "fralda"],
    "outros": ["celular", "console", "controle", "racao", "automotivo", "livro", "tv", "notebook", "monitor",
               "tablet", "impressora"],
}
_CATEGORIA_DO_TIPO = {tipo: cat for cat, tipos in CATEGORIAS.items() for tipo in tipos}

# O tipo vem do começo do título (o suficiente para o título curto do post, que tem até 75 caracteres): o final
# dos títulos da Amazon costuma ser uma lista de usos ("… para placa-mãe, relógio") que enganaria o dicionário.
_JANELA_TIPO = 80

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


_RE_UID_LIVRO = re.compile(r"^amazon:\d{9}[\dX]$")


@lru_cache(maxsize=4096)
def tipo_do_produto(titulo: str, uid: str = "") -> str | None:
    """Tipo do produto (ex.: "chuveiro") ou None se nada no título casar com o dicionário.
    Livro da Amazon não tem palavra que o denuncie no título, mas o ASIN dele é um ISBN-10: passe o `uid`
    ("amazon:8543111536") e ele vira "livro"."""
    if _RE_UID_LIVRO.match(uid):
        return "livro"
    palavras = _norm(titulo[:_JANELA_TIPO])
    for i in range(len(palavras)):
        for n in (3, 2, 1):                       # expressões maiores primeiro: "caixa de som" antes de "caixa"
            expr = " ".join(palavras[i:i + n])
            if len(palavras[i:i + n]) == n:
                achado = _EXTRAS.get(expr) or _INDICE.get(expr)
                if achado:
                    return achado
    return None


def emoji_do_produto(titulo: str, uid: str = "") -> str:
    """Emoji do tipo do produto para abrir o post; 🔥 se o tipo é desconhecido."""
    return EMOJIS.get(tipo_do_produto(titulo, uid) or "", "🔥")


def categoria_do_produto(titulo: str, uid: str = "") -> str | None:
    """Categoria do canal (casa_e_cozinha, moda, beleza…) ou None se o tipo é desconhecido."""
    return _CATEGORIA_DO_TIPO.get(tipo_do_produto(titulo, uid) or "")
