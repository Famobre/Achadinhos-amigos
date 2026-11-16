"""Controle do bot pelo Telegram (pra quando ele está hospedado, sem o painel).

Você conversa com o seu bot no privado:
  • manda um link de produto  → entra na fila de ofertas
  • /fila    → mostra o que está na fila
  • /status  → posts de hoje e próximo post
  • /postar  → posta agora
  • /pausar e /ligar
Só o dono responde: na 1ª vez, mande  /codigo 123456  com o código que aparece no console da hospedagem.
"""
import html
import random
import re
import time
from datetime import datetime

import requests

import config
import extrator

AJUDA = (
    "🤖 <b>Comandos do bot de achadinhos</b>\n\n"
    "🔗 <b>Mande um link</b> de produto (Mercado Livre, Shopee, Amazon...) e ele entra na fila.\n"
    "Se eu não conseguir ler a página, mande assim (uma coisa por linha):\n"
    "<code>link\nnome do produto\npreço\npreço antigo (opcional)\ncupom (opcional)</code>\n\n"
    "/fila — ofertas esperando\n/status — como está o bot\n/postar — posta a próxima agora\n"
    "/pausar — para de postar\n/ligar — volta a postar\n/apagar 12 — tira a oferta 12 da fila"
)


def codigo_fixo() -> str:
    """Código de pareamento estável (não muda a cada reinício), derivado do token do bot."""
    import hashlib
    h = hashlib.sha256(("achadinhos:" + config.cred("TELEGRAM_TOKEN")).encode()).hexdigest()
    return str(int(h, 16))[-6:]


def _admin() -> str:
    return config.cred("TELEGRAM_ADMIN") or str(config.estado().get("telegram_admin") or "")


