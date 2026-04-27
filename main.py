import re
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

BASE_URL = "https://annas-archive.gl"

# curl_cffi imita o TLS/JA3 do Chrome real e passa pelo DDoS-Guard
session = curl_requests.Session(impersonate="chrome")
session.headers.update({
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
})

# Mirrors externos conhecidos que aparecem listados na página /md5/
MIRROR_DOMAINS = (
    "libgen.li", "libgen.is", "libgen.rs", "libgen.st",
    "z-lib", "zlibrary",
    "library.lol", "annas-archive.se",
    "ipfs.io", "cloudflare-ipfs.com", "gateway.pinata.cloud",
    "dweb.link", "nftstorage.link", "w3s.link",
)


def buscar_livros(query: str, pagina: int = 1) -> list[dict]:
    """Lista md5+URL dos resultados (versão mínima, usada pelo scrape antigo)."""
    resultados = listar_resultados_da_busca(query, pagina)
    return [{"bookUrl": r["paginaAA"]} for r in resultados]


def listar_resultados_da_busca(query: str, pagina: int = 1) -> list[dict]:
    """Extrai TODOS os resultados (md5, nome, autor, capa) da página de busca
    em um único GET — sem visitar cada /md5/ individualmente."""
    resp = session.get(
        f"{BASE_URL}/search",
        params={"q": query, "page": pagina, "display": "list"},
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    resultados, vistos = [], set()
    for item in soup.select("div.js-aarecord-list-outer > div"):
        link = item.select_one("a[href*='/md5/']")
        if not link:
            continue
        m = re.search(r"/md5/([a-f0-9]{32})", link.get("href", ""), re.I)
        if not m or m.group(1) in vistos:
            continue
        md5 = m.group(1)
        vistos.add(md5)

        titulo_el = item.select_one("a.font-semibold.text-lg")
        # autor: o <a> que aponta pra /search?q=... e tem o ícone user-edit
        autor_el = item.select_one("a[href^='/search?q='] span.icon-\\[mdi--user-edit\\]")
        autor_a = autor_el.find_parent("a") if autor_el else None
        capa_el = item.select_one("img[src]")

        capa = capa_el.get("src") if capa_el else None
        resultados.append({
            "md5":       md5,
            "nome":      titulo_el.get_text(strip=True) if titulo_el else "Não encontrado",
            "autor":     autor_a.get_text(strip=True) if autor_a else "Não encontrado",
            "capa":      capa or None,
            "paginaAA":  f"{BASE_URL}/md5/{md5}",
        })
    return resultados


def buscar_metadados(book_url: str) -> dict:
    resp = session.get(book_url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # O card principal do livro é o primeiro div com sombra e fundo branco
    card = soup.select_one("div.shadow-lg.bg-white") or soup

    titulo_el = card.select_one("div.font-semibold.text-2xl")
    autor_el = card.find("a", class_=lambda c: bool(c) and "text-base" in c)
    capa_el = soup.select_one("img[src*='covers'], img[src*='cover']")

    def abs_url(href):
        if not href:
            return None
        return href if href.startswith("http") else BASE_URL + href

    # O div do título contém um <a>🔍</a> ao final; pegamos só o 1º trecho de texto
    titulo = next(iter(titulo_el.stripped_strings), None) if titulo_el else None

    slow_paths = sorted(set(re.findall(r"/slow_download/[a-f0-9]+/\d+/\d+", resp.text, re.I)))

    return {
        "titulo":          titulo or "Não encontrado",
        "autor":           autor_el.get_text(strip=True) if autor_el else "Não encontrado",
        "imagemCapa":      abs_url(capa_el.get("src")) if capa_el else "Sem capa",
        "paginaDoLivro":   book_url,
        "slowDownloadUrls": [BASE_URL + p for p in slow_paths],
        "_html":           resp.text,
    }


def extrair_mirrors(html: str) -> list[dict]:
    """Coleta links externos de mirrors (libgen, IPFS, z-library) da página /md5/."""
    soup = BeautifulSoup(html, "html.parser")
    encontrados = []
    vistos = set()

    for a in soup.select("a[href^='http']"):
        href = a["href"]
        host = urlparse(href).hostname or ""
        if not any(d in host for d in MIRROR_DOMAINS):
            continue
        if href in vistos:
            continue
        vistos.add(href)

        # Classifica o tipo do mirror pra deixar a saída mais útil
        if "ipfs" in host or "dweb.link" in host or "w3s.link" in host:
            tipo = "ipfs"
        elif "libgen" in host:
            tipo = "libgen"
        elif "z-lib" in host or "zlibrary" in host or "library.lol" in host:
            tipo = "z-library"
        else:
            tipo = "outro"

        encontrados.append({
            "tipo": tipo,
            "url": href,
            "label": a.get_text(strip=True)[:80] or host,
        })
    return encontrados


def _normalizar_url(url: str) -> str:
    """Reescapa caracteres ilegais (espaços, etc) sem dobrar %xx existentes."""
    parts = urlsplit(url)
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        quote(parts.path, safe="/%"),
        quote(parts.query, safe="=&%~()[]{},+;:@!$'*"),
        quote(parts.fragment, safe="%"),
    ))


