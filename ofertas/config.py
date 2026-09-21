import os
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Navegador do Playwright fica dentro do projeto: instalações no AppData podem
# não ser visíveis entre sessões diferentes (sandbox). Instale com:
# uv run python -m ofertas instalar-navegador
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(DATA_DIR / "pw-browsers"))

load_dotenv(BASE_DIR / ".env")


def _ler_yaml() -> dict:
    caminho = BASE_DIR / "config.yaml"
    if not caminho.exists():
        return {}
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Config:
    def __init__(self):
        y = _ler_yaml()
        geral = y.get("geral") or {}
        filtros = y.get("filtros") or {}

        # .env (segredos)
        self.bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.chat_id_apple: str = os.getenv("TELEGRAM_CHAT_ID_APPLE", "").strip()   # grupo só de produtos Apple
        self.owner_id: int = int(os.getenv("TELEGRAM_OWNER_ID", "0").strip() or 0)
        self.amazon_tag: str = os.getenv("AMAZON_TAG", "").strip()
        self.amazon_credential_id: str = os.getenv("AMAZON_CREDENTIAL_ID", "").strip()
        self.amazon_credential_secret: str = os.getenv("AMAZON_CREDENTIAL_SECRET", "").strip()
        self.ml_etiqueta: str = os.getenv("ML_ETIQUETA", "").strip()
        self.shopee_app_id: str = os.getenv("SHOPEE_APP_ID", "").strip()
        self.shopee_app_secret: str = os.getenv("SHOPEE_APP_SECRET", "").strip()

        # config.yaml
        self.intervalo_minutos: int = int(geral.get("intervalo_minutos", 30))
        self.max_posts_por_ciclo: int = int(geral.get("max_posts_por_ciclo", 5))
        self.espacamento_segundos: int = int(geral.get("espacamento_segundos", 120))
        self.nao_repetir_dias: int = int(geral.get("nao_repetir_dias", 7))
        self.horario_ativo: str = str(geral.get("horario_ativo") or "").strip()  # "08:00-23:00"; vazio = 24h

        self.desconto_minimo: int = int(filtros.get("desconto_minimo", 0))
        self.preco_minimo: float = float(filtros.get("preco_minimo", 0))
        self.preco_maximo: float = float(filtros.get("preco_maximo", 0))
        self.palavras_bloqueadas: list[str] = [
            str(p).lower() for p in (filtros.get("palavras_bloqueadas") or [])
        ]

        # Critérios de qualidade e ranking (ver selecao.py). Chaves ausentes usam estes padrões.
        sel = y.get("selecao") or {}
        camp = sel.get("campeoes") or {}
        self.exigir_avaliacao: bool = bool(sel.get("exigir_avaliacao", True))
        self.nota_minima: float = float(sel.get("nota_minima", 4.3))
        self.prova_social_minima: int = int(sel.get("prova_social_minima", 20))
        self.historico_dias: int = int(sel.get("historico_dias", 30))
        self.repostar_queda_pct: int = int(sel.get("repostar_se_queda_pct", 10))
        self.vendas_mensal_para_total: float = float(sel.get("vendas_mensal_para_total", 6))
        self.desconto_suspeito: int = int(sel.get("desconto_suspeito", 60))
        self.suspeita_vendas_minimas: int = int(sel.get("suspeita_vendas_minimas", 5000))
        self.preco_minimo_vs_mercado_pct: int = int(sel.get("preco_minimo_vs_mercado_pct", 50))
        self.verificar_vendedor: bool = bool(sel.get("verificar_vendedor", True))
        self.buscar_cupons: bool = bool(sel.get("buscar_cupons", True))
        self.vendedor_nivel_minimo: int = int(sel.get("vendedor_nivel_minimo", 4))
        self.campeoes_vendas_minimas: int = int(camp.get("vendas_minimas", 1000))
        self.campeoes_nota_minima: float = float(camp.get("nota_minima", 4.5))
        self.campeoes_pct_posts: int = max(0, min(100, int(camp.get("pct_dos_posts", 50))))

        # Variedade: não repete o mesmo TIPO de produto (ofertas/tipos.py) numa janela de horas
        var = y.get("variedade") or {}
        self.variedade_janela_horas: float = float(var.get("janela_horas", 4))
        try:
            from .tipos import adicionar_extras
            adicionar_extras(var.get("tipos_extras") or {})
        except Exception:
            pass

        # Grupo Apple (ofertas/destinos.py): produtos Apple vão para o grupo próprio, o resto para o canal geral.
        # Só liga com TELEGRAM_CHAT_ID_APPLE no .env; sem ele, tudo continua indo para o canal geral.
        apple = y.get("apple") or {}
        self.apple_ativo: bool = bool(apple.get("ativo", True))
        self.apple_max_posts: int = int(apple.get("max_posts_por_ciclo", 3))
        self.apple_queda_minima: int = int(apple.get("queda_minima", 5))
        self.apple_so_queda: bool = bool(apple.get("so_queda_de_preco", True))
        self.apple_buscas: list[str] = [str(t) for t in (apple.get("buscas") if apple.get("buscas") is not None else [
            "apple iphone", "apple ipad", "apple macbook", "airpods apple", "apple watch"])]
        self.apple_amazon_termos_por_ciclo: int = int(apple.get("amazon_termos_por_ciclo", 2))
        bloqueadas = apple.get("palavras_bloqueadas")
        self.apple_palavras_bloqueadas: list[str] = [str(p).lower() for p in (
            ["recondicionado", "seminovo", "usado", "vitrine", "open box", "caixa aberta", "swap"] if bloqueadas is None else bloqueadas)]

        # Mix de categorias (ofertas/mix.py): fatia-alvo dos posts por categoria
        mix = y.get("mix") or {}
        self.mix_ativo: bool = bool(mix.get("ativo", True))
        self.mix_janela_posts: int = int(mix.get("janela_posts", 40))
        self.mix_forca: float = float(mix.get("forca", 1.5))
        self.mix_tecnologia_preco_max: float = float(mix.get("tecnologia_preco_maximo", 300))
        self.mix_eletro_exige_queda: bool = bool(mix.get("eletrodomesticos_exige_queda_real", True))
        self.mix_sem_categoria: float = float(mix.get("sem_categoria", 0))
        self.mix_metas: dict[str, float] = {
            str(k): float(v) for k, v in (mix.get("metas") or {
                "casa_e_cozinha": 22, "moda": 20, "beleza": 15, "limpeza_e_higiene": 10, "tecnologia": 10,
                "esporte": 6, "saude": 5, "brinquedos_e_bebes": 5, "eletrodomesticos": 5, "outros": 2,
            }).items()
        }

        # Post de divulgação do canal (link/hashtag), publicado de tempos em tempos em vez de em todo post
        div = y.get("divulgacao") or {}
        self.divulgacao_ativa: bool = bool(div.get("ativa", True))
        self.divulgacao_a_cada_horas: float = float(div.get("a_cada_horas", 24))
        self.divulgacao_fixar: bool = bool(div.get("fixar", False))
        self.divulgacao_texto: str = str(div.get("texto") or "").strip()

        fontes = y.get("fontes") or {}
        self.fonte_ml: dict = fontes.get("mercadolivre") or {"ativa": False}
        self.fonte_shopee: dict = fontes.get("shopee") or {"ativa": False}
        self.fonte_amazon: dict = fontes.get("amazon") or {"ativa": False}

        # Seleção de nichos feita no painel (data/nichos.json). Se houver, ela
        # SUBSTITUI as categorias/departamentos/buscas do config.yaml.
        # Nenhum nicho selecionado = mantém o config.yaml (padrão: todas as categorias).
        self.nichos: list[str] = []
        try:
            from .nichos import expandir, ler_selecao
            self.nichos = ler_selecao()
            if self.nichos:
                exp = expandir(self.nichos)
                self.fonte_ml = {**self.fonte_ml, "categorias": exp["ml"]}
                self.fonte_amazon = {**self.fonte_amazon,
                                     "departamentos": exp["amazon_dep"],
                                     "buscas": exp["amazon_buscas"]}
                self.fonte_shopee = {**self.fonte_shopee, "buscas": exp["shopee"]}
        except Exception:
            pass


