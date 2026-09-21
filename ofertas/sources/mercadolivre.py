"""Mercado Livre: scraping da página de ofertas + link de afiliado via Linkbuilder.

O ML não tem API pública para afiliados, mas o Linkbuilder do painel usa uma API
interna simples (createLink), autenticada só pelos cookies da sessão. O bot chama
essa API de dentro de uma página logada (perfil persistente do Chrome em
data/ml_profile). Faça login uma única vez com:

    uv run python -m ofertas ml-login
"""
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

from ..config import DATA_DIR, config
from ..formatter import preco_br
from ..models import Oferta
from ..utils import USER_AGENT, parse_contagem, parse_nota, parse_preco_br, sessao

log = logging.getLogger("ofertas.ml")

URL_OFERTAS = "https://www.mercadolivre.com.br/ofertas"
URL_LINKBUILDER = "https://www.mercadolivre.com.br/afiliados/linkbuilder"
API_CREATELINK = "https://www.mercadolivre.com.br/affiliate-program/api/v2/affiliates/createLink"
PERFIL_DIR = DATA_DIR / "ml_profile"

_RE_ID = re.compile(r"(MLB-?\d{6,})")


def e_link(url: str) -> bool:
    return any(d in url for d in ("mercadolivre.com", "mercadolibre.com", "meli.la/"))


def tem_sessao() -> bool:
    return PERFIL_DIR.exists() and any(PERFIL_DIR.iterdir())


# ── Busca de ofertas (scraping, sem login) ───────────────────────────

def _categorias() -> dict[str, str]:
    """{id: nome} das categorias do config (aceita dict ou lista de ids); vazio = todas."""
    cats = config.fonte_ml.get("categorias") or {}
    return dict(cats) if isinstance(cats, dict) else {str(c): str(c) for c in cats}


def buscar_ofertas() -> list[Oferta]:
    """Página de ofertas do ML, filtrada pelas categorias do config (ofertas?category=MLB...)."""
    paginas = max(1, int(config.fonte_ml.get("paginas", 1)))
    categorias = _categorias() or {"": "todas"}
    s = sessao()
    ofertas: dict[str, Oferta] = {}
    for cat_id, nome in categorias.items():
        for pagina in range(1, paginas + 1):
            params = {}
            if cat_id:
                params["category"] = cat_id
            if pagina > 1:
                params["page"] = pagina
            r = s.get(URL_OFERTAS, params=params or None, timeout=30)
            r.raise_for_status()
            achadas = _parse_pagina(r.text)
            for o in achadas:
                ofertas.setdefault(o.id_produto, o)
            log.info("Mercado Livre %s: %d ofertas", nome, len(achadas))
            time.sleep(1)
    log.info("Mercado Livre: %d ofertas coletadas", len(ofertas))
    return list(ofertas.values())


def _preco_de(card, seletor_base: str) -> float | None:
    fracao = card.select_one(f"{seletor_base} .andes-money-amount__fraction")
    if not fracao:
        return None
    centavos = card.select_one(f"{seletor_base} .andes-money-amount__cents")
    texto = fracao.get_text(strip=True) + ("," + centavos.get_text(strip=True) if centavos else "")
    return parse_preco_br(texto)


