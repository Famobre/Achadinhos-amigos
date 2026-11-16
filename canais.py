"""Canais onde o bot posta. Todos por API oficial / webhook — nada de robô em navegador.

Tipos:
  telegram  — canal/grupo do Telegram (bot como admin)
  discord   — webhook de um canal do Discord
  postiz    — Postiz (gitroomhq/postiz-app): Instagram, X, Facebook (Página), Threads,
              LinkedIn, Bluesky, Mastodon... tudo pelas APIs oficiais
  webhook   — manda o JSON da oferta pra qualquer URL (ex.: n8n gerar vídeo pro TikTok/Kwai)
"""
import base64
import html
import io
import json
import random
import time
import uuid

import requests

import config

# ---------------------------------------------------------------- textos
SIMBOLO = {"BRL": "R$ ", "USD": "US$ ", "EUR": "€", "GBP": "£", "MXN": "MX$ ", "CAD": "C$ ",
           "AUD": "A$ ", "JPY": "¥", "INR": "₹", "ARS": "AR$ ", "CLP": "CLP$ ", "COP": "COL$ "}
NOME_LOJA = {"shopee": "🟠 Shopee", "mercadolivre": "🟡 Mercado Livre", "aliexpress": "🔴 AliExpress", "amazon": "📦 Amazon"}

T = {
    "pt": {"chamadas": ["🔥 ACHADINHO DO DIA", "😱 PREÇO BAIXOU", "⚡ OFERTA RELÂMPAGO", "🛒 VALE MUITO A PENA"],
           "por": "Por apenas", "off": "OFF", "nota": "de avaliação", "vendidos": "vendidos",
           "frete": "🚚 Frete grátis", "comprar": "COMPRAR AGORA",
           "amazon": "Como associado da Amazon, ganho com compras qualificadas."},
    "en": {"chamadas": ["🔥 DEAL OF THE DAY", "😱 PRICE DROP", "⚡ FLASH DEAL", "🛒 WORTH IT"],
           "por": "Only", "off": "OFF", "nota": "rating", "vendidos": "sold",
           "frete": "🚚 Free shipping", "comprar": "BUY NOW",
           "amazon": "As an Amazon Associate I earn from qualifying purchases."},
    "es": {"chamadas": ["🔥 OFERTA DEL DÍA", "😱 BAJÓ DE PRECIO", "⚡ OFERTA RELÁMPAGO", "🛒 VALE LA PENA"],
           "por": "Solo", "off": "OFF", "nota": "de valoración", "vendidos": "vendidos",
           "frete": "🚚 Envío gratis", "comprar": "COMPRAR AHORA",
           "amazon": "Como afiliado de Amazon, gano con compras que cumplen los requisitos."},
}


def dinheiro(v: float, moeda: str = "BRL") -> str:
    s = f"{v:,.2f}"
    if moeda in ("BRL", "EUR", "ARS", "CLP", "COP"):
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{SIMBOLO.get(moeda, moeda + ' ')}{s}"


CUPOM_T = {"pt": "Use o cupom", "en": "Use coupon", "es": "Usa el cupón"}
ANUNCIO = {"pt": "#anúncio", "en": "#ad", "es": "#anuncio"}


def _milhar(n: int) -> str:
    if n >= 1000:
        return f"+{n // 1000} mil"
    return f"+{n}"


def prova_social(o: dict, idioma: str = "pt") -> str:
    """'⭐ 4,9 • +10 mil vendidos' — quem vê que muita gente comprou, compra mais."""
    if idioma != "pt":
        return ""
    partes = []
    nota = float(o.get("nota") or 0)
    if nota >= 4.5:
        partes.append(f"⭐ {nota:.1f}".replace(".", ","))
    vendas = int(o.get("vendas") or 0)
    if vendas >= 100:
        partes.append(f"{_milhar(vendas)} vendidos")
    return " • ".join(partes)


