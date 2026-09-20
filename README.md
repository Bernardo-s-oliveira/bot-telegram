# Bot de Ofertas para Telegram 🔥

Bot que roda **no seu PC** (Windows), garimpa promoções em **Mercado Livre, Shopee e Amazon** e posta no seu canal do Telegram com **os seus links de afiliado** — foto, preço "de/por", desconto e botão de compra. Tudo local, sem servidor nem mensalidade.

> ⚠️ Você precisa das **suas próprias** contas de afiliado (Mercado Livre, Amazon Associados, Shopee Afiliados). As comissões vão para quem configurar — cada pessoa usa as suas.

## Como funciona

Dois modos, no mesmo programa:

1. **Automático** — de tempos em tempos busca ofertas, filtra por desconto mínimo, evita repetir produto e posta as melhores no canal, já com o seu link de afiliado.
2. **Conversor** — você cola qualquer link de produto no privado do bot; ele monta a prévia do post com o seu link e você decide **✅ Postar** ou **🗑 Descartar**.

| Plataforma | De onde vêm as ofertas | Link de afiliado |
|---|---|---|
| Mercado Livre | Página de ofertas, filtrada por categoria | API do Linkbuilder do painel (login 1x); gera `meli.la/...` |
| Shopee | Open API oficial de afiliados (busca por palavra-chave) | A API já devolve o link com sua comissão |
| Amazon | Creators API; ou, enquanto sua conta não é elegível, ofertas por departamento | Link com a sua tag |

---

## 🖥️ Jeito fácil: o painel gráfico (recomendado)

**Dê dois cliques em `PAINEL.bat`.** Ele prepara o ambiente sozinho e abre um painel no seu navegador, onde você faz tudo com formulário e botões — sem terminal:

- preenche a configuração (token, IDs, tags) num formulário;
- descobre os IDs do Telegram com o botão **🔎 Detectar IDs**;
- instala o navegador e faz o **login do Mercado Livre** por botões;
- testa as fontes (ML/Shopee/Amazon) e vê as ofertas na hora;
- **liga/desliga o bot** e acompanha o log ao vivo.

Na primeira vez, se o `uv` não estiver instalado, o `PAINEL.bat` instala e pede para você reabrir — só seguir a tela. Antes, tenha em mãos: o **token** do bot (@BotFather), um **canal** com o bot como **administrador**, e suas **contas de afiliado**.

> Prefere o terminal? O passo a passo manual está logo abaixo (dá no mesmo).

---

## Passo a passo (pelo terminal)

