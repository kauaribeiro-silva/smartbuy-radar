# SmartBuy Radar: Otimizador de Compra Perfeita

Plataforma full-stack multimodal para inteligência de compras no e-commerce brasileiro.

## Arquitetura
- **Frontend (`/frontend`)**: Next.js (App Router, TypeScript), Tailwind CSS, Lucide Icons, Framer Motion.
- **Backend (`/backend`)**: FastAPI assíncrono (`asyncio`), Server-Sent Events (SSE), `google-genai` (`gemini-2.5-flash` + Vision + Google Search Grounding), `Playwright` (`playwright-stealth`), `httpx`, `BeautifulSoup4`.

## Configuração
1. Copie `.env.example` para `.env` na raiz do projeto e preencha sua `GEMINI_API_KEY`.
2. Instale e suba o Backend e o Frontend conforme os comandos abaixo.

### Backend (Python + FastAPI)
```powershell
cd backend
uv venv
.\.venv\Scripts\activate
uv pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend (Next.js)
```powershell
cd frontend
npm install
npm run dev
```