def campanha_atual(hoje=None) -> str:
    """Selo de época que vai no topo do post (11.11, Black Friday, Natal...). Vazio fora das datas."""
    from datetime import date
    d = hoje or date.today()
    m, dia = d.month, d.day
    if m == 11 and dia < 11:
        return "⏳ ESQUENTA 11.11"
    if m == 11 and dia == 11:
        return "🔥 HOJE É 11.11 — DIA DE CUPOM"
    if m == 11 and dia < 27:
        return "🖤 ESQUENTA BLACK FRIDAY"
    if (m == 11 and dia >= 27) or (m == 12 and dia == 1):
        return "🖤 BLACK FRIDAY"
    if m == 12 and dia <= 12:
        return "🎄 ACHADOS PRO NATAL"
    if m == 12 and dia <= 24:
        return "🎁 PRESENTE DE ÚLTIMA HORA"
    return ""


def montar(o: dict, idioma: str, textos_ia: dict, formato: str = "texto") -> str:
    """Post curto no estilo dos canais de ofertas que mais vendem:
    🔥 produto / ❌ De / ✅ Por ... no Pix! / 👉 cupom / 🛒 link / #anúncio
    formato: 'html' (Telegram), 'md' (Discord) ou 'texto' (redes sociais)."""
    t = T.get(idioma, T["pt"])
    ia = textos_ia.get(idioma) or {}
    esc = html.escape if formato == "html" else (lambda s: s)
    neg = (lambda s: f"<b>{s}</b>") if formato == "html" else (lambda s: f"**{s}**") if formato == "md" \
        else (lambda s: f"*{s}*") if formato == "zap" else (lambda s: s)
    risc = (lambda s: f"<s>{s}</s>") if formato == "html" else (lambda s: f"~~{s}~~") if formato == "md" \
        else (lambda s: f"~{s}~") if formato == "zap" else (lambda s: s)
    copiar = (lambda s: f"<code>{html.escape(s)}</code>") if formato == "html" else (lambda s: f"`{s}`") if formato == "md" else (lambda s: s)
    moeda = o.get("moeda", "BRL")

    linhas = []
    selo = campanha_atual() if idioma == "pt" and config.carregar().get("selo_campanha", True) else ""
    if selo:
        linhas.append(neg(esc(selo)))
    if ia.get("chamada"):
        linhas += [neg(esc(ia["chamada"])), ""]
    linhas.append(f"🔥 {neg(esc(o['nome'][:110]))}")
    if ia.get("descricao"):
        linhas.append(esc(ia["descricao"]))
    prova = prova_social(o, idioma)
    if prova:
        linhas.append(prova)
    linhas.append("")
    # feed da Shopee: o "preço original" dele nem sempre aparece na página — não mostra pra não parecer desconto falso
    antigo = 0 if o.get("_feed") else float(o.get("preco_antigo") or 0)
    if antigo > o["preco"]:
        de = {"pt": "De", "en": "Was", "es": "Antes"}.get(idioma, "De")
        linhas.append(f"❌ {de} {risc(dinheiro(antigo, moeda))}")
    cond = (o.get("condicao") or "").strip()
    por = {"pt": "Por", "en": "Now", "es": "Ahora"}.get(idioma, "Por")
    off = f" ({int(o['desconto'])}% OFF)" if o.get("desconto", 0) >= 5 and antigo <= o["preco"] and not o.get("_feed") else ""
    linhas.append(f"✅ {por} {neg(dinheiro(o['preco'], moeda))}{(' ' + esc(cond)) if cond else ''}!{off}")
    if o.get("frete_gratis"):
        linhas.append(t["frete"])
    cupom = (o.get("cupom") or "").strip()
    if cupom:
        linhas += ["", f"👉 {CUPOM_T.get(idioma, CUPOM_T['pt'])} {neg(copiar(cupom))}"]
    linhas.append("")
    if formato == "html":  # link curto aparece inteiro; link gigante vira texto clicável
        rotulo = o["link"] if len(o["link"]) <= 45 else t["comprar"]
        linhas.append(f"🛒 <a href=\"{html.escape(o['link'])}\">{html.escape(rotulo)}</a>")
    elif formato == "md":
        linhas.append(f"🛒 <{o['link']}>")
    else:
        linhas.append(f"🛒 {o['link']}")
    if o["plataforma"] == "amazon":
        linhas += ["", f"ℹ️ {t['amazon']}"]
    rodape = (config.carregar().get("rodape") or "").strip()
    if rodape and formato != "html":  # no próprio Telegram não precisa
        linhas += ["", rodape]
    linhas += ["", ANUNCIO.get(idioma, "#anúncio")]
    return "\n".join(linhas)


