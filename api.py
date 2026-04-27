from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from main import (
    BASE_URL,
    buscar_metadados,
    listar_resultados_da_busca,
    obter_link_direto,
)

app = FastAPI(title="Books API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Livro(BaseModel):
    md5: str
    nome: str
    autor: str
    capa: str | None
    paginaAA: str


class LinkDireto(BaseModel):
    url: str


@app.get("/search", response_model=list[Livro])
def search(
    q: str = Query(..., min_length=1, description="Termo de busca"),
    pagina: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=50),
):
    resultados = listar_resultados_da_busca(q, pagina)
    return resultados[:limite]


@app.get("/download/{md5}", response_model=LinkDireto)
def download(md5: str):
    if not (len(md5) == 32 and all(c in "0123456789abcdef" for c in md5.lower())):
        raise HTTPException(400, "md5 inválido")

    meta = buscar_metadados(f"{BASE_URL}/md5/{md5}")
    slow_urls = meta.get("slowDownloadUrls") or []
    if not slow_urls:
        raise HTTPException(404, "Livro sem URLs de slow_download disponíveis")

    for slow_url in slow_urls[:3]:
        direto = obter_link_direto(slow_url)
        if direto:
            return LinkDireto(url=direto)

    raise HTTPException(503, "Não foi possível resolver o link direto (timeout ou captcha)")