def resolver_libgen(libgen_url: str) -> list[str]:
    """Segue libgen.li/file.php?id=... e extrai links HTTP de download direto
    (gateways IPFS, get.php do CDN). Esses não exigem captcha."""
    try:
        resp = session.get(libgen_url)
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    diretos, vistos = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith(("http://", "https://")):
            continue
        if "/ipfs/" in href or "get.php" in href.lower():
            href = _normalizar_url(href)
            if href in vistos:
                continue
            vistos.add(href)
            diretos.append(href)
    return diretos


_FILE_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+\.(?:epub|pdf|azw3|mobi|djvu|fb2|lit|cbz|cbr)"
    r"(?:\?[^\s\"'<>]*)?",
    re.I,
)


def obter_link_direto(slow_url: str, headless: bool = True, timeout_s: int = 60) -> str | None:
    """Abre /slow_download/ no Chromium, espera o DDoS-Guard liberar e o JS
    hidratar, e extrai a URL do arquivo (que a AA renderiza como texto na
    seção 'Download with short filename'). Retorna None se não aparecer."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        page = ctx.new_page()
        try:
            page.goto(slow_url, wait_until="domcontentloaded", timeout=60_000)

            # 1) espera DDoS-Guard sair (título muda)
            for _ in range(timeout_s):
                try:
                    if "DDoS" not in (page.title() or "DDoS"):
                        break
                except Exception:
                    pass  # navegação em andamento
                page.wait_for_timeout(1000)
            else:
                return None

            # 2) espera a URL aparecer no innerText (a AA injeta via JS)
            for _ in range(timeout_s):
                try:
                    txt = page.evaluate("document.body.innerText")
                except Exception:
                    page.wait_for_timeout(1000)
                    continue
                m = _FILE_URL_RE.search(txt or "")
                if m and "annas-archive" not in m.group():
                    return m.group()
                page.wait_for_timeout(1000)

            return None
        except PWTimeout:
            return None
        finally:
            browser.close()


def scrape(query: str, resolver_direto: bool = False):
    print(f"\nBuscando: {query}")
    livros = buscar_livros(query)
    print(f"   {len(livros)} resultado(s)\n")

    resultados = []
    for i, livro in enumerate(livros, 1):
        print(f"[{i}/{len(livros)}] {livro['bookUrl']}")
        meta = buscar_metadados(livro["bookUrl"])
        mirrors = extrair_mirrors(meta.pop("_html"))

        downloads = []
        for m in mirrors:
            if m["tipo"] == "libgen" and "file.php" in m["url"]:
                downloads.extend(resolver_libgen(m["url"]))

        resultado = {**meta, "mirrors": mirrors, "downloadsDiretos": downloads}
        resultados.append(resultado)

        print(f"  Título:    {resultado['titulo']}")
        print(f"  Autor:     {resultado['autor']}")
        print(f"  Diretos:   {len(downloads)}")
        for url in downloads[:3]:
            print(f"    - {url}")
        print()

    # Demo: tenta resolver o slow_download do 1º livro com URLs disponíveis
    if resolver_direto:
        for r in resultados:
            slow_urls = r.get("slowDownloadUrls") or []
            if not slow_urls:
                continue
            print(f"\n→ Tentando slow_download do livro: {r['titulo']}")
            for slow_url in slow_urls[:3]:
                print(f"   {slow_url}")
                direto = obter_link_direto(slow_url)
                if direto:
                    print(f"   ✓ {direto}")
                    r["linkDireto"] = direto
                    break
                print("   ✗ não consegui (captcha ou timeout)")
            break

    return resultados


if __name__ == "__main__":
    scrape("Harry Potter E a pedra filosofal", resolver_direto=True)