def curto(o: dict, idioma: str, textos_ia: dict) -> str:
    """Versão curta pro X/Bluesky (280 caracteres) sem cortar o link."""
    t = T.get(idioma, T["pt"])
    ia = textos_ia.get(idioma) or {}
    chamada = ia.get("chamada") or random.choice(t["chamadas"])
    preco = dinheiro(o["preco"], o.get("moeda", "BRL"))
    off = f" (-{int(o['desconto'])}%)" if o.get("desconto", 0) >= 5 and not o.get("_feed") else ""
    fim = f"\n👉 {o['link']}"
    cup = f"\n🎟️ {o['cupom']}" if o.get("cupom") else ""
    corpo = f"{chamada}\n{o['nome']}\n💰 {preco}{off}{cup}"
    sobra = 280 - len(fim) - 1
    if len(corpo) > sobra:
        corpo = corpo[:sobra - 1].rstrip() + "…"
    return corpo + fim


# ---------------------------------------------------------------- card (imagem)
def card(o: dict, idioma: str) -> bytes | None:
    """Gera (uma vez por idioma) o card da oferta. None se desligado ou se der erro."""
    cfg = config.carregar()
    if not cfg.get("usar_card", True):
        return None
    estilo = cfg.get("estilo_imagem", "foto")
    cache = o.setdefault("_cards", {})
    chave = idioma if estilo == "card" else "foto"  # a foto pura é igual em todos os idiomas
    if chave not in cache:
        try:
            import cards
            assinatura = config.cred("TELEGRAM_CANAL")
            assinatura = assinatura if assinatura.startswith("@") else ""
            cache[chave] = cards.imagem_do_post(o, idioma, assinatura, estilo)
        except Exception as e:
            try:
                print(f"[card] falhou: {e}")
            except Exception:
                pass
            cache[chave] = None
    return cache[chave]


# ---------------------------------------------------------------- envio
def botoes_telegram(o: dict, chat: str) -> str:
    """Botões embaixo do post: comprar (mais cliques que link no texto) e compartilhar o canal."""
    from urllib.parse import quote
    linhas = [[{"text": "🛒 VER OFERTA", "url": o["link"]}]]
    canal = (chat or "").lstrip("@")
    if canal and not canal.lstrip("-").isdigit():
        convite = f"https://t.me/{canal}"
        linhas.append([{"text": "📤 Mandar pra um amigo",
                        "url": "https://t.me/share/url?url=" + quote(convite) +
                               "&text=" + quote("Olha esse canal de achadinhos, sempre tem oferta boa 👀")}])
    zap = (config.carregar().get("link_whatsapp") or "").strip()
    if zap.startswith("http"):
        linhas.append([{"text": "💬 Canal no WhatsApp", "url": zap}])
    return json.dumps({"inline_keyboard": linhas})


