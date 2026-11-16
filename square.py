"""Bot de achadinhos rodando sozinho na hospedagem (Square Cloud), sem o painel.

Mesmo bot do launcher: posta no Telegram no horário/ritmo do config.json.
Controle pelo Telegram: mande /ajuda no privado do seu bot.

Este arquivo nunca deixa o processo morrer calado: qualquer erro vai pro console
e pro arquivo log_bot.txt (dá pra abrir no gerenciador de arquivos da Square).
"""
import os
import sys
import threading
import time
import traceback
from pathlib import Path

LOG = Path(__file__).parent / "log_bot.txt"

try:  # console da hospedagem mostra tudo na hora (e com acento)
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass


def anotar(msg: str):
    linha = f"[{time.strftime('%d/%m %H:%M:%S')}] {msg}"
    print(linha, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
        if LOG.stat().st_size > 2_000_000:  # não deixa o arquivo crescer pra sempre
            LOG.write_text(LOG.read_text(encoding="utf-8")[-500_000:], encoding="utf-8")
    except Exception:
        pass


os.environ["TZ"] = "America/Sao_Paulo"  # o servidor fica em outro fuso: horários do bot são os do Brasil
try:
    time.tzset()
except AttributeError:
    pass

anotar(f"Achadinhos: iniciando (Python {sys.version.split()[0]}, pasta {Path(__file__).parent})")


def main():
    import comandos
    import config
    from bot import Bot
    from db import DB

    db = DB()
    _log = db.log

    def log_tudo(msg, nivel="info"):  # tudo que o bot registra também vai pro log_bot.txt
        _log(msg, nivel)
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%d/%m %H:%M:%S')}] {msg}\n")
        except Exception:
            pass
    db.log = log_tudo
    bot = Bot(db)
    bot._pausado = False
    # o feed da Shopee (190 MB) só é baixado 10 min depois de ligar — evita pico de CPU logo na partida
    bot.feed_ultima = time.time() - 20 * 3600 + 600
    db.log(f"☁️ Bot iniciado na hospedagem — {time.strftime('%d/%m %H:%M')} (horário de Brasília)")
    chaves = ("TELEGRAM_TOKEN", "TELEGRAM_CANAL", "SHOPEE_AFFILIATE_ID", "SHOPEE_FEED_URL", "ML_AFILIADO_PARAMS", "ANTHROPIC_API_KEY")
    anotar("Chaves: " + "  ".join(f"{k}={'ok' if config.cred(k) else 'FALTA'}" for k in chaves))
    faltando = [k for k in ("TELEGRAM_TOKEN", "TELEGRAM_CANAL") if not config.cred(k)]
    if faltando:
        anotar(f"FALTAM CHAVES: {', '.join(faltando)} (confira o .env / segredos.json)")
    bot.ligar()
    threading.Thread(target=comandos.rodar, args=(bot, db), daemon=True).start()
    ultimo_sinal = 0
    while True:  # se a thread do bot cair por algum motivo, sobe de novo
        time.sleep(60)
        if not bot.rodando and not bot._pausado:
            anotar("Bot tinha parado — religando")
            bot.ligar()
        if time.time() - ultimo_sinal > 1800 or ultimo_sinal == 0:  # sinal de vida a cada 30 min no console
            ultimo_sinal = time.time()
            prox = time.strftime("%H:%M", time.localtime(bot.proximo_post_ts)) if bot.proximo_post_ts else "—"
            anotar(f"✅ vivo — posts hoje: {db.posts_hoje()} — próximo post ~{prox} — feed: {bot.feed.total()} ofertas — fila: {bot.ofertas.na_fila()}")
            if not comandos._admin() and config.cred("TELEGRAM_TOKEN"):
                anotar(f"🔑 Falta conectar o dono: mande no privado do seu bot  /codigo {comandos.codigo_fixo()}")


if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception:
            anotar("ERRO:\n" + traceback.format_exc())
            time.sleep(30)  # espera e tenta de novo, em vez de a hospedagem desligar
