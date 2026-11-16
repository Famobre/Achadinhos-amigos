"""Gera o card da oferta (imagem 1080x1350, formato de post do Instagram).

Layout estilo vitrine de marketplace: faixa da loja, foto grande, selo de desconto,
"mais vendido", título, preço antigo riscado, preço novo grande, nota e chamada pro link.
"""
import io
import math
import os
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1080, 1350

LOJA = {
    "shopee": ("Shopee", (238, 77, 45)),
    "mercadolivre": ("Mercado Livre", (255, 230, 0)),
    "aliexpress": ("AliExpress", (230, 30, 50)),
    "amazon": ("Amazon", (35, 47, 62)),
    "outra": ("Oferta", (230, 0, 40)),
}
TXT = {
    "pt": {"vendidos": "vendidos", "mais": "MAIS VENDIDO", "frete": "Frete grátis", "cta": "COMPRE PELO LINK", "cupom": "CUPOM",
           "de": "de", "por": "por"},
    "en": {"vendidos": "sold", "mais": "BEST SELLER", "frete": "Free shipping", "cta": "TAP THE LINK TO BUY", "cupom": "COUPON",
           "de": "was", "por": "now"},
    "es": {"vendidos": "vendidos", "mais": "MÁS VENDIDO", "frete": "Envío gratis", "cta": "COMPRA EN EL LINK", "cupom": "CUPÓN",
           "de": "antes", "por": "ahora"},
}