def _telegram(c: dict, o: dict, textos: dict):
    token = c.get("token") or config.cred("TELEGRAM_TOKEN")
    chat = c.get("chat")
    if not (token and chat):
        raise RuntimeError("Telegram sem token ou sem @canal")
    texto = montar(o, c.get("idioma", "pt"), textos, "html")
    base = f"https://api.telegram.org/bot{token}"
    extra = {"reply_markup": botoes_telegram(o, chat)} if config.carregar().get("botoes_telegram", True) else {}

    def enviar(metodo, dados, **kw):
        r = requests.post(f"{base}/{metodo}", data={**dados, **extra}, timeout=60, **kw)
        if not r.ok and extra and "button" in r.text.lower():  # botão recusado: manda sem
            r = requests.post(f"{base}/{metodo}", data=dados, timeout=60, **kw)
        return r

    img = card(o, c.get("idioma", "pt"))
    if img:
        r = enviar("sendPhoto", {"chat_id": chat, "caption": texto[:1024], "parse_mode": "HTML"},
                   files={"photo": ("oferta.jpg", img, "image/jpeg")})
        if r.ok:
            return
    if o.get("imagem", "").startswith("http"):
        r = enviar("sendPhoto", {"chat_id": chat, "photo": o["imagem"], "caption": texto[:1024], "parse_mode": "HTML"})
        if r.ok:
            return
    r = enviar("sendMessage", {"chat_id": chat, "text": texto, "parse_mode": "HTML"})
    if not r.ok:
        raise RuntimeError(f"Telegram recusou: {r.text[:150]}")


def _discord(c: dict, o: dict, textos: dict):
    url = c.get("webhook")
    if not url:
        raise RuntimeError("Discord sem webhook")
    texto = montar(o, c.get("idioma", "pt"), textos, "md")
    corpo = {"username": c.get("nome") or "Achadinhos", "content": texto[:1900]}
    img = card(o, c.get("idioma", "pt"))
    if img:
        r = requests.post(url, data={"payload_json": json.dumps(corpo)},
                          files={"files[0]": ("oferta.jpg", img, "image/jpeg")}, timeout=60)
    else:
        if (o.get("imagem") or "").startswith("http"):
            corpo["embeds"] = [{"image": {"url": o["imagem"]}, "url": o["link"], "color": 0xFF1F3D}]
        r = requests.post(url, json=corpo, timeout=30)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Discord recusou ({r.status_code}): {r.text[:150]}")


