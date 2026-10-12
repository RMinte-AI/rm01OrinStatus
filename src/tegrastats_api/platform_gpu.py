"""Evidenced platform identity and a lifecycle-owned, causal GPU sampler.

NVML is imported only by the explicitly started Thor reader. HTTP and broadcasts
never query a device. Python 3.8-compatible; no CUDA or device configuration.
"""
import copy
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from .config import freshness_timeout_ms


THOR_GPU_INTERVAL_MS = 1000


def identify_platform(path='/proc/device-tree/compatible'):
    try:
        tokens = [x.decode('utf-8') for x in Path(path).read_bytes().split(b'\0') if x]
    except (OSError, UnicodeError):
        tokens = []
    matches = [x for x in ('nvidia,tegra234', 'nvidia,tegra264') if x in tokens]
    soc = matches[0].split(',')[1] if len(matches) == 1 else None
    family = {'tegra234': 'jetson_orin', 'tegra264': 'jetson_thor'}.get(soc, 'unknown')
    return {'schema_version': 1, 'family': family, 'soc': soc,
            'identification_source': 'device_tree_compatible' if soc else None,
            'compatible': tokens, 'gpu_backend': {'jetson_orin': 'tegrastats', 'jetson_thor': 'nvml'}.get(family, 'unavailable')}


