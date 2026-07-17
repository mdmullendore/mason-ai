# mason-ai

A small FastAPI backend that answers visitor questions about Mason Mullendore, using
retrieval-augmented generation (RAG) over a bio/resume corpus. Deployed on Render;
consumed by a chat widget on [masonmullendore.com](https://masonmullendore.com) (hosted
separately on Vercel). Runs entirely on Google Gemini's free tier — both embeddings and
answer generation — no other LLM provider needed.

## How it works

- `content/*.md` holds the source material (currently a placeholder bio — replace with
  real resume/bio content).
- `scripts/ingest.py` chunks that content, embeds each chunk with Gemini
  (`gemini-embedding-001`), and writes `data/embeddings.json`. Re-run it whenever
  `content/` changes; the output is committed.
- At request time, `app/main.py`'s `POST /chat` embeds the incoming question, retrieves
  the top-k similar chunks via in-process cosine similarity (`app/rag.py`, plain numpy —
  no vector database), and streams back a Gemini-generated answer (`gemini-2.5-flash`)
  grounded in those chunks as Server-Sent Events.

No vector database, no separate embedding service to run — everything needed at request
time is the one committed `data/embeddings.json` file, loaded into memory on startup.

## Local development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GEMINI_API_KEY

python scripts/ingest.py       # (re)generate data/embeddings.json from content/
uvicorn app.main:app --reload  # serves on http://localhost:8000
```

Test it:

```bash
curl -N -X POST localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What does Mason do?"}'
```

## Updating content

1. Edit or add `.md` files under `content/`.
2. Re-run `python scripts/ingest.py`.
3. Commit the updated `data/embeddings.json` along with your content changes.
4. Redeploy (Render redeploys automatically on push, if connected to the repo).

## Deploying to Render

1. Push this repo to GitHub.
2. In Render, "New +" → "Blueprint", point it at the repo — it will pick up
   `render.yaml` automatically.
3. Fill in the two environment variables in the Render dashboard (they're marked
   `sync: false` in `render.yaml`, so Render prompts for them rather than expecting them
   committed): `GEMINI_API_KEY`, `ALLOWED_ORIGIN` (set this to
   `https://masonmullendore.com`).
4. Once deployed, confirm `https://<your-service>.onrender.com/health` returns
   `{"status": "ok"}`.

**Free-tier notes**:
- Render's free web services spin down after inactivity and cold-start on the next
  request (10–30s delay). The first question after idle time will feel slow — upgrade to
  a paid instance later if that matters.
- Gemini's free tier has request-per-minute rate limits (varies by model). Fine for a
  personal site's traffic; if you ever hit limits, Google's AI Studio dashboard shows
  current usage against the free-tier quota.

## Wiring up the frontend widget

This repo doesn't touch the masonmullendore.com frontend — it only exposes the API. A
minimal React component consuming it (reading the SSE stream via `fetch` + a body
reader, since `EventSource` doesn't support POST bodies):

```tsx
import { useState } from "react";

const API_URL = "https://<your-service>.onrender.com/chat";

export function ChatWidget() {
  const [answer, setAnswer] = useState("");

  async function ask(message: string) {
    setAnswer("");
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6);
        if (payload === "[DONE]") continue;
        const { delta } = JSON.parse(payload);
        setAnswer((prev) => prev + delta);
      }
    }
  }

  return (
    <div>
      <button onClick={() => ask("What does Mason do?")}>Ask a question</button>
      <p>{answer}</p>
    </div>
  );
}
```

Drop this into the actual site repo and swap the hardcoded question for a real input
field once you're ready to wire it up.