### 0. Instalar o uv (uma vez)
O projeto usa o [uv](https://docs.astral.sh/uv/) (gerenciador de Python — ele baixa o Python sozinho, você não precisa instalar Python separado). Abra o **PowerShell** e cole:
```powershell
winget install astral-sh.uv
```
Feche e reabra o PowerShell depois de instalar.

### 1. Baixar e abrir a pasta
Baixe o .zip, extraia para uma pasta (ex: `C:\ofertas-bot`) e entre nela no PowerShell:
```powershell
cd C:\ofertas-bot
```

### 2. Instalar as dependências
```powershell
uv sync
uv run python -m ofertas instalar-navegador
```
(O segundo comando baixa o navegador que gera os links do Mercado Livre, ~120 MB.)

### 3. Criar o bot no Telegram
Fale com o [@BotFather](https://t.me/BotFather) → `/newbot` → escolha um nome e um @username → **copie o token** que ele te dá.

### 4. Criar o canal e colocar o bot como admin
Crie um canal no Telegram. Abra os detalhes do canal → **Administradores** → adicione o seu bot com permissão de **Publicar mensagens**.

### 5. Preencher o `.env`
```powershell
Copy-Item .env.example .env
notepad .env
```
Preencha (o que você não tiver ainda, deixe em branco e preencha depois):
- `TELEGRAM_BOT_TOKEN` — o token do passo 3.
- `TELEGRAM_OWNER_ID` e `TELEGRAM_CHAT_ID` — o jeito fácil: salve o token, rode o bot (passo 7), mande `/id` para ele no privado (dá o seu **owner id**) e encaminhe um post do canal para ele (dá o **chat id**). Depois cole os dois no `.env`.
- `ML_ETIQUETA` — a "Etiqueta em uso" que aparece no [Linkbuilder](https://www.mercadolivre.com.br/afiliados/linkbuilder) do painel de afiliados do Mercado Livre.
- `AMAZON_TAG` — sua tag do [Amazon Associados](https://associados.amazon.com.br) (algo como `seunome-20`).
- `AMAZON_CREDENTIAL_ID` / `SECRET` — opcional (Creators API). Sem isso o bot funciona mesmo assim.
- `SHOPEE_APP_ID` / `SHOPEE_APP_SECRET` — no [painel de afiliados Shopee](https://affiliate.shopee.com.br), menu **Abrir API** (a aprovação pode demorar alguns dias).

### 6. Login no Mercado Livre (uma vez só)
```powershell
uv run python -m ofertas ml-login
```
Abre um Chrome normal. Faça login na sua conta de afiliado, confira que o Linkbuilder aparece logado e **feche o navegador**. A sessão fica salva e o bot passa a gerar os links sozinho.

### 7. Conferir e rodar
```powershell
uv run python -m ofertas check     # mostra o que ainda falta configurar
uv run python -m ofertas run       # liga o bot (ou dê 2 cliques no run.bat)
```
Cole um link de produto no privado do bot para testar, ou espere o primeiro ciclo automático.

---

## Como o bot escolhe as ofertas

O objetivo não é postar o maior desconto, e sim a **melhor compra**. O desconto que a **loja** anuncia não qualifica, não pontua e **não aparece no post**: na página de ofertas do ML a mediana anunciada é ~42%, com muito preço "De" inflado ou permanente. Só vale o que o histórico de preços do bot comprova. Cada oferta coletada passa por:

1. **Portões de qualidade** — descarta produto sem avaliação, com nota baixa (< 4,3), sem preço, com preço **acima do normal** (mais de 5% acima da mediana do histórico) ou **mais caro que anúncios iguais** coletados no mesmo ciclo (o mais em conta entre os iguais é o que passa).
2. **Duas faixas**, que se alternam nos posts de cada ciclo (faixa vazia cede a vaga à outra):
   - **Campeões** — muitas vendas (≥ 1.000) + nota ≥ 4,5 + preço em conta. **Não exige desconto**: funciona desde o primeiro dia e com preços parados;
   - **Queda de preço** — queda **comprovada** no histórico (≥ 25%) + prova social (vendas/avaliações). Só existe depois de ~1 dia de coleta.
3. **Score** — dentro de cada faixa, ordena por vendas, nota (com peso menor se há poucas avaliações), queda comprovada, preço vs. histórico e preço absoluto. Sem repetir o mesmo produto (nem em outra cor/loja) e sem uma plataforma dominar o ciclo.

**O que o post mostra sobre preço:** só `💰 R$ X`, mais nota e vendas. Quando o histórico comprova uma queda de 10% ou mais, aparece também `❌ De: R$ Y` (o **preço médio recente**, não o "De" da loja) e um selo `🔻 Caiu N% em relação ao preço médio dos últimos D dias` — o percentual aparece uma vez só. `📉 Menor preço dos últimos D dias` só sai se o preço já esteve mais alto.

**Descontos que parecem bons demais** (preço errado, vendedor duvidoso):
- preço abaixo de 50% dos anúncios iguais coletados no ciclo é descartado como suspeito;
- queda de **60% ou mais** (ou, sem histórico ainda, desconto anunciado de 60% ou mais, usado só como alerta) só passa com vendedor confiável. No Mercado Livre o bot abre a página do produto (com o mesmo navegador logado do Linkbuilder) e confere a reputação: exige nível ≥ 4 e, nesses casos, MercadoLíder Gold/Platinum ou loja oficial — se reprovar, entra o próximo melhor. Na Amazon e na Shopee, onde não dá para ler o vendedor, exige prova forte (≥ 5.000 vendas e nota ≥ 4,6);
- vendedor confiável ganha selo no post ("🛡️ Loja oficial", "🏅 Vendedor MercadoLíder Platinum").

**Cupons (Mercado Livre):** na mesma visita à página do produto, o bot lê os cupons e adiciona ao post uma linha como *"🎟 R$ 106,32 com cupom (ative na página do produto)"*. Só anuncia cupom que vale para **1 unidade**: o "20% OFF com Cupom" que aparece na listagem muitas vezes exige compra mínima acima do preço do item, ou só vale "por seguir a loja" — esses são ignorados. Os cupons que a página lista se ativam com um clique, sem código. Desligue com `buscar_cupons: false`. Amazon e Shopee ficam sem cupom (não há dado confiável nas páginas que o bot lê).

**Variedade e apresentação:**
- O bot não repete o mesmo **tipo de produto** (creatina, chuveiro, balança, fone…) numa janela de horas (`variedade.janela_horas`, padrão 4) nem dentro do mesmo ciclo. O tipo vem de um dicionário de palavras em `ofertas/tipos.py`; produto que não casa com nada não sofre a regra (ex.: livros). Para ensinar tipos novos: `variedade.tipos_extras` no `config.yaml`.
- Os títulos das lojas (muitas vezes com 150+ caracteres) saem curtos: cortados num separador natural ou em fim de palavra (~75 caracteres), sem conectivo pendurado e sem código de modelo no início ("Eps-6905 Balança…" vira "Balança…").
- Link e hashtag do canal **não** vão em cada post: uma mensagem de divulgação (`divulgacao.texto`) é publicada uma vez a cada 24 h, depois das ofertas de um ciclo. Dá para desligar (`ativa: false`), mudar o intervalo ou fixá-la no canal (`fixar: true`, exige permissão de fixar mensagens).

Se não houver oferta boa o bastante, o bot **posta menos** em vez de completar a cota. Um produto já postado só volta antes de 7 dias se o preço caiu 10% ou mais desde o último post (o post diz "🔁 De volta e mais barato").

Para ver o que ele postaria agora, sem postar nada (e alimentar o histórico de preços):
```powershell
uv run python -m ofertas simular
```
Todos os critérios estão na seção `selecao:` do `config.yaml`. Os testes: `uv run --with pytest python -m pytest tests`.

## Ajustes — `config.yaml`
Intervalo entre ciclos, quantos posts por vez, desconto mínimo, horário ativo e o **escopo do canal**. Por padrão o bot pega **ofertas de todas as categorias**. Para focar num nicho (tecnologia, moda, casa, pet…), preencha as listas de `categorias`/`departamentos`/`buscas` no `config.yaml` — há exemplos comentados dentro do arquivo. Edite e **reinicie o bot** (ele só lê a configuração ao iniciar).

## Deixar rodando sozinho
- O bot posta enquanto a janela estiver aberta e o PC ligado. O `run.bat` reinicia sozinho se cair.
- `horario_ativo` no `config.yaml` evita posts de madrugada (padrão 08:00–23:00).
- Para o PC não dormir: Configurações → Sistema → Energia → *Suspender: nunca*.
- Iniciar junto com o Windows: `Win+R` → `shell:startup` → atalho para o `run.bat`.

---

## Problemas comuns

**`Failed to spawn: python` / "Uma política de Controle de Aplicativo bloqueou este arquivo"**
O **Smart App Control** do Windows 11 bloqueia programas sem assinatura (inclusive o Python que o uv baixa). Para desativar: aperte **Windows**, digite `Controle inteligente de aplicativos`, abra, marque **Desativado**, confirme e **reinicie o PC**. Isso não desliga o antivírus (o Windows Defender continua ativo). Obs.: uma vez desativado, o Smart App Control só volta a ligar reinstalando o Windows.

**`uv` não é reconhecido**
Feche e reabra o PowerShell depois de instalar o uv (passo 0).

**`testar ml` não acha ofertas / links param de sair**
Os sites mudam de layout de vez em quando. Refaça o `ml-login` (a sessão pode ter expirado) e, se persistir, os seletores ficam em `ofertas/sources/`.

---

## ⚖️ Uso responsável
- **Divulgação obrigatória**: os programas de afiliado (Amazon principalmente) exigem avisar que os links geram comissão. Coloque na descrição do canal algo como *"Contém links de afiliado; podemos receber comissão pelas compras, sem custo extra para você."*
- Respeite os termos de cada plataforma e não abuse da frequência de busca (os padrões do `config.yaml` já são comedidos).
- Os preços mudam a qualquer momento; o post reflete o preço no instante da coleta.

## Estrutura
```
ofertas/
├── main.py            # comandos (check, run, converter, postar, ml-login, testar)
├── bot_interativo.py  # bot do Telegram (conversor + agendador)
├── pipeline.py        # coleta → filtros → escolhe → gera links → posta
├── selecao.py         # qualidade, validação do desconto, faixas e score
├── formatter.py       # visual do post
├── db.py              # banco: anti-repetição + histórico de preços
├── config.py          # lê .env + config.yaml
└── sources/           # mercadolivre.py, shopee.py, amazon.py
```