def _parse_card(card) -> Oferta | None:
    a = card.select_one("a.poly-component__title")
    if not (a and a.get("href")):
        return None
    titulo = a.get_text(strip=True)
    url = a["href"].split("#")[0].split("?")[0]

    m = _RE_ID.search(a["href"])
    id_produto = m.group(1).replace("-", "") if m else url.rstrip("/").rsplit("/", 1)[-1][:40]

    preco = _preco_de(card, ".poly-price__current")
    preco_original = _preco_de(card, "s.andes-money-amount--previous")

    desconto = None
    selo = card.select_one(".poly-price__discount-polylabel, .andes-money-amount__discount")
    if selo:
        m = re.search(r"(\d+)\s*%", selo.get_text())
        desconto = int(m.group(1)) if m else None

    img = card.select_one("img.poly-component__picture")
    imagem = (img.get("data-src") or img.get("src")) if img else None
    if imagem and imagem.startswith("data:"):
        imagem = None  # placeholder de lazy-load

    nota, vendas = _parse_review(card.select_one(".poly-component__review-compacted"))
    el = card.select_one(".poly-component__seller")
    vendedor = el.get_text(" ", strip=True) if el else None

    partes = []
    if "Frete grátis" in card.get_text():
        partes.append("🚚 Frete grátis")
    pix = card.select_one(".poly-price__unit-description")
    if pix and "pix" in pix.get_text().lower():
        partes.append("💠 preço no Pix")

    return Oferta(
        plataforma="mercadolivre",
        id_produto=id_produto,
        titulo=titulo,
        url_afiliado="",  # preenchido depois pelo Linkbuilder
        url_produto=url,
        preco=preco,
        preco_original=preco_original,
        desconto_pct=desconto,
        imagem=imagem,
        extra=" · ".join(partes) or None,
        nota=nota,
        vendas=vendas,
        vendedor=vendedor,
    )


_RE_VENDIDOS = re.compile(r"([\d.,]+\s*(?:mil|M|mi)?)\s*vendidos", re.I)


def _parse_review(el) -> tuple[float | None, int | None]:
    """Bloco de avaliação do card ("4.9 | +10mil vendidos") -> (nota, vendas mínimas)."""
    if not el:
        return None, None
    texto = el.get_text(" ", strip=True)
    m = _RE_VENDIDOS.search(texto)
    return parse_nota(texto.split("|")[0]), (parse_contagem(m.group(1)) if m else None)


def _parse_pagina(html: str) -> list[Oferta]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div.poly-card")
    ofertas = [o for o in (_parse_card(c) for c in cards) if o]
    if cards and not ofertas:
        log.warning("Página de ofertas do ML mudou de layout? %d cards, 0 parseados", len(cards))
    return ofertas


# ── Link de afiliado via Linkbuilder (Playwright + sessão logada) ────

def _abrir_contexto(pw, headless: bool):
    """Google Chrome instalado (build de produção) + perfil persistente do projeto.

    O ML recusa login no "Chrome for Testing" do Playwright, então usamos o
    Chrome real via channel="chrome" e removemos as marcas de automação.
    """
    kwargs = dict(
        channel="chrome",
        headless=headless,
        locale="pt-BR",
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    if headless:
        kwargs["user_agent"] = USER_AGENT  # o headless real se anuncia como HeadlessChrome
    else:
        kwargs["no_viewport"] = True
    try:
        return pw.chromium.launch_persistent_context(str(PERFIL_DIR), **kwargs)
    except Exception as e:
        if "chrome" not in str(e).lower():
            raise
        log.warning("Google Chrome não encontrado (%s); usando Chromium do projeto — "
                    "o login no ML pode ser recusado", type(e).__name__)
        kwargs.pop("channel")
        return pw.chromium.launch_persistent_context(str(PERFIL_DIR), **kwargs)


def _achar_chrome() -> str:
    """Caminho do Google Chrome instalado (Windows, macOS ou Linux)."""
    import shutil
    candidatos: list[str] = []

    if sys.platform == "win32":
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    chave = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
                    with winreg.OpenKey(hive, chave) as k:
                        candidatos.append(winreg.QueryValueEx(k, None)[0])
                except OSError:
                    continue
        except ImportError:
            pass
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                candidatos.append(str(Path(base) / "Google/Chrome/Application/chrome.exe"))
    elif sys.platform == "darwin":
        candidatos += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:  # Linux e afins
        for nome in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                     "brave-browser", "microsoft-edge"):
            achado = shutil.which(nome)
            if achado:
                candidatos.append(achado)
        candidatos += ["/usr/bin/google-chrome", "/usr/bin/chromium",
                       "/snap/bin/chromium", "/usr/bin/chromium-browser"]

    for c in candidatos:
        if c and Path(c).exists():
            return c
    raise RuntimeError("Google Chrome não encontrado — instale o Google Chrome e tente de novo.")