_PASTAS_FONTES = [
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/System/Library/Fonts/Supplemental"),
]
_FONTES = {
    "bold": ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf"],
    "black": ["seguibl.ttf", "ariblk.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
    "reg": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"],
}
_cache = {}


def fonte(tipo: str, tam: int):
    chave = (tipo, tam)
    if chave in _cache:
        return _cache[chave]
    for nome in _FONTES[tipo]:
        for pasta in _PASTAS_FONTES:
            p = pasta / nome
            if p.exists():
                _cache[chave] = ImageFont.truetype(str(p), tam)
                return _cache[chave]
    _cache[chave] = ImageFont.load_default(tam)
    return _cache[chave]


def _foto(origem: str | None) -> Image.Image | None:
    if not origem:
        return None
    try:
        if str(origem).startswith("http"):
            dados = requests.get(origem, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).content
        else:
            dados = Path(origem.replace("file:///", "").replace("file://", "")).read_bytes()
        return Image.open(io.BytesIO(dados)).convert("RGBA")
    except Exception:
        return None


def _quebrar(d: ImageDraw.ImageDraw, texto: str, f, largura: int, linhas_max: int) -> list[str]:
    palavras, linhas, atual = texto.split(), [], ""
    for p in palavras:
        teste = (atual + " " + p).strip()
        if d.textlength(teste, font=f) <= largura:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = p
        if len(linhas) == linhas_max:
            break
    if len(linhas) < linhas_max and atual:
        linhas.append(atual)
    if len(linhas) == linhas_max and " ".join(linhas) != " ".join(palavras):
        while d.textlength(linhas[-1] + "…", font=f) > largura and " " in linhas[-1]:
            linhas[-1] = linhas[-1].rsplit(" ", 1)[0]
        linhas[-1] += "…"
    return linhas


def _estrela(d, cx, cy, r, cor):
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    d.polygon(pts, fill=cor)


def _dinheiro(v: float, moeda: str) -> tuple[str, str, str]:
    simb = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£", "MXN": "MX$", "CAD": "C$", "AUD": "A$"}.get(moeda, moeda)
    inteiro, cent = f"{v:,.2f}".split(".")
    if moeda in ("BRL", "EUR"):
        inteiro = inteiro.replace(",", ".")
    return simb, inteiro, cent


def _k(n: int) -> str:
    return f"{n/1000:.1f} mil".replace(".0 mil", " mil") if n >= 1000 else str(n)


def gerar(o: dict, idioma: str = "pt", assinatura: str = "") -> bytes:
    t = TXT.get(idioma, TXT["pt"])
    nome_loja, cor = LOJA.get(o.get("plataforma"), LOJA["outra"])
    img = Image.new("RGB", (W, H), (246, 246, 248))
    d = ImageDraw.Draw(img)

    # faixa da loja
    d.rectangle([0, 0, W, 110], fill=cor)
    tinta = (30, 30, 30) if (cor[0] * 0.3 + cor[1] * 0.59 + cor[2] * 0.11) > 170 else (255, 255, 255)
    d.text((48, 55), nome_loja.upper(), font=fonte("black", 46), fill=tinta, anchor="lm")
    if assinatura:
        d.text((W - 48, 55), assinatura, font=fonte("bold", 30), fill=tinta, anchor="rm")

    # área da foto
    card = [40, 140, W - 40, 780]
    d.rounded_rectangle(card, 28, fill=(255, 255, 255))
    foto = _foto(o.get("imagem"))
    if foto:
        foto = ImageOps.contain(foto, (card[2] - card[0] - 80, card[3] - card[1] - 60))
        x = card[0] + (card[2] - card[0] - foto.width) // 2
        y = card[1] + (card[3] - card[1] - foto.height) // 2
        img.paste(foto, (x, y), foto)
    else:
        d.text(((card[0] + card[2]) // 2, (card[1] + card[3]) // 2), nome_loja, font=fonte("black", 90), fill=(215, 215, 220), anchor="mm")

    # selos
    desconto = 0 if o.get("_feed") else int(o.get("desconto") or 0)
    antigo_inf = float(o.get("preco_antigo") or 0)
    if not desconto and antigo_inf > o["preco"] > 0:
        desconto = round((1 - o["preco"] / antigo_inf) * 100)
    if desconto >= 5:
        d.rounded_rectangle([card[0] + 24, card[1] + 24, card[0] + 250, card[1] + 120], 22, fill=(0, 166, 80))
        d.text((card[0] + 137, card[1] + 72), f"-{desconto}%", font=fonte("black", 58), fill="white", anchor="mm")
    if (o.get("vendas") or 0) >= 1000 or o.get("mais_vendido"):
        f = fonte("black", 30)
        larg = d.textlength(t["mais"], font=f) + 44
        d.rounded_rectangle([card[2] - 24 - larg, card[1] + 30, card[2] - 24, card[1] + 84], 10, fill=(255, 112, 0))
        d.text((card[2] - 24 - larg / 2, card[1] + 57), t["mais"], font=f, fill="white", anchor="mm")

    # título
    y = 815
    for linha in _quebrar(d, o.get("nome", ""), fonte("bold", 46), W - 120, 2):
        d.text((60, y), linha, font=fonte("bold", 46), fill=(25, 25, 30))
        y += 58

    # nota + vendidos
    y += 12
    nota = float(o.get("nota") or 0)
    info = []
    if nota > 0:
        for i in range(5):
            cheia = nota >= i + 0.75
            _estrela(d, 78 + i * 44, y + 20, 19, (52, 131, 250) if cheia else (205, 205, 210))
        d.text((78 + 5 * 44, y + 20), f"{nota:.1f}", font=fonte("bold", 34), fill=(90, 90, 95), anchor="lm")
        xi = 78 + 5 * 44 + 80
    else:
        xi = 60
    if o.get("vendas"):
        info.append(f"+{_k(int(o['vendas']))} {t['vendidos']}")
    if info:
        d.text((xi, y + 20), " · ".join(info), font=fonte("reg", 32), fill=(110, 110, 115), anchor="lm")
    if nota > 0 or info:
        y += 62

    # preços
    moeda = o.get("moeda", "BRL")
    antigo = 0 if o.get("_feed") else float(o.get("preco_antigo") or 0)
    if not antigo and desconto >= 5:
        antigo = o["preco"] / (1 - desconto / 100)
    if antigo > o["preco"]:
        s, i, c = _dinheiro(antigo, moeda)
        txt = f"{s} {i},{c}" if moeda in ("BRL", "EUR") else f"{s} {i}.{c}"
        fx = fonte("reg", 38)
        d.text((60, y), txt, font=fx, fill=(150, 150, 155))
        bb = d.textbbox((60, y), txt, font=fx)
        meio = (bb[1] + bb[3]) // 2
        d.line([(bb[0], meio), (bb[2], meio)], fill=(150, 150, 155), width=3)
        y += 52
    s, i, c = _dinheiro(o["preco"], moeda)
    fs, fi, fc = fonte("bold", 50), fonte("black", 118), fonte("bold", 50)
    x = 60
    d.text((x, y + 30), s, font=fs, fill=(25, 25, 30))
    x += d.textlength(s, font=fs) + 14
    d.text((x, y), i, font=fi, fill=(25, 25, 30))
    x += d.textlength(i, font=fi) + 6
    d.text((x, y + 14), c, font=fc, fill=(25, 25, 30))
    x += d.textlength(c, font=fc) + 36
    pilulas = []
    if o.get("condicao"):
        pilulas.append((o["condicao"].strip()[:18], (235, 240, 255), (40, 90, 200)))
    if o.get("frete_gratis"):
        pilulas.append((t["frete"], (225, 247, 234), (0, 140, 70)))
    fp = fonte("bold", 32)
    yy = y + 14 if len(pilulas) > 1 else y + 42
    for texto, fundo, tinta in pilulas:
        larg = d.textlength(texto, font=fp) + 32
        if x + larg > W - 40:
            break
        d.rounded_rectangle([x, yy, x + larg, yy + 52], 14, fill=fundo)
        d.text((x + 16, yy + 26), texto, font=fp, fill=tinta, anchor="lm")
        yy += 62

    # chamada
    cupom = (o.get("cupom") or "").strip().upper()
    if cupom:  # botão vira o cupom, com borda tracejada de "vale"
        d.rounded_rectangle([40, H - 150, W - 40, H - 40], 26, fill=(255, 196, 0))
        for xx in range(70, W - 70, 34):
            d.line([(xx, H - 136), (xx + 18, H - 136)], fill=(140, 90, 0), width=3)
            d.line([(xx, H - 54), (xx + 18, H - 54)], fill=(140, 90, 0), width=3)
        texto = f"{t['cupom']}: {cupom}"
        f = fonte("black", 52)
        while d.textlength(texto, font=f) > W - 160 and f.size > 30:
            f = fonte("black", f.size - 4)
        d.text((W // 2, H - 95), texto, font=f, fill=(40, 25, 0), anchor="mm")
    else:
        d.rounded_rectangle([40, H - 150, W - 40, H - 40], 26, fill=(52, 131, 250))
        d.text((W // 2, H - 95), t["cta"], font=fonte("black", 50), fill="white", anchor="mm")

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def so_foto(o: dict) -> bytes | None:
    """Só a foto do produto (sem preço nem texto), centralizada num quadrado branco."""
    foto = _foto(o.get("imagem"))
    if not foto:
        return None
    lado = 1080
    fundo = Image.new("RGB", (lado, lado), (255, 255, 255))
    foto = ImageOps.contain(foto, (lado - 60, lado - 60))
    fundo.paste(foto, ((lado - foto.width) // 2, (lado - foto.height) // 2), foto)
    buf = io.BytesIO()
    fundo.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


def imagem_do_post(o: dict, idioma: str = "pt", assinatura: str = "", estilo: str = "foto") -> bytes | None:
    return gerar(o, idioma, assinatura) if estilo == "card" else so_foto(o)
