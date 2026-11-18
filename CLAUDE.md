# SmartBuy Radar v2.0 — Contexto Completo do Projeto (Handoff)

## 1. Visão Geral do Produto
**SmartBuy Radar: Otimizador de Compra Perfeita** é uma plataforma web full-stack multimodal para inteligência de compras no e-commerce brasileiro.
A partir de um **link de produto**, **termo de busca** ou **imagem/print (`Ctrl+V` / upload)**, o sistema identifica o modelo exato e executa 5 pipelines assíncronos transmitidos ao vivo via **Server-Sent Events (SSE)** para entregar:
1. **Campeão À Vista (Pix / 1x)**: menor desembolso imediato em lojas confiáveis.
2. **Campeão Parcelado (Sem Juros)**: melhor parcelamento (priorizando 10x/12x sem juros quando valores totais empatam).
3. **Campeão Estratégico (Smart Hack)**: menor **Custo Efetivo Real** combinando Loja Confiável + Cupom Ativo + Pontos (Livelo/Esfera) ou Cashback (Méliuz, Inter Shop, Cuponomia, Zoom/Buscapé), com checklist passo a passo de execução.
4. **Termômetro de Timing & Recomendações (v2.0)**: classifica se é hora de comprar (`🟢 MOMENTO IDEAL`, `🟡 PREÇO NORMAL`, `🔴 PREÇO INFLADO`) com base no histórico SQLite e sugere 1 alternativa direta + 2 complementos de ecossistema com preços reais ao vivo.

---

## 2. Repositório, Produção (Railway) e Credenciais de Acesso

- **Diretório Local:** `c:\Users\kaua.ribeiro\OneDrive - Clinica Derma Skin\Área de Trabalho\Price`
- **Repositório GitHub:** `https://github.com/kauaribeiro-silva/smartbuy-radar.git` (branch `main`)
- **Projeto no Railway:** `smartbuy-radar`
  - `projectId`: `1a6f7fbc-c502-49ec-9e9c-32604e5a09b2`
  - `environmentId` (`production`): `fcb9bc96-74e7-4285-8603-0f174514f770`
- **Serviços no Railway:**
  1. **Backend (`smartbuy-backend`)**:
     - `serviceId`: `6ff3fdf1-98a8-42c6-b840-d0bf2e6ec262`
     - `rootDirectory`: `/backend` | `dockerfilePath`: `Dockerfile` | `healthcheckPath`: `/health`
     - **URL Pública:** `https://smartbuy-backend-production-19bcf6b9.up.railway.app`
     - Variáveis configuradas no Railway: `GEMINI_API_KEY`, `APP_ACCESS_PASSWORD=242501905`, `HEADLESS_BROWSER=true`, `PORT=8000`
  2. **Frontend (`smartbuy-frontend`)**:
     - `serviceId`: `bd45d9a7-488e-4a02-9427-8b7fc663b57d`
     - `rootDirectory`: `/frontend` (build via Railpack / Next.js 15)
     - **URL Pública:** `https://smartbuy-frontend-production.up.railway.app`
     - **Senha de Acesso Privado (`/login`):** `242501905`
     - Variáveis configuradas no Railway: `NEXT_PUBLIC_API_URL=https://smartbuy-backend-production-19bcf6b9.up.railway.app`, `APP_ACCESS_PASSWORD=242501905`, `PORT=3000`

> **Nota sobre o ambiente local Windows:** Este computador de trabalho não possui `node`/`npm` instalado e bloqueia instaladores globais (`Access is denied`). O Python `3.14` com `uv` está instalado e o ambiente virtual fica em `backend/.venv`. O build do Next.js roda diretamente no Railway ao fazer `git push origin main`.

---

## 3. Arquitetura e Árvore de Arquivos