def ml_login() -> None:
    """Abre um Chrome comum (SEM automação — indetectável porque não há o que
    detectar) no perfil do bot, para você logar no ML uma única vez."""
    chrome = _achar_chrome()
    PERFIL_DIR.mkdir(parents=True, exist_ok=True)
    print("\n➡️  Vai abrir um Chrome normal com o perfil do bot (separado do seu).")
    print("    1. Faça login no Mercado Livre (senha, 2FA etc.)")
    print("    2. Confira que o Linkbuilder carrega logado")
    print("    3. FECHE o navegador para terminar\n")
    proc = subprocess.Popen([chrome, f"--user-data-dir={PERFIL_DIR}", "--no-first-run",
                             "--no-default-browser-check", URL_LINKBUILDER])
    proc.wait()
    print(f"✅ Perfil salvo em {PERFIL_DIR} — o bot usa essa sessão sozinho daqui pra frente.")
    print('   Teste com: uv run python -m ofertas converter "<link de produto do ML>"')


def _criar_links_api(page, urls: list[str], etiqueta: str) -> list[str]:
    """Chama a API interna do Linkbuilder de dentro da página logada; retorna os short links.

    Payload/resposta observados em 2026-08-21:
    POST createLink {"urls": [...], "tag": "<etiqueta>"} ->
    {"status": 200, "urls": [{"id", "created", "short_url": "https://meli.la/...", ...}]}
    """
    r = page.evaluate(
        """async ({api, urls, tag}) => {
            const resp = await fetch(api, {
                method: 'POST',
                headers: {'content-type': 'application/json'},
                body: JSON.stringify({urls, tag}),
            });
            const corpo = await resp.text();
            try { return {http: resp.status, dados: JSON.parse(corpo)}; }
            catch (e) { return {http: resp.status, texto: corpo.slice(0, 300)}; }
        }""",
        {"api": API_CREATELINK, "urls": urls, "tag": etiqueta},
    )
    if r.get("http") != 200 or not r.get("dados"):
        raise RuntimeError(f"createLink respondeu HTTP {r.get('http')}: {r.get('texto', '')}")
    itens = (r["dados"].get("urls")) or []
    links = [i.get("short_url") or "" for i in itens]
    if len(links) != len(urls) or not all(links):
        raise RuntimeError(f"createLink devolveu {sum(1 for l in links if l)} links "
                           f"para {len(urls)} URLs: {r['dados']}")
    return links