config = Config()


def dentro_do_horario(agora: datetime | None = None) -> bool:
    """True se agora está dentro de geral.horario_ativo (aceita janela virando a noite)."""
    if not config.horario_ativo:
        return True
    try:
        inicio, fim = config.horario_ativo.split("-")
        h1, m1 = (int(x) for x in inicio.strip().split(":"))
        h2, m2 = (int(x) for x in fim.strip().split(":"))
    except ValueError:
        return True  # formato inválido: não bloqueia
    agora = agora or datetime.now()
    t, a, b = agora.hour * 60 + agora.minute, h1 * 60 + m1, h2 * 60 + m2
    return a <= t < b if a <= b else (t >= a or t < b)


def verificar() -> list[str]:
    """Retorna a lista do que ainda falta configurar."""
    pendencias = []
    if not config.bot_token:
        pendencias.append("TELEGRAM_BOT_TOKEN (crie o bot no @BotFather)")
    if not config.chat_id:
        pendencias.append("TELEGRAM_CHAT_ID (canal/grupo onde o bot vai postar)")
    if not config.owner_id:
        pendencias.append("TELEGRAM_OWNER_ID (seu user id — mande /id para o bot)")
    if config.chat_id_apple and config.chat_id_apple == config.chat_id:
        pendencias.append("TELEGRAM_CHAT_ID_APPLE é IGUAL a TELEGRAM_CHAT_ID: o grupo Apple e o canal geral "
                          "precisam de IDs diferentes (use 'Detectar IDs' no painel e escolha o destino de cada chat)")
    if not config.amazon_tag:
        pendencias.append("AMAZON_TAG (tag/Store ID do Amazon Associates)")
    if config.fonte_amazon.get("ativa") and not (config.amazon_credential_id and config.amazon_credential_secret):
        pendencias.append("AMAZON_CREDENTIAL_ID / AMAZON_CREDENTIAL_SECRET (Creators API — busca automática)")
    if not (config.shopee_app_id and config.shopee_app_secret):
        pendencias.append("SHOPEE_APP_ID / SHOPEE_APP_SECRET (painel de afiliados > Open API)")
    if not config.ml_etiqueta:
        pendencias.append("ML_ETIQUETA (a 'Etiqueta em uso' do Linkbuilder do ML)")
    if not list((DATA_DIR / "pw-browsers").glob("chromium-*")):
        pendencias.append("Chromium do Playwright (rode: uv run python -m ofertas instalar-navegador)")
    perfil_ml = DATA_DIR / "ml_profile"
    if not (perfil_ml.exists() and any(perfil_ml.iterdir())):
        pendencias.append("Sessão do Mercado Livre (rode: uv run python -m ofertas ml-login)")
    return pendencias