def _webhook(c: dict, o: dict, textos: dict):
    url = c.get("url")
    if not url:
        raise RuntimeError("Webhook sem URL")
    idiomas = c.get("idiomas") or ["pt", "en", "es"]
    corpo = {
        "evento": "nova_oferta", "enviado_em": int(time.time()),
        "produto": {k: o.get(k) for k in ("id", "plataforma", "nome", "preco", "moeda", "desconto", "nota",
                                           "vendas", "frete_gratis", "imagem", "link", "pais")},
        "textos": {i: {"post": montar(o, i, textos, "texto"), **(textos.get(i) or {})} for i in idiomas},
        # card pronto (JPEG em base64) por idioma — dá pra usar direto como imagem do vídeo no n8n
        "cards": {i: base64.b64encode(card(o, i)).decode() for i in idiomas if card(o, i)},
    }
    r = requests.post(url, json=corpo, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Webhook respondeu {r.status_code}: {r.text[:120]}")


_postiz_cache = {}


def postiz_integracoes(c: dict) -> list[dict]:
    base = (c.get("url") or "https://api.postiz.com/public/v1").rstrip("/")
    r = requests.get(f"{base}/integrations", headers={"Authorization": c.get("api_key", "")}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Postiz recusou ({r.status_code}): {r.text[:150]}")
    return r.json() if isinstance(r.json(), list) else r.json().get("integrations", [])


# plataformas que precisam de vídeo ou de configuração extra — essas ficam pro n8n/vídeo
POSTIZ_PULAR = {"tiktok", "youtube", "pinterest", "instagram-standalone-reels"}


def _postiz(c: dict, o: dict, textos: dict):
    base = (c.get("url") or "https://api.postiz.com/public/v1").rstrip("/")
    h = {"Authorization": c.get("api_key", "")}
    if not h["Authorization"]:
        raise RuntimeError("Postiz sem API key")
    chave = base + h["Authorization"]
    if chave not in _postiz_cache or time.time() - _postiz_cache[chave][0] > 3600:
        _postiz_cache[chave] = (time.time(), postiz_integracoes(c))
    integ = [i for i in _postiz_cache[chave][1] if not i.get("disabled")]
    escolhidas = set(c.get("integracoes") or [])
    if escolhidas:
        integ = [i for i in integ if i.get("id") in escolhidas]
    integ = [i for i in integ if (i.get("identifier") or i.get("providerIdentifier")) not in POSTIZ_PULAR]
    if not integ:
        raise RuntimeError("Postiz: nenhuma rede compatível conectada (TikTok/YouTube ficam pro vídeo do n8n)")

    imagens = []
    img = card(o, c.get("idioma", "pt"))
    if img or (o.get("imagem") or "").startswith("http"):
        try:
            img = img or requests.get(o["imagem"], timeout=30).content
            up = requests.post(f"{base}/upload", headers=h, timeout=60,
                               files={"file": (f"{uuid.uuid4().hex[:8]}.jpg", io.BytesIO(img), "image/jpeg")})
            if up.ok:
                d = up.json()
                imagens = [{"id": d.get("id"), "path": d.get("path")}]
        except requests.RequestException:
            pass

    texto = montar(o, c.get("idioma", "pt"), textos, "texto")
    posts = []
    for i in integ:
        tipo = i.get("identifier") or i.get("providerIdentifier") or ""
        settings = {"__type": tipo}
        if tipo.startswith("instagram"):
            if not imagens:
                continue  # Instagram não aceita post sem imagem
            settings["post_type"] = "post"
        if tipo == "x":
            settings["who_can_reply_post"] = "everyone"
        conteudo = curto(o, c.get("idioma", "pt"), textos) if tipo in ("x", "bluesky") else texto
        posts.append({"integration": {"id": i["id"]}, "value": [{"content": conteudo, "image": imagens}],
                      "settings": settings})
    if not posts:
        raise RuntimeError("Postiz: sem imagem pra postar no Instagram")
    corpo = {"type": "now", "date": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
             "shortLink": False, "tags": [], "posts": posts}
    r = requests.post(f"{base}/posts", json=corpo, headers={**h, "Content-Type": "application/json"}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Postiz recusou o post ({r.status_code}): {r.text[:150]}")


ENVIAR = {"telegram": _telegram, "discord": _discord, "webhook": _webhook, "postiz": _postiz}


def canais_ativos(cfg: dict) -> list[dict]:
    lista = []
    if config.cred("TELEGRAM_TOKEN") and config.cred("TELEGRAM_CANAL"):
        lista.append({"id": "principal", "tipo": "telegram", "nome": "Telegram principal", "ativo": True,
                      "idioma": "pt", "chat": config.cred("TELEGRAM_CANAL"), "plataformas": []})
    lista += [c for c in cfg.get("canais", []) if c.get("ativo", True)]
    return lista


def idioma_de(c: dict) -> list[str]:
    return c.get("idiomas") if c.get("tipo") == "webhook" else [c.get("idioma", "pt")]


def enviar_principal(texto: str, fixar: bool = False) -> int:
    """Manda um texto (HTML) no canal principal do Telegram. Retorna o id da mensagem."""
    token, chat = config.cred("TELEGRAM_TOKEN"), config.cred("TELEGRAM_CANAL")
    if not (token and chat):
        raise RuntimeError("Telegram principal não configurado (aba Contas)")
    base = f"https://api.telegram.org/bot{token}"
    r = requests.post(f"{base}/sendMessage", data={"chat_id": chat, "text": texto, "parse_mode": "HTML",
                                                    "disable_web_page_preview": "true"}, timeout=30)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"Telegram: {j.get('description')}")
    mid = j["result"]["message_id"]
    if fixar:
        requests.post(f"{base}/pinChatMessage", data={"chat_id": chat, "message_id": mid,
                                                      "disable_notification": "true"}, timeout=30)
    return mid


BOAS_VINDAS = (
    "👋 <b>Bem-vindo(a) ao Achadinhos!</b>\n\n"
    "Aqui saem ofertas da Shopee e do Mercado Livre filtradas por avaliação e desconto:\n"
    "✅ Só produto bem avaliado\n✅ Preço que vale a pena de verdade\n✅ Cupom quando tiver\n\n"
    "🕗 Ofertas todo dia, das 7h às 23h\n"
    "🔔 <b>Ativa as notificações</b> pra não perder as relâmpago (elas acabam rápido)\n"
    "📤 Achou algo bom? Encaminha pra quem vai gostar 💛\n"
    "{zap}"
    "⭐ Toda sexta à noite: <b>TOP 5 da semana</b>"
)


def boas_vindas() -> str:
    zap = (config.carregar().get("link_whatsapp") or "").strip()
    return BOAS_VINDAS.replace("{zap}", f"💬 Também no WhatsApp: {zap}\n\n" if zap else "\n")


def top_da_semana(posts: list[dict]) -> str:
    """Resumo semanal com os melhores posts (maior desconto) — formato que o povo salva e compartilha."""
    linhas = ["⭐ <b>TOP 5 DA SEMANA</b> ⭐", "Os achados que mais valeram a pena nos últimos dias:", ""]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    for e, p in zip(emojis, posts):
        nome = html.escape(p["nome"][:70])
        linhas.append(f"{e} {nome} — <b>{dinheiro(p['preco'])}</b>")
        linhas.append(f"🛒 <a href=\"{html.escape(p['link'])}\">Ver oferta</a>")
        linhas.append("")
    linhas += ["⚠️ Preço pode ter mudado, confere no link.", "📤 Encaminha pra alguém que vai gostar!", "", "#anúncio"]
    return "\n".join(linhas)


def zap_para_admin(o: dict, textos: dict):
    """WhatsApp no modo assistido: manda no seu privado do Telegram o post já no formato do Zap,
    com um botão que abre o WhatsApp com o texto pronto — você só escolhe o canal/grupo e envia.
    (Postar sozinho no WhatsApp exige programa não oficial e pode banir o seu número.)"""
    from urllib.parse import quote
    token = config.cred("TELEGRAM_TOKEN")
    admin = config.cred("TELEGRAM_ADMIN") or str(config.estado().get("telegram_admin") or "")
    if not (token and admin):
        return
    texto = montar(o, "pt", textos, "zap")
    teclado = json.dumps({"inline_keyboard": [[{"text": "📲 POSTAR NO WHATSAPP",
                                                 "url": "https://wa.me/?text=" + quote(texto)}]]})
    base = f"https://api.telegram.org/bot{token}"
    dados = {"chat_id": admin, "caption": ("📲 Pronto pro Zap:\n\n" + texto)[:1024], "reply_markup": teclado}
    r = None
    if (o.get("imagem") or "").startswith("http"):
        r = requests.post(f"{base}/sendPhoto", data={**dados, "photo": o["imagem"]}, timeout=30)
    if not (r is not None and r.ok):
        requests.post(f"{base}/sendMessage", data={"chat_id": admin, "text": "📲 Pronto pro Zap:\n\n" + texto,
                                                   "reply_markup": teclado, "disable_web_page_preview": "true"},
                      timeout=30)


def avisar_admin(texto: str):
    token = config.cred("TELEGRAM_TOKEN")
    admin = config.cred("TELEGRAM_ADMIN") or str(config.estado().get("telegram_admin") or "")
    if token and admin:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": admin, "text": texto}, timeout=30)


def testar(c: dict) -> str:
    """Manda um post de teste num canal (botão 'Testar' do painel)."""
    o = {"id": "teste", "plataforma": "aliexpress", "nome": "Produto de teste do painel ✅", "preco": 19.9,
         "moeda": "USD" if c.get("idioma") != "pt" else "BRL", "desconto": 40, "nota": 4.8, "vendas": 1234,
         "imagem": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/Example.jpg/320px-Example.jpg",
         "link": "https://example.com", "pais": "BR"}
    ENVIAR[c["tipo"]](c, o, {})
    return "Post de teste enviado"
