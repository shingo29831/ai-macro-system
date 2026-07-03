# engines/manager.py
# @role: Manages the lifecycle of local AI API servers as independent background processes.

import subprocess
import os
import atexit
import json
from typing import List

class LocalServerManager:
    def __init__(self) -> None:
        self._processes: List[subprocess.Popen] = []

    def start_servers(self) -> None:
        # Load custom connection settings to determine if local servers are needed.
        config_path = 'config.json'
        ai_mode = 'local'
        llm_host = '127.0.0.1'
        llm_port = '8844'
        cv_host = '127.0.0.1'
        cv_port = '8843'

        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    ai_mode = config.get('ai_mode', 'local')
                    llm_host = config.get('llm_host', '127.0.0.1')
                    # Fallback to default port if explicitly empty
                    llm_port = config.get('llm_port', '8844') or '8844'
                    cv_host = config.get('cv_host', '127.0.0.1')
                    cv_port = config.get('cv_port', '8843') or '8843'
            except Exception:
                pass

        env = os.environ.copy()

        # Start LLM server if local mode is selected and host points to local machine
        if ai_mode == 'local' and llm_host in ['127.0.0.1', 'localhost']:
            llm_cmd = ['python', '-m', 'uvicorn', 'engines.llm.server:app', '--port', llm_port]
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            llm_proc = subprocess.Popen(llm_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            self._processes.append(llm_proc)

        # Start CV server independently if host points to local machine
        if cv_host in ['127.0.0.1', 'localhost']:
            yolo_cmd = ['python', '-m', 'uvicorn', 'engines.yolo.server:app', '--port', cv_port]
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            yolo_proc = subprocess.Popen(yolo_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            self._processes.append(yolo_proc)

        atexit.register(self.stop_servers)

    def stop_servers(self) -> None:
        """ Ensures all background server processes are strictly and completely terminated. """
        for p in self._processes:
            if p.poll() is None:
                if os.name == 'nt':
                    # Windows requires taskkill with tree flag (/T) to kill child processes launched by uvicorn
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW
                        )
                    except Exception:
                        p.kill()
                else:
                    p.terminate()
                    try:
                        p.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        p.kill()
        self._processes.clear()