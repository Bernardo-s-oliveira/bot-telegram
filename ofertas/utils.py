import re

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def sessao() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"})
    return s


def parse_preco_br(texto: str | None) -> float | None:
    """Converte "R$ 1.234,56" / "1.234" / "56,43" em float."""
    t = re.sub(r"[^\d,.]", "", texto or "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.rsplit(".", 1)[1]) == 3):
        t = t.replace(".", "")  # ponto de milhar, sem centavos
    try:
        return float(t)
    except ValueError:
        return None


_SUFIXOS = {"mil": 1_000, "k": 1_000, "mi": 1_000_000, "m": 1_000_000,
            "milhao": 1_000_000, "milhoes": 1_000_000, "milhão": 1_000_000, "milhões": 1_000_000}
_RE_CONTAGEM = re.compile(r"(\d[\d.,]*)\s*(milh[õo]es|milh[ãa]o|mil|mi|[kKM])?(?![a-zA-Zà-ú])")


def parse_contagem(texto: str | None) -> int | None:
    """Converte contagens abreviadas em inteiro: "+10mil" -> 10000, "12,4 mil" -> 12400,
    "+1M" -> 1000000, "Mais de 600" -> 600, "1.234" -> 1234. Sem número: None."""
    m = _RE_CONTAGEM.search(texto or "")
    if not m:
        return None
    numero, sufixo = m.group(1).rstrip(".,"), m.group(2)
    if not numero:
        return None
    if sufixo:
        # com sufixo, a vírgula/ponto é decimal ("12,4 mil"); ponto de milhar não ocorre aqui
        try:
            return round(float(numero.replace(",", ".")) * _SUFIXOS[sufixo.lower()])
        except ValueError:
            return None
    return int(re.sub(r"[.,]", "", numero))  # sem sufixo: separadores são de milhar


def parse_nota(texto: str | None) -> float | None:
    """Primeira nota 0–5 do texto: "4,8 de 5 estrelas" / "4.9 | +10mil" -> 4.8 / 4.9."""
    m = re.search(r"(?<![\d.,])(\d(?:[.,]\d)?)(?![\d])", texto or "")
    if not m:
        return None
    nota = float(m.group(1).replace(",", "."))
    return nota if 0 <= nota <= 5 else None


def extrair_urls(texto: str | None) -> list[str]:
    return re.findall(r"https?://\S+", texto or "")