def gerar_links_afiliado(ofertas: list[Oferta]) -> None:
    """Preenche oferta.url_afiliado via API do Linkbuilder (lotes de 10 URLs)."""
    from playwright.sync_api import sync_playwright

    if not tem_sessao():
        raise RuntimeError("Sessão do ML não encontrada — rode: uv run python -m ofertas ml-login")
    if not config.ml_etiqueta:
        raise RuntimeError("ML_ETIQUETA não configurada no .env "
                           "(é a 'Etiqueta em uso' do Linkbuilder no painel de afiliados)")
    pendentes = [o for o in ofertas if not o.url_afiliado and o.url_produto]
    if not pendentes:
        return

    with sync_playwright() as pw:
        ctx = _abrir_contexto(pw, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(URL_LINKBUILDER, wait_until="domcontentloaded")
            if "login" in page.url or "registration" in page.url:
                raise RuntimeError("Sessão do ML expirou — rode de novo: "
                                   "uv run python -m ofertas ml-login")
            page.wait_for_timeout(1500)  # deixa os scripts de sessão da página rodarem
            for i in range(0, len(pendentes), 10):
                lote = pendentes[i:i + 10]
                links = _criar_links_api(page, [o.url_produto for o in lote], config.ml_etiqueta)
                for o, link in zip(lote, links):
                    o.url_afiliado = link
            log.info("Mercado Livre: %d links de afiliado gerados", len(pendentes))
        finally:
            ctx.close()


# ── Busca por termos (grupo Apple) ───────────────────────────────────

def buscar_termos(termos: list[str]) -> list[Oferta]:
    """Ofertas das páginas de busca do ML para cada termo (ex.: "apple iphone"). A página de ofertas quase
    nunca traz produto Apple, então o grupo Apple precisa de busca própria. As listagens de busca barram
    requisições simples (pedem verificação de conta) mas abrem no navegador logado do bot, que é o que usamos."""
    from playwright.sync_api import sync_playwright

    if not tem_sessao():
        raise RuntimeError("Sessão do ML não encontrada — rode: uv run python -m ofertas ml-login")
    achadas: dict[str, Oferta] = {}
    with sync_playwright() as pw:
        ctx = _abrir_contexto(pw, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for termo in termos:
                url = "https://lista.mercadolivre.com.br/" + re.sub(r"\s+", "-", termo.strip().lower())
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2500)
                    if "login" in page.url or "account-verification" in page.url:
                        raise RuntimeError("Sessão do ML expirou — rode de novo: uv run python -m ofertas ml-login")
                    novas = _parse_pagina(page.content())
                except RuntimeError:
                    raise
                except Exception as e:
                    log.warning("Busca '%s' no ML falhou: %s", termo, type(e).__name__)
                    continue
                for o in novas:
                    achadas.setdefault(o.id_produto, o)
                log.info("Mercado Livre busca '%s': %d itens", termo, len(novas))
        finally:
            ctx.close()
    return list(achadas.values())


# ── Reputação do vendedor (página do produto, navegador logado) ──────

# Bloco de tracking da página do produto, específico do anúncio (verificado em 2026-09-19), ex.:
# "event_data":{"seller_id":510386964,"seller_name":"Casa Dalonso","reputation_level":"5_green",
#               "power_seller_status":"platinum","official_store_id":5361,...
_RE_SELLER = re.compile(r'"seller_id":(\d+),"seller_name":"([^"]*)"(.{0,400})', re.S)
_RE_NIVEL = re.compile(r'"reputation_level":"(\d)_')
_RE_STATUS = re.compile(r'"power_seller_status":"(\w+)"')
_RE_LOJA_OFICIAL = re.compile(r'"official_store_id":\d+')


def parse_vendedor(html: str) -> dict:
    """Dados do vendedor do anúncio a partir do HTML da página do produto. {} se não achar."""
    m = _RE_SELLER.search(html)
    if not m:
        return {}
    trecho = m.group(3)
    nivel, status = _RE_NIVEL.search(trecho), _RE_STATUS.search(trecho)
    soup = BeautifulSoup(html, "lxml")
    vendas = soup.select_one(".ui-pdp-seller__header__subtitle")
    rotulo = soup.select_one(".ui-pdp-seller__label-sold")
    return {
        "vendedor": m.group(2),
        "nivel": int(nivel.group(1)) if nivel else None,
        "status": status.group(1) if status else None,
        "loja_oficial": bool(_RE_LOJA_OFICIAL.search(trecho)) or (rotulo is not None and "oficial" in rotulo.get_text().lower()),
        "vendas": parse_contagem(vendas.get_text(" ", strip=True)) if vendas and "venda" in vendas.get_text().lower() else None,
    }


# Cupons na página do produto (verificado em 2026-09-19). Não há código: o comprador ativa no modal
# "Ver cupons disponíveis". O pill do card ("20% OFF com Cupom") não diz a condição; a página diz.
# Formatos vistos (o campo amount_type nem sempre existe):
#   {"label":"Compre R$ 79,99 e ganhe 20% OFF","status":"unredeemed","amount_type":"percentage",
#    "amount":20,"campaign_id":"13566431","type":"COUPON_NOT_MIN_PURCHASE_AMOUNT"}     <- exige compra mínima
#   {"label":"R$ 106,32 com Cupom","status":"unredeemed","amount":0,
#    "campaign_id":"14167118","type":"INACTIVE_COUPON_NOT_APPLIED"}                    <- preço final exato
# O `status` é da conta logada do bot ("redeemed" = ela já ativou), não da campanha: não é critério.
_RE_CUPOM = re.compile(r'\{"label":"([^"]+)","status":"(\w+)"(?:,"amount_type":"\w+")?,"amount":[\d.]+,'
                       r'"campaign_id":"(\d+)","type":"(\w+)"')
# Só formatos sem condição extra. Outros (ex.: "... por seguir a loja") não são anunciados.
_RE_CUPOM_REGRA = re.compile(r"^(?:Compre R\$ ([\d.,]+) e )?ganhe (?:(\d+)%|R\$ ([\d.,]+)) OFF$", re.I)
_RE_CUPOM_PRECO = re.compile(r"^R\$ ([\d.,]+) com Cupom$", re.I)


def parse_cupons(html: str) -> list[dict]:
    """Cupons listados na página do produto (sem repetidos): label, status, tipo e campanha."""
    vistos, cupons = set(), []
    for label, status, campanha, tipo in _RE_CUPOM.findall(html):
        if campanha not in vistos:
            vistos.add(campanha)
            cupons.append({"label": label, "status": status, "campanha": campanha, "tipo": tipo})
    return cupons


def texto_cupom(o: Oferta, cupons: list[dict]) -> str | None:
    """Texto do cupom para o post, ou None. Só anuncia cupom que vale para 1 unidade deste produto:
    formato sem condição extra, compra mínima (se houver) atingida pelo preço e tipo sem "mínimo não atingido"."""
    if not o.preco:
        return None
    melhor: tuple[float, str] | None = None     # (economia estimada em R$, texto)
    for c in cupons:
        if "NOT_MIN" in c["tipo"] or c["status"] not in ("unredeemed", "redeemed"):
            continue
        if m := _RE_CUPOM_PRECO.match(c["label"]):          # o ML informa o preço final com o cupom
            final = parse_preco_br(m.group(1)) or 0.0
            if not 0 < final < o.preco:
                continue
            economia, texto = o.preco - final, f"🎟 {preco_br(final)} com cupom (ative na página do produto)"
        elif m := _RE_CUPOM_REGRA.match(c["label"]):
            minimo = parse_preco_br(m.group(1)) or 0.0
            if o.preco < minimo:
                continue
            if m.group(3):      # valor fixo: o preço final é exato
                economia = parse_preco_br(m.group(3)) or 0.0
                texto = f"🎟 Cupom de {preco_br(economia)} OFF → {preco_br(max(o.preco - economia, 0))} (ative na página do produto)"
            else:               # percentual: o cupom pode ter teto de desconto, então não calculo o preço final
                economia = o.preco * int(m.group(2)) / 100
                texto = f"🎟 Cupom de {m.group(2)}% OFF"
                if minimo:
                    texto += f" em compras a partir de {preco_br(minimo)}"
                texto += " (ative na página do produto)"
        else:
            continue
        if melhor is None or economia > melhor[0]:
            melhor = (economia, texto)
    return melhor[1] if melhor else None


# Produto de importação (comércio internacional): a lista "tags" do anúncio traz "cbt_item". Verificado em 2026-09-20:
# presente nos 3 Ryzen importados testados (Saikang Store, Loja Alpha, CLUB ENVIOS Miami) e ausente em 13 páginas
# de anúncios nacionais. O texto "internacional" NÃO serve: aparece também nas páginas nacionais (menu do site).
_RE_CBT = re.compile(r'"tags":\[[^\]]*"cbt_item"')


def parse_internacional(html: str) -> bool:
    """True se a página do produto é de um anúncio de importação (comércio internacional)."""
    return bool(_RE_CBT.search(html))


def verificar_vendedores(ofertas: list[Oferta]) -> None:
    """Abre a página de cada produto (mesmo navegador logado do Linkbuilder) e preenche os campos
    de vendedor e o cupom. Falha em uma página não interrompe as outras (ela só fica sem dados)."""
    from playwright.sync_api import sync_playwright

    if not tem_sessao():
        raise RuntimeError("Sessão do ML não encontrada — rode: uv run python -m ofertas ml-login")
    pendentes = [o for o in ofertas if o.url_produto and not o.vendedor_checado]
    if not pendentes:
        return

    with sync_playwright() as pw:
        ctx = _abrir_contexto(pw, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for o in pendentes:
                dados, cupom = {}, None
                try:
                    page.goto(o.url_produto, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(1500)
                    if "login" in page.url or "account-verification" in page.url:
                        raise RuntimeError("Sessão do ML expirou — rode de novo: uv run python -m ofertas ml-login")
                    html = page.content()
                    dados = parse_vendedor(html)
                    cupom = texto_cupom(o, parse_cupons(html)) if config.buscar_cupons else None
                    o.internacional = parse_internacional(html)
                except RuntimeError:
                    raise
                except Exception as e:
                    log.warning("Não consegui ler o vendedor de '%s': %s", o.titulo[:40], type(e).__name__)
                o.vendedor_checado = True
                o.vendedor = dados.get("vendedor") or o.vendedor
                o.vendedor_nivel = dados.get("nivel")
                o.vendedor_status = dados.get("status")
                o.loja_oficial = bool(dados.get("loja_oficial"))
                o.vendas_vendedor = dados.get("vendas")
                o.cupom = cupom
            log.info("Mercado Livre: %d página(s) de produto conferida(s), %d com cupom válido",
                     len(pendentes), sum(1 for o in pendentes if o.cupom))
        finally:
            ctx.close()


def converter(url: str) -> Oferta:
    """Link de produto -> Oferta com dados da página + link de afiliado."""
    url = url.split("#")[0]
    if "meli.la/" in url:  # link de afiliado encurtado: expande até o produto
        try:
            url = sessao().get(url, allow_redirects=True, timeout=20).url.split("#")[0]
        except Exception as e:
            log.warning("Não consegui expandir o link meli.la: %s", e)
    titulo = preco = preco_original = imagem = None
    try:
        r = sessao().get(url, timeout=25)
        soup = BeautifulSoup(r.text, "lxml")
        el = soup.select_one("h1.ui-pdp-title")
        titulo = el.get_text(strip=True) if el else None
        el = soup.select_one('meta[property="og:image"]')
        imagem = el.get("content") if el else None
        el = soup.select_one('meta[itemprop="price"]')
        if el and el.get("content"):
            preco = float(el["content"])
        else:
            el = soup.select_one(".ui-pdp-price__second-line .andes-money-amount__fraction")
            preco = parse_preco_br(el.get_text()) if el else None
        el = soup.select_one("s.andes-money-amount--previous .andes-money-amount__fraction")
        preco_original = parse_preco_br(el.get_text()) if el else None
    except Exception as e:
        log.warning("Não consegui ler a página do produto: %s", e)

    m = _RE_ID.search(url)
    oferta = Oferta(
        plataforma="mercadolivre",
        id_produto=m.group(1).replace("-", "") if m else url.rstrip("/").rsplit("/", 1)[-1][:40],
        titulo=titulo or "Oferta Mercado Livre",
        url_afiliado="",
        url_produto=url.split("?")[0],
        preco=preco,
        preco_original=preco_original,
        imagem=imagem,
    )
    gerar_links_afiliado([oferta])
    if not oferta.url_afiliado:
        raise RuntimeError("Linkbuilder não devolveu o link de afiliado")
    return oferta
