from dataclasses import dataclass, field


@dataclass
class Oferta:
    plataforma: str            # "mercadolivre" | "shopee" | "amazon"
    id_produto: str
    titulo: str
    url_afiliado: str          # vazio até o link de afiliado ser gerado
    url_produto: str = ""
    preco: float | None = None
    preco_original: float | None = None
    desconto_pct: int | None = None
    imagem: str | None = None
    extra: str | None = None   # frete grátis, "no Pix", cupom etc. (não entra na seleção)

    # Prova social, lida das fontes. None = a fonte não informou.
    nota: float | None = None          # 0–5
    avaliacoes: int | None = None      # nº de avaliações
    vendas: int | None = None          # vendas informadas (ML/Shopee: total; Amazon: no último mês)
    vendas_mensal: bool = False        # True quando `vendas` se refere só ao último mês

    # Vendedor (Mercado Livre: lido na página do produto por mercadolivre.verificar_vendedores)
    vendedor: str | None = None
    vendedor_nivel: int | None = None      # reputação 1 (vermelho) a 5 (verde)
    vendedor_status: str | None = None     # MercadoLíder: "silver" | "gold" | "platinum"
    vendas_vendedor: int | None = None
    loja_oficial: bool = False
    vendedor_checado: bool = False         # já tentamos ler o vendedor (mesmo que sem sucesso)

    # Referência de preço de anúncios parecidos coletados no mesmo ciclo (pipeline.anotar_preco_mercado)
    preco_mercado: float | None = None

    # Preenchidos por selecao.avaliar()
    desconto_verificado: bool = False  # o desconto exibido é comprovado pelo histórico de preços
    suspeita: bool = False             # desconto tão alto que exige checar o vendedor
    score: float = 0.0
    faixa: str = ""                    # "campeao" | "desconto"
    selos: list[str] = field(default_factory=list)   # motivos exibidos no post

    @property
    def uid(self) -> str:
        return f"{self.plataforma}:{self.id_produto}"

    @property
    def desconto(self) -> int | None:
        if self.desconto_pct:
            return self.desconto_pct
        if self.preco and self.preco_original and self.preco_original > self.preco:
            return round(100 * (1 - self.preco / self.preco_original))
        return None