```text
Price/
├── .env                        # Variáveis locais (ignorado pelo git)
├── .env.example
├── .gitignore
├── CLAUDE.md                   # Este arquivo de contexto
├── README.md
├── backend/
│   ├── Dockerfile              # mcr.microsoft.com/playwright/python:v1.50.0-noble + Chromium
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── app/
│   │   ├── config.py           # Settings (gemini_model="gemini-3.5-flash-lite", app_access_password, etc.)
│   │   ├── db.py               # Persistência SQLite (tracked_products + price_history)
│   │   ├── main.py             # FastAPI + rotas SSE (/api/radar/stream, /api/search) e Memória (/api/memory/*)
│   │   ├── models.py           # Schemas Pydantic v1 + v2 (SpecContract, BlockedGroup, TimingVerdict, etc.)
│   │   ├── pipelines/
│   │   │   ├── orchestrator.py       # Orquestra os 5 pipelines e emite eventos SSE + salva no SQLite
│   │   │   ├── identification.py     # Pipeline 1: Gemini Vision (imagem), JSON-LD/slug (URL) e texto
│   │   │   ├── spec_validator.py     # StrictSpecValidator (Trava A: Spec Lock, Trava B: Piso 55%, Trava C: IA)
│   │   │   ├── marketplace_hunter.py # MarketplaceDeepHunter (Pelando 72h + decode JWT, Promobit API, ML)
│   │   │   ├── search_trust.py       # Pipeline 2: KaBuM! (__NEXT_DATA__), Zoom/Buscapé (Redux), Bing Shopping BR
│   │   │   ├── coupon_hunter.py      # Pipeline 3: Cuponomia ao vivo + Pelando/Promobit com filtro de nicho/marca
│   │   │   ├── benefits_intel.py     # Pipeline 4: Livelo, Esfera, Méliuz, Inter Shop, Cuponomia + tabela base
│   │   │   ├── financial_engine.py   # Pipeline 5: Cálculo de Custo Efetivo Real e 3 Cards de Veredito
│   │   │   └── recommendations.py    # Termômetro de Timing (Verde/Amarelo/Vermelho) + Cross-Sell ao vivo
│   │   └── utils/
│   │       ├── browser.py      # HTTPX + Playwright Stealth (Stealth().apply_stealth_async)
│   │       └── parsers.py      # Parser BRL (suporta "R$ 1.655" e "R$ 4.299,90"), JSON-LD e Trust Score
│   └── tests/
│       └── test_pipelines.py   # 8 testes unitários cobrindo parsers, trust filter, cupons, vereditos e travas v2
└── frontend/
    ├── package.json
    ├── tsconfig.json
    ├── next.config.ts
    ├── tailwind.config.ts
    └── src/
        ├── middleware.ts                         # Protege todas as rotas exigindo cookie HMAC smartbuy_session
        ├── lib/auth.ts                           # Assinatura/verificação HMAC-SHA256 (Web Crypto API)
        ├── types/radar.ts                        # Tipagens TypeScript completas (v1 + v2)
        ├── app/
        │   ├── layout.tsx
        │   ├── globals.css
        │   ├── page.tsx                          # Dashboard principal integrado ao SSE e Memória
        │   ├── login/page.tsx                    # Tela de login privado (senha: 242501905)
        │   └── api/
        │       ├── auth/route.ts                 # POST login / DELETE logout
        │       ├── radar/route.ts                # Proxy SSE autenticado (injeta header X-Radar-Key)
        │       └── memory/
        │           ├── radar/route.ts            # Proxy GET /api/memory/radar
        │           ├── refresh/[id]/route.ts     # Proxy POST /api/memory/refresh/{id}
        │           └── products/[id]/route.ts    # Proxy PATCH / DELETE /api/memory/products/{id}
        └── components/
            ├── SearchHero.tsx                    # Busca unificada (URL/texto/Ctrl+V imagem) + filtro À Vista/Parcelado
            ├── StartupPromoRadar.tsx             # Barra discreta de buscas recentes, alertas de queda e Preço Alvo
            ├── LiveAgentLogs.tsx                 # Terminal SSE animado (Framer Motion)
            ├── SpecGuardBanner.tsx               # Banner mostrando tokens obrigatórios, piso 55% e itens bloqueados
            ├── VerdictCards.tsx                  # 3 Cards Campeões (Pix, Parcelado, Smart Hack)
            ├── HowToBuyGuide.tsx                 # Checklist interativo passo a passo
            ├── BuyingIntelligence.tsx            # Termômetro de Timing + Recomendações de Ecossistema
            └── ComparisonTable.tsx               # Tabela comparativa com filtro Pix vs. Parcelado (sem juros)
```