def percent(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100


def gpu_packet(source='unavailable', status='unavailable', value=None, sampled_at=None,
               age=None, interval=None, device_uuid=None):
    return {'schema_version': 1, 'utilization_pct': value, 'source': source, 'status': status,
            'sampled_at_utc': sampled_at, 'age_at_telemetry_receive_ms': age,
            'sample_interval_ms': interval,
            'freshness_timeout_ms': max(5000, 3 * interval) if interval else None,
            'device_uuid': device_uuid}


class UnsupportedGPU(Exception):
    pass


class NVMLReader:
    def __init__(self):
        self.api = None
        self.initialized = False
        self.device_uuid = None

    def open(self):
        import pynvml
        self.api = pynvml
        pynvml.nvmlInit()
        self.initialized = True
        if pynvml.nvmlDeviceGetCount() != 1:
            raise UnsupportedGPU('single GPU required')
        self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        # An unsupported MIG query on integrated hardware is not a read failure.
        if hasattr(pynvml, 'nvmlDeviceGetMigMode'):
            try:
                if pynvml.nvmlDeviceGetMigMode(self.handle)[0]:
                    raise UnsupportedGPU('MIG utilization unsupported')
            except pynvml.NVMLError_NotSupported:
                pass
        value = pynvml.nvmlDeviceGetUUID(self.handle)
        self.device_uuid = value.decode('utf-8') if isinstance(value, bytes) else value
        if not isinstance(self.device_uuid, str) or not self.device_uuid:
            raise UnsupportedGPU('device identity unavailable')

    def read(self):
        try:
            return self.api.nvmlDeviceGetUtilizationRates(self.handle).gpu
        except self.api.NVMLError_NotSupported as error:
            raise UnsupportedGPU('utilization unsupported') from error

    def close(self):
        if self.initialized:
            self.api.nvmlShutdown()
            self.initialized = False


class GPUSampler:
    JOIN_TIMEOUT_SECONDS = 2

    def __init__(self, interval_ms, reader=None, monotonic=time.monotonic, utc=None):
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms <= 0:
            raise ValueError('GPU interval must be positive integer milliseconds')
        self.interval = interval_ms
        self.reader = reader if reader is not None else NVMLReader()
        self.clock = monotonic
        self.utc = utc or (lambda: datetime.now(timezone.utc).isoformat())
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        # One freshness window plus a predecessor, including failed outcomes.
        # Exhausted history is unavailable, never a future result or fake zero age.
        capacity = (freshness_timeout_ms(interval_ms) + interval_ms - 1) // interval_ms + 1
        self.history = deque(maxlen=capacity)
        self.thread = None
        self._stop_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._needs_close = False
        self._release_error = None
        self.stats = {'reads': 0, 'errors': 0, 'missed': 0, 'read_ms_max': 0.0,
                      'state': 'not_started', 'error_class': None, 'sample_interval_ms': interval_ms,
                      'period_ms_last': None, 'period_ms_max': None, 'period_count': 0}

    def start(self):
        if self.thread is not None:
            raise RuntimeError('GPU sampler lifecycle cannot restart')
        self.thread = threading.Thread(target=self._run, daemon=True, name='tegrastats-gpu-reader')
        self.thread.start()

    def _save(self, value, status, completed, stamp, error=None):
        with self.lock:
            if self.stop_event.is_set():
                return
            self.history.append({'t': completed, 'utc': stamp, 'value': value, 'status': status,
                                 'uuid': getattr(self.reader, 'device_uuid', None)})
            self.stats['state'] = status
            self.stats['error_class'] = error

    def _run(self):
        try:
            # open may partially acquire resources before raising. NVMLReader
            # itself pairs shutdown only with a successful nvmlInit.
            self._needs_close = True
            try:
                self.reader.open()
            except (ImportError, UnsupportedGPU) as error:
                self._save(None, 'unavailable', self.clock(), None, type(error).__name__)
                return
            except Exception as error:
                self._save(None, 'error', self.clock(), None, type(error).__name__)
                return
            due = self.clock()
            previous_started = None
            while not self.stop_event.is_set():
                started = self.clock()
                if previous_started is not None:
                    actual = (started-previous_started)*1000
                    with self.lock:
                        self.stats['period_ms_last'] = actual
                        self.stats['period_ms_max'] = max(self.stats['period_ms_max'] or 0, actual)
                        self.stats['period_count'] += 1
                previous_started = started
                try:
                    value = self.reader.read()
                    if not percent(value):
                        raise ValueError('GPU percentage unavailable')
                    completed = self.clock()
                    self._save(value, 'valid', completed, self.utc())
                except UnsupportedGPU as error:
                    completed = self.clock()
                    self._save(None, 'unavailable', completed, None, type(error).__name__)
                except Exception as error:
                    completed = self.clock()
                    self._save(None, 'error', completed, None, type(error).__name__)
                    with self.lock:
                        self.stats['errors'] += 1
                with self.lock:
                    self.stats['reads'] += 1
                    self.stats['read_ms_max'] = max(self.stats['read_ms_max'], (completed - started) * 1000)
                interval = self.interval / 1000.0
                missed = max(0, int((completed - due) // interval))
                with self.lock:
                    self.stats['missed'] += missed
                due += (missed+1)*interval
                self.stop_event.wait(max(0.0, due - self.clock()))
        finally:
            # Close inside the reader thread, after its read has returned. A
            # failed partially initialized NVML session also needs release.
            self._close_reader()

    def _close_reader(self):
        # A retry is allowed only through this original owner after the worker
        # exits. A successful release clears ownership; failures retain it.
        with self._cleanup_lock:
            if not self._needs_close:
                return
            try:
                self.reader.close()
            except Exception as error:
                with self.lock:
                    self._release_error = error
                    self.stats['error_class'] = type(error).__name__
                    self.stats['state'] = 'cleanup_error'
            else:
                with self.lock:
                    self._needs_close = False
                    self._release_error = None
                    self.stats['state'] = 'stopped'

    def snapshot_at(self, received_t):
        with self.lock:
            rows = [copy.deepcopy(x) for x in self.history if x['t'] <= received_t]
        if not rows:
            return gpu_packet(interval=self.interval)
        row = rows[-1]
        if row['status'] != 'valid':
            source = 'unavailable' if row['status'] == 'unavailable' else 'nvml'
            return gpu_packet(source, row['status'], interval=self.interval, device_uuid=row['uuid'])
        age = (received_t - row['t']) * 1000
        stale = age >= max(5000, 3 * self.interval)
        return gpu_packet('nvml', 'stale' if stale else 'valid', None if stale else row['value'],
                          row['utc'], age, self.interval, row['uuid'])

    def request_stop(self):
        self.stop_event.set()

    def stop(self):
        # Serialize control requests, without holding any lock needed by the
        # worker. A release retry belongs to this owner and never opens/reads GPU.
        with self._stop_lock:
            self.request_stop()
            if self._release_error is not None and self.thread and not self.thread.is_alive():
                self.thread = threading.Thread(target=self._close_reader, daemon=True,
                                               name='tegrastats-gpu-cleanup')
                self.thread.start()
            if self.thread and self.thread.ident is not None:
                self.thread.join(timeout=self.JOIN_TIMEOUT_SECONDS)
                if self.thread.is_alive():
                    with self.lock:
                        self.stats['state'] = 'cleanup_pending'
                    raise RuntimeError('GPU reader did not exit; cleanup_pending')
            if self._release_error is not None:
                raise RuntimeError('GPU resource release failed; cleanup_error') from self._release_error

    def diagnostics(self):
        with self.lock:
            return dict(self.stats, running=bool(self.thread and self.thread.is_alive()),
                        cleanup_required=self._needs_close, history_capacity=self.history.maxlen)
