"""Lê a página do produto a partir do link de afiliado e tenta preencher tudo sozinho.

Funciona como a prévia de link do WhatsApp: abre a página uma vez e lê os dados
públicos que a loja publica pra redes sociais e buscadores (Open Graph e JSON-LD).
Se a loja não entregar algum dado, o campo fica vazio pra você completar.
"""
import html as H
import json
import re
from urllib.parse import urljoin, urlparse

import requests

NAVEGADOR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}
# o mesmo "leitor de prévia" que o Facebook/WhatsApp usam — muitas lojas entregam os dados prontos pra ele
PREVIA = {**NAVEGADOR, "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"}


def plataforma_de(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "mercadoli" in host or "mercadolibre" in host or "meli.la" in host:
        return "mercadolivre"
    if "shopee" in host or "shp.ee" in host:
        return "shopee"
    if "aliexpress" in host or "s.click.ali" in host:
        return "aliexpress"
    if "amazon." in host or "amzn." in host or "a.co" == host:
        return "amazon"
    return "outra"


def _num(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _meta(html: str, *nomes) -> str:
    for n in nomes:
        for padrao in (rf'<meta[^>]+(?:property|name|itemprop)=["\']{re.escape(n)}["\'][^>]*content=["\']([^"\']+)',
                       rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name|itemprop)=["\']{re.escape(n)}["\']'):
            m = re.search(padrao, html, re.I)
            if m:
                return H.unescape(m.group(1)).strip()
    return ""


def _jsonld_produto(html: str) -> dict:
    for bloco in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            dado = json.loads(bloco.strip())
        except ValueError:
            continue
        itens = dado if isinstance(dado, list) else dado.get("@graph", [dado]) if isinstance(dado, dict) else []
        for it in itens:
            if isinstance(it, dict) and "Product" in str(it.get("@type", "")):
                return it
    return {}


def _ler(html: str, url: str) -> dict:
    d = {}
    p = _jsonld_produto(html)
    if p:
        d["nome"] = p.get("name")
        img = p.get("image")
        d["imagem"] = img[0] if isinstance(img, list) and img else (img.get("url") if isinstance(img, dict) else img)
        ofertas = p.get("offers") or {}
        if isinstance(ofertas, list):
            ofertas = ofertas[0] if ofertas else {}
        d["preco"] = _num(ofertas.get("price") or ofertas.get("lowPrice"))
        d["moeda"] = ofertas.get("priceCurrency")
        nota = (p.get("aggregateRating") or {})
        d["nota"] = _num(nota.get("ratingValue"))
        d["avaliacoes"] = int(_num(nota.get("reviewCount") or nota.get("ratingCount")))
    d["nome"] = d.get("nome") or _meta(html, "og:title", "twitter:title")
    d["imagem"] = d.get("imagem") or _meta(html, "og:image", "og:image:secure_url", "twitter:image")
    d["preco"] = d.get("preco") or _num(_meta(html, "product:price:amount", "og:price:amount", "price"))
    d["moeda"] = d.get("moeda") or _meta(html, "product:price:currency", "og:price:currency", "priceCurrency")

    # Mercado Livre: preço antigo e frete vêm no estado da página
    if plataforma_de(url) == "mercadolivre":
        m = re.search(r'"original_price"\s*:\s*([\d.]+)', html)
        if m:
            d["preco_antigo"] = float(m.group(1))
        if not d.get("preco"):
            m = re.search(r'"price"\s*:\s*\{[^}]*"value"\s*:\s*([\d.]+)', html) or re.search(r'"price"\s*:\s*([\d.]+)', html)
            if m:
                d["preco"] = float(m.group(1))
        if re.search(r'"free_shipping"\s*:\s*true|Frete grátis|Chegará grátis', html):
            d["frete_gratis"] = True
        m = re.search(r'\+?\s*([\d.]+)\s*mil\s+vendidos', html) or re.search(r'\+?\s*(\d+)\s+vendidos', html)
        if m:
            n = _num(m.group(1))
            d["vendas"] = int(n * 1000) if "mil" in m.group(0) else int(n)
    # limpa sufixos de título tipo " | Mercado Livre"
    if d.get("nome"):
        d["nome"] = re.split(r"\s+[|\-–]\s+(Mercado Livre|Shopee|AliExpress|Amazon)", d["nome"])[0].strip()
    if d.get("imagem"):
        d["imagem"] = urljoin(url, d["imagem"])
    return {k: v for k, v in d.items() if v}


def _link_produto_ml(html: str, base: str) -> str | None:
    """Link social do ML (perfil de afiliado) → acha o link do produto dentro da página."""
    m = re.search(r'https?://(?:www\.|produto\.)?mercadoli[^"\'\s<>]+(?:/p/MLB\d+|MLB-?\d+)[^"\'\s<>]*', html)
    return m.group(0).replace("\\u002F", "/") if m else None


def extrair(link: str) -> dict:
    link = (link or "").strip()
    if not link.startswith("http"):
        raise ValueError("Cole o link completo (começando com https://)")
    sessao = requests.Session()
    resultado, url_final = {}, link
    for cab in (PREVIA, NAVEGADOR):
        try:
            r = sessao.get(link, headers=cab, timeout=20, allow_redirects=True)
        except requests.RequestException:
            continue
        url_final = r.url
        if r.ok and "text/html" in r.headers.get("content-type", "text/html"):
            achado = _ler(r.text, url_final)
            # link "social" do ML aponta pra página do afiliado: segue pro produto
            if plataforma_de(url_final) == "mercadolivre" and "/social/" in url_final and not achado.get("preco"):
                prod = _link_produto_ml(r.text, url_final)
                if prod:
                    try:
                        r2 = sessao.get(prod, headers=NAVEGADOR, timeout=20)
                        if r2.ok:
                            achado = {**achado, **_ler(r2.text, r2.url)}
                    except requests.RequestException:
                        pass
            resultado = {**achado, **resultado}  # o que já achou tem prioridade
        if resultado.get("nome") and resultado.get("preco") and resultado.get("imagem"):
            break
    resultado["plataforma"] = plataforma_de(url_final) if plataforma_de(url_final) != "outra" else plataforma_de(link)
    resultado.setdefault("moeda", "BRL")
    return resultado


# ----------------------------------------------------------------------------
# Plano B: navegador invisível (o mesmo motor Edge/WebView2 que desenha o painel).
# Lojas como Amazon e Shopee só mostram o produto pra navegador de verdade.
# ----------------------------------------------------------------------------
JS_LER = r"""
(() => {
  const q = s => document.querySelector(s);
  const txt = s => { const e = q(s); return e ? (e.innerText || e.textContent || '').trim() : ''; };
  const meta = n => { const e = q(`meta[property="${n}"],meta[name="${n}"],meta[itemprop="${n}"]`); return e ? e.content : ''; };
  const num = s => { s = (s || '').replace(/[^\d,.]/g, ''); if (s.includes(',') && s.lastIndexOf(',') > s.lastIndexOf('.')) s = s.replace(/\./g, '').replace(',', '.'); else s = s.replace(/,/g, ''); return parseFloat(s) || 0; };
  const d = {url: location.href};
  let ld = null;
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
    try { let j = JSON.parse(s.textContent); (Array.isArray(j) ? j : (j['@graph'] || [j])).forEach(x => { if (String(x['@type']).includes('Product')) ld = x; }); } catch (e) {}
  });
  if (ld) {
    d.nome = ld.name; let im = ld.image; d.imagem = Array.isArray(im) ? im[0] : (im && im.url) || im;
    let of = Array.isArray(ld.offers) ? ld.offers[0] : (ld.offers || {});
    d.preco = num(String(of.price || of.lowPrice || '')); d.moeda = of.priceCurrency;
    if (ld.aggregateRating) { d.nota = num(String(ld.aggregateRating.ratingValue)); }
  }
  const h = location.hostname;
  if (h.includes('amazon')) {
    d.nome = txt('#productTitle') || d.nome;
    d.preco = num(txt('#corePrice_feature_div .a-price .a-offscreen') || txt('#corePriceDisplay_desktop_feature_div .a-price .a-offscreen') || txt('.a-price .a-offscreen')) || d.preco;
    d.preco_antigo = num(txt('#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen') || txt('.basisPrice .a-offscreen') || txt('.a-text-price .a-offscreen'));
    const img = q('#landingImage') || q('#imgBlkFront');
    if (img) d.imagem = img.getAttribute('data-old-hires') || img.src;
    const nota = q('#acrPopover'); if (nota) d.nota = num((nota.getAttribute('title') || '').split(' ')[0]);
    const rev = txt('#acrCustomerReviewText'); if (rev) d.avaliacoes = parseInt(rev.replace(/\D/g, '')) || 0;
    const comp = txt('#social-proofing-faceout-title-tk_bought'); if (comp) { const m = comp.match(/([\d.]+)\s*(mil)?/); if (m) d.vendas = Math.round(num(m[1]) * (m[2] ? 1000 : 1)); }
    if (/frete gr[aá]tis|entrega gr[aá]tis/i.test(txt('#deliveryBlockMessage') || txt('#mir-layout-DELIVERY_BLOCK'))) d.frete_gratis = true;
    d.moeda = d.moeda || (h.endsWith('.br') ? 'BRL' : h.endsWith('.com') ? 'USD' : '');
  } else if (h.includes('mercadoli')) {
    d.nome = txt('.ui-pdp-title') || d.nome;
    d.preco = num(meta('price')) || num(txt('.ui-pdp-price__second-line .andes-money-amount__fraction')) || d.preco;
    const ant = q('.ui-pdp-price__original-value .andes-money-amount__fraction'); if (ant) d.preco_antigo = num(ant.innerText + ',' + (txt('.ui-pdp-price__original-value .andes-money-amount__cents') || '00'));
    const img = q('.ui-pdp-gallery__figure img'); if (img) d.imagem = img.getAttribute('data-zoom') || img.src;
    const r = txt('.ui-pdp-review__rating'); if (r) d.nota = num(r);
    const v = txt('.ui-pdp-subtitle'); const m = v.match(/\+?\s*([\d.]+)\s*(mil)?\s*vendidos/); if (m) d.vendas = Math.round(num(m[1]) * (m[2] ? 1000 : 1));
    if (/frete gr[aá]tis|chegar[aá] gr[aá]tis/i.test(document.body.innerText.slice(0, 20000))) d.frete_gratis = true;
  }
  d.nome = d.nome || meta('og:title') || document.title;
  d.imagem = d.imagem || meta('og:image');
  if (!d.preco) { // genérico (Shopee e outras): primeiro preço que aparecer na tela
    const m = (document.body.innerText || '').match(/R\$\s?([\d.]+,\d{2})/); if (m) { d.preco = num(m[1]); d.moeda = 'BRL'; }
  }
  if (!d.preco_antigo && h.includes('shopee')) {
    const ps = (document.body.innerText || '').match(/R\$\s?[\d.]+,\d{2}/g) || [];
    const vals = ps.slice(0, 4).map(num).filter(Boolean); if (vals.length > 1 && Math.max(...vals) > d.preco) d.preco_antigo = Math.max(...vals);
  }
  return JSON.stringify(d);
})()
"""


def extrair_navegador(link: str, webview_mod, espera: int = 25) -> dict:
    """Abre o link numa janela invisível, espera a página montar e lê os dados da tela."""
    import threading
    import time
    pronto = threading.Event()
    jan = webview_mod.create_window("leitor", link, hidden=True, width=1280, height=900)
    try:
        jan.events.loaded += lambda *a: pronto.set()
        pronto.wait(espera)
        dados, fim = {}, time.time() + 12
        while time.time() < fim:  # páginas em JavaScript (Shopee) demoram pra mostrar o preço
            try:
                bruto = jan.evaluate_js(JS_LER)
                dados = json.loads(bruto) if isinstance(bruto, str) else (bruto or {})
            except Exception:
                dados = {}
            if dados.get("nome") and dados.get("preco"):
                break
            time.sleep(1.5)
        url = dados.pop("url", link)
        dados = {k: v for k, v in dados.items() if v}
        if dados.get("nome"):
            dados["nome"] = re.split(r"\s+[|\-–]\s+(Mercado Livre|Shopee|AliExpress|Amazon)", dados["nome"])[0].strip()
        if dados.get("imagem"):
            dados["imagem"] = urljoin(url, dados["imagem"])
        dados["plataforma"] = plataforma_de(url) if plataforma_de(url) != "outra" else plataforma_de(link)
        return dados
    finally:
        try:
            jan.destroy()
        except Exception:
            pass