---

## 4. Detalhes Técnicos Importantes Descobertos

1. **Modelo Gemini e Cota (`google-genai`)**:
   - `gemini-2.5-flash` retorna `404 NOT_FOUND` para chaves novas.
   - `gemini-3.8-flash` funciona, mas chamadas com a ferramenta `GoogleSearch` grounding na chave gratuita atual retornam `429 RESOURCE_EXHAUSTED`.
   - Por isso, `gemini_model` está configurado para `gemini-3.5-flash-lite` (que responde `200 OK` para chamadas normais e JSON estruturado nas Travas A e C), e todos os módulos possuem fallback resiliente para nunca travar caso o Gemini retorne `429`/`503`.
2. **Fontes Reais de Scraping que funcionam sem bloqueio de IP de Datacenter**:
   - **Zoom / Buscapé (`_search_zoom_live`)**: extrai `<script id="__NEXT_DATA__">` -> `props.initialReduxState.hits.hits` e expande a página de produto (`props.initialReduxState.offers.offerList`), trazendo Amazon, Magazine Luiza, KaBuM!, Fast Shop, etc.
   - **KaBuM! (`_search_kabum_live`)**: extrai `<script id="__NEXT_DATA__">` -> `props.pageProps.data` (`catalogServer.data`) com preço Pix (`priceWithDiscount`) e preço parcelado (`price`).
   - **Bing Shopping BR (`_search_bing_shopping_live`)**: extrai `a.br-offLink[href]`, lê o título completo em `span[title]` e decodifica o parâmetro Base64 `u=` para obter links diretos da `amazon.com.br` e `mercadolivre.com.br`.
   - **Pelando (`hunt_pelando_live`)**: extrai os cards ativos de `pelando.com.br/busca/...` e decodifica o token JWT em `https://dpl.pelando.com.br/r/<jwt>` para obter a URL direta da oferta (especialmente **Shopee Oficial** e **Mercado Livre**).
   - **Promobit (`hunt_promobit_live`)**: consome a API JSON `https://api.promobit.com.br/search?q=...` (`active_offers`).
   - **Cuponomia (`_scrape_cuponomia_store`)**: extrai cupons verificados hoje em `https://www.cuponomia.com.br/desconto/{slug}` filtrando marcas/nichos incompatíveis e exigindo gasto mínimo `>= 5x` para cupons de valor fixo em R$.
3. **Regras do `StrictSpecValidator` (`spec_validator.py`)**:
   - Impede que buscas como `"Nintendo Switch OLED"` tragam `"Switch Lite"` ou acessórios.
   - Protege variantes padrão de console (`NEVER_FORBID_ON_CONSOLE`: `"slim"`, `"digital"`, `"standard"`, `"bundle"`, `"jogos"`, `"1tb"`, `"825gb"`) para que buscas por `"PlayStation 5"` retornem os consoles PS5 Slim (~R$ 3.799–4.200) em vez de bloqueá-los.
   - Em buscas genéricas por especificação (ex.: `"Monitor 27 polegadas 144hz"`), preserva `27` e `144hz` na query das lojas e aceita `27"` como equivalente a `27 polegadas`.

---

## 5. Comandos Úteis

### Rodar testes do Backend localmente (Windows PowerShell)
```powershell
cd "c:\Users\kaua.ribeiro\OneDrive - Clinica Derma Skin\Área de Trabalho\Price\backend"
.\.venv\Scripts\python.exe -m pytest -v
```

### Enviar alterações para produção
```powershell
cd "c:\Users\kaua.ribeiro\OneDrive - Clinica Derma Skin\Área de Trabalho\Price"
git add .
git commit -m "sua mensagem"
git push origin main
```