def _rs(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _num(s: str) -> float:
    s = re.sub(r"[^\d,.]", "", s or "")
    if "," in s and s.rfind(",") > s.rfind("."):
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


class Comandos:
    def __init__(self, bot, db):
        self.bot, self.db = bot, db
        self.token = config.cred("TELEGRAM_TOKEN")
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.offset = None
        self.codigo = None
        if not _admin():
            self.codigo = codigo_fixo()
            db.log(f"🔑 Pra controlar o bot pelo Telegram, mande no privado do seu bot:  /codigo {self.codigo}")

    def responder(self, chat, texto):
        try:
            requests.post(f"{self.base}/sendMessage", data={"chat_id": chat, "text": texto, "parse_mode": "HTML",
                                                           "disable_web_page_preview": "true"}, timeout=30)
        except requests.RequestException:
            pass

    # ------------------------------------------------------------------ loop
    def rodar(self):
        if not self.token:
            self.db.log("Comandos do Telegram desligados: falta o token", "aviso")
            return
        try:
            eu = requests.get(f"{self.base}/getMe", timeout=20).json()
            if eu.get("ok"):
                self.db.log(f"🤖 Comandos ligados no bot @{eu['result'].get('username')} — fale com ELE no privado")
            else:
                self.db.log(f"Token do Telegram recusado: {eu.get('description')}", "erro")
            # webhook ligado (ex.: outro construtor de bot) impede o bot de ler as mensagens
            requests.post(f"{self.base}/deleteWebhook", timeout=20)
        except Exception as e:
            self.db.log(f"Comandos: não consegui falar com o Telegram ({e})", "erro")
        ultimo_erro = ""
        while True:
            try:
                r = requests.get(f"{self.base}/getUpdates", params={"timeout": 50, "offset": self.offset,
                                                                    "allowed_updates": '["message"]'}, timeout=65)
                j = r.json()
                if not j.get("ok"):
                    desc = str(j.get("description") or j)
                    if desc != ultimo_erro:
                        ultimo_erro = desc
                        self.db.log(f"Comandos do Telegram: {desc}", "erro")
                    if "webhook" in desc.lower():
                        requests.post(f"{self.base}/deleteWebhook", timeout=20)
                        time.sleep(5)
                    elif j.get("error_code") == 409:  # outro programa lendo o mesmo bot (ex.: painel no PC)
                        time.sleep(60)
                    else:
                        time.sleep(10)
                    continue
                ultimo_erro = ""
                for up in j.get("result", []):
                    self.offset = up["update_id"] + 1
                    msg = up.get("message") or {}
                    if msg.get("chat", {}).get("type") == "private" and msg.get("text"):
                        try:
                            self.tratar(msg)
                        except Exception as e:
                            self.responder(msg["chat"]["id"], f"⚠️ Deu erro: {html.escape(str(e)[:200])}")
            except Exception:
                time.sleep(10)

    # ------------------------------------------------------------------ mensagens
    def tratar(self, msg):
        chat = msg["chat"]["id"]
        texto = msg["text"].strip()
        admin = _admin()
        if texto.startswith("/codigo"):
            if admin:
                return self.responder(chat, "Esse bot já tem dono ✅" if str(chat) != admin else "Você já é o dono ✅")
            if self.codigo and texto.split()[-1] == self.codigo:
                config.salvar_estado(telegram_admin=str(chat))
                self.codigo = None
                self.db.log("🔑 Dono do bot conectado pelo Telegram")
                return self.responder(chat, "✅ Pronto! Agora só você controla o bot.\n\n" + AJUDA)
            return self.responder(chat, "Código errado.")
        if str(chat) != admin:
            return  # ignora estranhos sem responder

        cmd = texto.split()[0].lower().split("@")[0]
        if cmd in ("/start", "/ajuda", "/help"):
            return self.responder(chat, AJUDA)
        if cmd == "/fila":
            fila = [o for o in self.bot.ofertas.listar() if o.get("status") == "fila"]
            if not fila:
                return self.responder(chat, "A fila está vazia. Mande um link pra adicionar 🙂")
            linhas = [f"📋 <b>{len(fila)} na fila</b>", ""]
            for o in fila[:20]:
                linhas.append(f"#{o['id']} {html.escape(o['nome'][:50])} — {_rs(o['preco'])}")
            return self.responder(chat, "\n".join(linhas))
        if cmd == "/apagar":
            n = re.findall(r"\d+", texto)
            if n:
                self.bot.ofertas.apagar(int(n[0]))
                return self.responder(chat, f"🗑️ Oferta #{n[0]} tirada da fila")
            return self.responder(chat, "Use assim: /apagar 12")
        if cmd == "/status":
            prox = self.bot.proximo_post_ts
            prox_txt = datetime.fromtimestamp(prox).strftime("%H:%M") if prox else "—"
            cfg = config.carregar()
            return self.responder(chat, (
                f"{'🟢 Rodando' if self.bot.rodando else '⏸️ Pausado'}\n"
                f"📤 Posts hoje: {self.db.posts_hoje()}\n⏰ Próximo post: ~{prox_txt}\n"
                f"🕗 Horário: {cfg['hora_inicio']}h às {cfg['hora_fim']}h, a cada {cfg['intervalo_min']} min"))
        if cmd == "/postar":
            o = self.bot.postar_agora()
            return self.responder(chat, f"✅ Postado: {html.escape(o['nome'][:60])}" if o else "Nada novo pra postar agora.")
        if cmd == "/pausar":
            self.bot._pausado = True
            self.bot.desligar()
            return self.responder(chat, "⏸️ Pausado. Mande /ligar pra voltar.")
        if cmd == "/ligar":
            self.bot._pausado = False
            self.bot.ligar()
            return self.responder(chat, "▶️ Ligado!")

        links = re.findall(r"https?://\S+", texto)
        if links:
            return self.adicionar(chat, texto, links[0])
        self.responder(chat, "Não entendi 😅\n\n" + AJUDA)

    def adicionar(self, chat, texto, link):
        linhas = [l.strip() for l in texto.splitlines() if l.strip() and link not in l]
        d = {}
        try:
            d = extrator.extrair(link)
        except Exception:
            pass
        d["link"] = link
        d.setdefault("plataforma", extrator.plataforma_de(link))
        # o que você escreveu tem prioridade: nome / preço / preço antigo / cupom
        if len(linhas) >= 1:
            d["nome"] = linhas[0]
        if len(linhas) >= 2 and _num(linhas[1]):
            d["preco"] = _num(linhas[1])
        if len(linhas) >= 3 and _num(linhas[2]):
            d["preco_antigo"] = _num(linhas[2])
        if len(linhas) >= 4:
            d["cupom"] = linhas[3]
        if not (d.get("nome") and d.get("preco")):
            return self.responder(chat, "Não consegui ler essa página 😕 Mande de novo assim:\n\n"
                                        f"<code>{html.escape(link)}\nNome do produto\n49,90\n89,90</code>\n\n"
                                        "(a última linha é o preço antigo, é opcional)")
        r = self.bot.ofertas.salvar(d)
        if not r.get("ok"):
            return self.responder(chat, f"⚠️ {html.escape(r.get('msg', 'não salvou'))}")
        antigo = f" (de {_rs(d['preco_antigo'])})" if d.get("preco_antigo") else ""
        self.responder(chat, f"✅ Na fila: <b>{html.escape(d['nome'][:70])}</b>\n{_rs(d['preco'])}{antigo}" +
                             ("" if d.get("imagem") else "\n⚠️ Sem foto — vai sair só texto"))


def rodar(bot, db):
    Comandos(bot, db).rodar()
