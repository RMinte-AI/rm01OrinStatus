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
import copy
import uuid
from datetime import datetime, timezone
from .config import freshness_timeout_ms
from .platform_gpu import identify_platform, GPUSampler, gpu_packet, percent, THOR_GPU_INTERVAL_MS


_diagnostic_lock = threading.Lock()
_diagnostic_window = 0.0
_diagnostic_count = 0


def _power_diagnostic():
    """At most five warnings per minute; never log raw device input."""
    global _diagnostic_window, _diagnostic_count
    with _diagnostic_lock:
        now = time.monotonic()
        if now - _diagnostic_window >= 60:
            _diagnostic_window, _diagnostic_count = now, 0
        if _diagnostic_count < 5:
            logger.warning("Invalid or conflicting power rail omitted")
            _diagnostic_count += 1


logger = logging.getLogger(__name__)


class TegrastatsParser:
    """Parser for tegrastats output."""
    
    def __init__(self, interval: int = 1000, platform_reader=None, gpu_factory=None):
        """
        Initialize parser.
        
        Args:
            interval: Sampling interval in milliseconds
        """
        if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
            raise ValueError("tegrastats interval must be a positive integer in ms")
        self._platform_reader = platform_reader or identify_platform
        self._gpu_factory = gpu_factory or GPUSampler
        self._platform = copy.deepcopy(self._platform_reader())
        self._gpu_sampler = None
        self._pending_gpu_sampler = None
        self._gpu_error = None
        self.interval = interval
        self._source_interval_ms = interval
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._unclaimed_output = None
        self._running = False
        self._current_data: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._stream_id = str(uuid.uuid4())
        self._sample_seq = 0
        self._received_at = None
        self._received_monotonic = None
        self._collector_state = "stopped"
        self._freshness_timeout_ms = freshness_timeout_ms(interval)

    def _close_unclaimed_output(self):
        if self._unclaimed_output is not None:
            self._unclaimed_output.stdout.close()
            self._unclaimed_output = None

    def _cleanup_process(self, process):
        with self._cleanup_lock:
            if process.poll() is not None:
                return
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def start(self) -> None:
        """Start a new collection lifecycle, never relabel an old sample."""
        with self._lifecycle_lock:
            if self._running:
                return
            interval = self.interval
            if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
                raise ValueError('tegrastats interval must be a positive integer in ms')
            if self._process is not None:
                self._cleanup_process(self._process)
            self._close_unclaimed_output()
            old_gpu = self._pending_gpu_sampler or self._gpu_sampler
            gpu_error = None
            self._pending_gpu_sampler = None
            if old_gpu is not None:
                try:
                    old_gpu.stop()
                except Exception as error:
                    self._pending_gpu_sampler = old_gpu
                    gpu_error = old_gpu.diagnostics()['state']
                    logger.warning('Prior GPU cleanup incomplete: %s', type(error).__name__)
            self._platform = copy.deepcopy(self._platform_reader())
            self._gpu_sampler = None
            self._gpu_error = gpu_error
            with self._lock:
                self._source_interval_ms = interval
                self._freshness_timeout_ms = freshness_timeout_ms(interval)
                self._stream_id = str(uuid.uuid4())
                stream_id = self._stream_id
                self._sample_seq = 0
                self._received_at = self._received_monotonic = None
                self._current_data = {}
                self._collector_state = "starting"
            process = None
            ownership = {"lock": threading.Lock(), "claimed": False, "cancelled": False}
            try:
                process = subprocess.Popen(
                    ["tegrastats", "--interval", str(interval)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1, universal_newlines=True)
                self._process = process
                if self._platform['family'] == 'jetson_thor' and self._pending_gpu_sampler is None:
                    try:
                        sampler = self._gpu_factory(THOR_GPU_INTERVAL_MS)
                        self._gpu_sampler = sampler
                        sampler.start()
                    except Exception as error:
                        # An optional GPU task cannot fail the tegrastats startup.
                        self._gpu_error = type(error).__name__
                        if self._gpu_sampler is not None:
                            try:
                                self._gpu_sampler.stop()
                            except Exception:
                                self._pending_gpu_sampler = self._gpu_sampler
                                logger.exception('Failed GPU startup; cleanup remains owned')
                            self._gpu_sampler = None
                        logger.warning('GPU startup unavailable: %s', type(error).__name__)
                with self._lock:
                    self._running = True
                    self._collector_state = "running"
                self._thread = threading.Thread(
                    target=self._reader_entry, args=(process, stream_id, ownership), daemon=True)
                self._thread.start()
            except Exception:
                with self._lock:
                    self._running = False
                    self._collector_state = "error"
                with ownership["lock"]:
                    startup_owns_pipe = not ownership["claimed"]
                    ownership["cancelled"] = True
                if process is not None:
                    try:
                        self._cleanup_process(process)
                    except Exception:
                        logger.exception("Startup failed; process cleanup also failed")
                    if startup_owns_pipe:
                        self._unclaimed_output = process
                        try:
                            self._close_unclaimed_output()
                        except Exception:
                            logger.exception("Startup failed; unclaimed output pipe close failed")
                        self._thread = None
                    try:
                        if process.poll() is not None:
                            self._process = None
                    except Exception:
                        logger.exception("Startup failed; cleanup status unavailable")
                if self._gpu_sampler is not None:
                    try:
                        self._gpu_sampler.stop()
                    except Exception:
                        logger.exception('Source startup failed; GPU cleanup also failed')
                raise

    def _reader_entry(self, process, stream_id, ownership):
        # Failure may cancel a delayed reader before it takes ownership.
        with ownership["lock"]:
            if ownership["cancelled"]:
                return
            ownership["claimed"] = True
        self._parse_output(process, stream_id)

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                self._running = False
                if self._collector_state != "error":
                    self._collector_state = "stopped"
            errors = []
            gpu = self._gpu_sampler or self._pending_gpu_sampler
            if gpu is not None:
                gpu.request_stop()
            if self._process is not None:
                try:
                    self._cleanup_process(self._process)
                except Exception as error:
                    errors.append(error)
                    logger.exception('Source process cleanup failed')
            try:
                self._close_unclaimed_output()
            except Exception as error:
                errors.append(error)
                logger.exception('Unclaimed output cleanup failed')
            if self._thread and self._thread.is_alive():
                try:
                    self._thread.join(timeout=2)
                except Exception as error:
                    errors.append(error)
                    logger.exception('Source reader join failed')
            if self._process is not None:
                try:
                    if self._process.poll() is not None:
                        self._process = None
                except Exception as error:
                    errors.append(error)
                    logger.exception('Source cleanup status unavailable')
            if gpu is not None:
                try:
                    gpu.stop()
                except Exception as error:
                    errors.append(error)
                    logger.exception('GPU cleanup incomplete')
            if errors:
                with self._lock:
                    self._collector_state = 'error'
                raise errors[0]

    def capability_diagnostics(self):
        with self._lock:
            platform = copy.deepcopy(self._platform)
            sampler = self._gpu_sampler
            pending = self._pending_gpu_sampler
            error = self._gpu_error
            source_interval = self._source_interval_ms
        interval = (THOR_GPU_INTERVAL_MS if platform['gpu_backend'] == 'nvml'
                    else source_interval if platform['gpu_backend'] == 'tegrastats' else None)
        return {"platform": platform, "source_sample_interval_ms": source_interval,
                "gpu_sample_interval_ms": interval,
                "gpu_reader": sampler.diagnostics() if sampler else
                {"state": "error" if error else "not_selected", "error_class": error, "running": False},
                "previous_gpu_cleanup": pending.diagnostics() if pending else None}

    def get_snapshot(self):
        """Copy data and its identity together; network work stays outside the lock."""
        with self._lock:
            age = None
            if self._received_monotonic is not None:
                elapsed = time.monotonic() - self._received_monotonic
                if elapsed >= 0:
                    age = elapsed * 1000
            meta = {
                "schema_version": 1, "stream_id": self._stream_id,
                "sample_seq": self._sample_seq, "received_at": self._received_at,
                "sample_age_ms": age,
                "fresh": (self._collector_state == "running" and age is not None
                          and age < self._freshness_timeout_ms),
                "collector_state": self._collector_state,
                "freshness_timeout_ms": self._freshness_timeout_ms,
                "time_basis": "collector_receive",
                "source_sample_interval_ms": self._source_interval_ms,
            }
            return copy.deepcopy(self._current_data), meta

    def get_current_status(self) -> Dict[str, Any]:
        """Preserve the public parser contract, including an empty no-data result."""
        return self.get_snapshot()[0]

    def _accept_line(self, line, stream_id, received_at, received_monotonic):
        # Select the causal copy before potentially slow parsing, and capture
        # this generation's owner rather than looking up a new one after restart.
        with self._lock:
            if stream_id != self._stream_id or not self._running:
                return False
            platform = copy.deepcopy(self._platform)
            sampler = self._gpu_sampler
            gpu_error = self._gpu_error
            interval = self._source_interval_ms
        gpu = gpu_packet()
        if platform['family'] == 'jetson_thor':
            if sampler is not None:
                try:
                    gpu = sampler.snapshot_at(received_monotonic)
                except Exception as error:
                    logger.warning('GPU snapshot failed: %s', type(error).__name__)
                    gpu = gpu_packet('nvml', 'error', interval=THOR_GPU_INTERVAL_MS)
            else:
                gpu = gpu_packet('nvml' if gpu_error else 'unavailable',
                                 'error' if gpu_error else 'unavailable', interval=THOR_GPU_INTERVAL_MS)
        parsed = self.parse_line(line.strip())
        if not parsed:
            return False
        if platform['family'] == 'jetson_orin':
            value = parsed['gpu'].get('gr3d_freq')
            if percent(value):
                gpu = gpu_packet('tegrastats', 'valid', value, received_at, 0, interval)
            elif 'gr3d_freq' in parsed['gpu']:
                parsed['gpu'].pop('gr3d_freq')
                gpu = gpu_packet('tegrastats', 'error', interval=interval)
            else:
                gpu = gpu_packet(interval=interval)
        parsed['platform'] = platform
        parsed['gpu'].update(gpu)
        with self._lock:
            if stream_id != self._stream_id or not self._running:
                return False
            self._sample_seq += 1
            self._current_data = parsed
            self._received_at = received_at
            self._received_monotonic = received_monotonic
        return True

    def _parse_output(self, process, stream_id) -> None:
        failed = False
        try:
            for line in iter(process.stdout.readline, ''):
                # Timestamp the completed read, before parsing or lock contention.
                received_monotonic = time.monotonic()
                received_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                with self._lock:
                    if stream_id != self._stream_id or not self._running:
                        break
                self._accept_line(line, stream_id, received_at, received_monotonic)
        except Exception:
            failed = True
            logger.exception("Tegrastats reader exited unexpectedly")
        finally:
            with self._lock:
                if stream_id == self._stream_id and self._running:
                    self._running = False
                    self._collector_state = "error" if failed or process.poll() not in (0, None) else "stopped"
            with self._lock:
                sampler = self._gpu_sampler if stream_id == self._stream_id else None
            if sampler is not None:
                sampler.request_stop()
            try:
                self._cleanup_process(process)
            except Exception:
                with self._lock:
                    if stream_id == self._stream_id:
                        self._collector_state = 'error'
                logger.exception('Source reader process cleanup failed')
            finally:
                if process.stdout:
                    process.stdout.close()

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
            # Recognize telemetry at the beginning (optional tegrastats date prefix).
            frame = re.sub(r'^\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}\s+', '', line.strip())
            if not re.match(r'^(?:RAM |SWAP |CPU \[|GR3D_FREQ |[A-Za-z0-9_]+@|VDD[A-Z0-9_]+ |VIN_[A-Z0-9_]+ )', frame):
                return {}
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
            temp_matches = re.findall(r'(?:^|\s)(\w+)@(-?\d+(?:\.\d+)?)C(?=\s|$)', line)
            for sensor, temp in temp_matches:
                result["temperature"][sensor.lower()] = float(temp)
            
            # Parse power information
            rails = {}
            invalid = set()
            for match in re.finditer(r'(?:^|\s)((?:VDD[A-Z0-9_]+|VIN_[A-Z0-9_]+))(?=\s|$)', line):
                component = match.group(1).lower()
                remainder = line[match.end():].split()
                token = remainder[0] if remainder else ""
                values = re.fullmatch(r'(\d+)(mW)?/(\d+)(mW)?', token)
                if not values or values.group(2) != values.group(4):
                    invalid.add(component)
                    continue
                value = {"current": int(values.group(1)),
                         "average": int(values.group(3)), "unit": "mW"}
                if component in rails and rails[component] != value:
                    invalid.add(component)
                rails[component] = value
            result["power"] = {key: value for key, value in rails.items() if key not in invalid}
            if invalid:
                _power_diagnostic()

            # Parse GPU information
            gpu_match = re.search(r'GR3D_FREQ (\d+)%', line)
            if gpu_match:
                result["gpu"]["gr3d_freq"] = int(gpu_match.group(1))
            
            if not (result["cpu"]["cores"] or result["memory"]["ram"]
                    or result["memory"]["swap"] or result["temperature"]
                    or result["power"] or result["gpu"]):
                return {}
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
