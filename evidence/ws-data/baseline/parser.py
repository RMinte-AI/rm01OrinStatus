"""
Tegrastats parser module.
"""

import subprocess
import threading
import time
import logging
from typing import Dict, Any, Optional, List
import json
import re


logger = logging.getLogger(__name__)


class TegrastatsParser:
    """Parser for tegrastats output."""
    
    def __init__(self, interval: int = 1000):
        """
        Initialize parser.
        
        Args:
            interval: Sampling interval in milliseconds
        """
        self.interval = interval
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._current_data: Dict[str, Any] = {}
        self._lock = threading.Lock()
        
    def start(self) -> None:
        """Start tegrastats process and parsing thread."""
        if self._running:
            logger.warning("Parser already running")
            return
            
        try:
            # Start tegrastats process
            cmd = ["tegrastats", "--interval", str(self.interval)]
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            self._running = True
            self._thread = threading.Thread(target=self._parse_output, daemon=True)
            self._thread.start()
            
            logger.info(f"tegrastats进程已启动，间隔: {self.interval}ms")
            
        except Exception as e:
            logger.error(f"启动tegrastats失败: {e}")
            raise
    
    def stop(self) -> None:
        """Stop tegrastats process and parsing thread."""
        if not self._running:
            return
            
        self._running = False
        
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            except Exception as e:
                logger.error(f"停止tegrastats进程时出错: {e}")
            finally:
                self._process = None
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
            
        logger.info("tegrastats进程已停止")
    
    def get_current_status(self) -> Dict[str, Any]:
        """Get current parsed data."""
        with self._lock:
            return self._current_data.copy() if self._current_data else {}
    
    def _parse_output(self) -> None:
        """Parse tegrastats output in background thread."""
        if not self._process or not self._process.stdout:
            return
            
        try:
            for line in iter(self._process.stdout.readline, ''):
                if not self._running:
                    break
                    
                line = line.strip()
                if line:
                    try:
                        parsed_data = self.parse_line(line)
                        if parsed_data:
                            with self._lock:
                                self._current_data = parsed_data
                    except Exception as e:
                        logger.error(f"解析tegrastats行时出错: {e}")
                        
        except Exception as e:
            logger.error(f"读取tegrastats输出时出错: {e}")
        finally:
            self._running = False
    
    @staticmethod
    def parse_line(line: str) -> Dict[str, Any]:
        """
        Parse a single line of tegrastats output.
        
        Args:
            line: Raw tegrastats output line
            
        Returns:
            Parsed data dictionary
        """
        try:
            result = {
                "timestamp": time.time(),
                "cpu": {"cores": []},
                "memory": {"ram": {}, "swap": {}},
                "temperature": {},
                "power": {},
                "gpu": {}
            }
            
            # Parse CPU information
            cpu_match = re.search(r'CPU \[(.*?)\]', line)
            if cpu_match:
                cpu_data = cpu_match.group(1)
                cores = []
                
                # Parse individual CPU cores
                core_pattern = r'(\d+)%@(\d+)'
                core_matches = re.findall(core_pattern, cpu_data)
                
                for i, (usage, freq) in enumerate(core_matches):
                    cores.append({
                        "id": i,
                        "usage": int(usage),
                        "freq": int(freq)
                    })
                
                result["cpu"]["cores"] = cores
            
            # Parse memory information
            ram_match = re.search(r'RAM (\d+)/(\d+)MB', line)
            if ram_match:
                used, total = ram_match.groups()
                result["memory"]["ram"] = {
                    "used": int(used),
                    "total": int(total),
                    "unit": "MB"
                }
            
            # Parse swap information
            swap_match = re.search(r'SWAP (\d+)/(\d+)MB \(cached (\d+)MB\)', line)
            if swap_match:
                used, total, cached = swap_match.groups()
                result["memory"]["swap"] = {
                    "used": int(used),
                    "total": int(total),
                    "cached": int(cached),
                    "unit": "MB"
                }
            
            # Parse temperature information
            temp_matches = re.findall(r'(\w+)@([\d.]+)C', line)
            for sensor, temp in temp_matches:
                result["temperature"][sensor.lower()] = float(temp)
            
            # Parse power information
            power_matches = re.findall(r'(\w+) (\d+)/(\d+)', line)
            for component, current, average in power_matches:
                result["power"][component.lower()] = {
                    "current": int(current),
                    "average": int(average),
                    "unit": "mW"
                }
            
            # Parse GPU information
            gpu_match = re.search(r'GR3D_FREQ (\d+)%', line)
            if gpu_match:
                result["gpu"]["gr3d_freq"] = int(gpu_match.group(1))
            
            return result
            
        except Exception as e:
            logger.error(f"解析tegrastats行失败: {e}")
            return {}
    
    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()