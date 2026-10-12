"""
Main server module for Tegrastats API.
"""

import logging
import threading
import time
import sys
from datetime import datetime
from typing import Dict, Any, Optional

from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS

from .config import Config
from .parser import TegrastatsParser
from .model_monitor import ModelMonitor


logger = logging.getLogger(__name__)


def _has_primary_failure(error):
    # A successful signal exit must not suppress a subsequent cleanup failure.
    return error is not None and not (
        isinstance(error, SystemExit) and error.code in (None, 0))


class ConnectionLimiter:
    """Connection limiter for WebSocket connections."""
    
    def __init__(self, max_connections: int = 10):
        self.max_connections = max_connections
        self.current_connections = 0
        self.lock = threading.Lock()
    
    def can_accept(self) -> bool:
        """Check if new connection can be accepted."""
        with self.lock:
            return self.current_connections < self.max_connections
    
    def add_connection(self) -> bool:
        """Add a new connection."""
        with self.lock:
            if self.current_connections < self.max_connections:
                self.current_connections += 1
                return True
            return False
    
    def remove_connection(self) -> None:
        """Remove a connection."""
        with self.lock:
            if self.current_connections > 0:
                self.current_connections -= 1
    
    def get_count(self) -> int:
        """Get current connection count."""
        with self.lock:
            return self.current_connections


class TegrastatsServer:
    """Main Tegrastats API server."""
    
    def __init__(self, config: Optional[Config] = None):
        """
        Initialize server.
        
        Args:
            config: Server configuration
        """
        self.config = config or Config()
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'tegrastats-api-secret'
        
        # Setup CORS
        CORS(self.app, origins=self.config.cors_origins)
        
        # Setup SocketIO
        self.socketio = SocketIO(
            self.app,
            cors_allowed_origins=self.config.cors_origins,
            async_mode='threading',
            allow_unsafe_werkzeug=self.config.allow_unsafe_werkzeug,
            logger=False,
            engineio_logger=False
        )
        
        # Initialize components
        self.parser = TegrastatsParser(interval=self.config.tegrastats_interval)
        self.limiter = ConnectionLimiter(max_connections=self.config.max_connections)
        self.model_monitor = ModelMonitor(max_log_lines=500)
        
        # Setup routes and events
        self._setup_routes()
        self._setup_socketio_events()
        
        # Data update thread
        self._update_thread: Optional[threading.Thread] = None
        self._running = False
        self._shutdown_requested = False
        self._shutdown_deferred = False
        
        logger.info("Tegrastats API服务器已初始化")
    
    def _setup_routes(self) -> None:
        """Setup Flask routes."""
        
        @self.app.route('/api/health', methods=['GET'])
        def health():
            """Health check endpoint."""
            return jsonify({
                'status': 'healthy',
                'service': 'tegrastats-api',
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'connected_clients': self.limiter.get_count(),
                'telemetry_meta': self.parser.get_snapshot()[1],
                'capabilities': self.parser.capability_diagnostics(),
                'package_version': __import__('tegrastats_api').__version__,
                'parser_file': __import__('inspect').getfile(TegrastatsParser)
            })
        
        @self.app.route('/api/status', methods=['GET'])
        def status():
            """Get complete system status."""
            data, meta = self.parser.get_snapshot()
            if not data:
                return jsonify({'error': 'No data available', 'telemetry_meta': meta}), 503
            
            # Add timestamp in ISO format
            data['timestamp'] = datetime.utcnow().isoformat() + 'Z'
            data['telemetry_meta'] = meta
            return jsonify(data)
        
        @self.app.route('/api/cpu', methods=['GET'])
        def cpu():
            """Get CPU information."""
            data, meta = self.parser.get_snapshot()
            if not data or 'cpu' not in data:
                return jsonify({'error': 'CPU data not available', 'telemetry_meta': meta}), 503
            
            return jsonify({
                'cpu': data['cpu'],
                'telemetry_meta': meta,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            })
        
        @self.app.route('/api/memory', methods=['GET'])
        def memory():
            """Get memory information."""
            data, meta = self.parser.get_snapshot()
            if not data or 'memory' not in data:
                return jsonify({'error': 'Memory data not available', 'telemetry_meta': meta}), 503
            
            return jsonify({
                'memory': data['memory'],
                'telemetry_meta': meta,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            })
        
        @self.app.route('/api/temperature', methods=['GET'])
        def temperature():
            """Get temperature information."""
            data, meta = self.parser.get_snapshot()
            if not data or 'temperature' not in data:
                return jsonify({'error': 'Temperature data not available', 'telemetry_meta': meta}), 503
            
            return jsonify({
                'temperature': data['temperature'],
                'telemetry_meta': meta,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            })
        
        @self.app.route('/api/power', methods=['GET'])
        def power():
            """Get power information."""
            data, meta = self.parser.get_snapshot()
            if not data or 'power' not in data:
                return jsonify({'error': 'Power data not available', 'telemetry_meta': meta}), 503
            
            return jsonify({
                'power': data['power'],
                'telemetry_meta': meta,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            })
        
        @self.app.route('/api/model_status', methods=['GET'])
        def model_status():
            """Get model status information."""
            status = self.model_monitor.get_all_status()
            return jsonify(status)
        
        @self.app.route('/api/model_logs/<model_type>', methods=['GET'])
        def model_logs(model_type):
            """Get model logs for a specific model."""
            lines = request.args.get('lines', default=100, type=int)
            lines = min(lines, 1000)  # Cap at 1000 lines
            
            if model_type not in ['llm', 'embedding', 'reranker']:
                return jsonify({'error': 'Invalid model type'}), 400
            
            logs = self.model_monitor.get_logs(model_type, lines)
            return jsonify({
                'model_type': model_type,
                'logs': logs,
                'count': len(logs),
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            })
    
    def _setup_socketio_events(self) -> None:
        """Setup SocketIO event handlers."""
        
        @self.socketio.on('connect')
        def handle_connect():
            """Handle client connection."""
            client_ip = request.environ.get('REMOTE_ADDR', 'unknown')
            
            if not self.limiter.can_accept():
                logger.warning(f"拒绝连接 {client_ip}: 达到最大连接数限制")
                return False
            
            if self.limiter.add_connection():
                logger.info(f"WebSocket客户端连接: {client_ip}, SID: {request.sid}, "
                           f"当前连接数: {self.limiter.get_count()}")
                return True
            else:
                logger.warning(f"无法添加连接: {client_ip}")
                return False
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            """Handle client disconnection."""
            client_ip = request.environ.get('REMOTE_ADDR', 'unknown')
            self.limiter.remove_connection()
            logger.info(f"WebSocket客户端断开: {client_ip}, SID: {request.sid}, "
                       f"当前连接数: {self.limiter.get_count()}")
    
    def _update_data_thread(self) -> None:
        """Background thread for updating data."""
        logger.info("数据更新线程启动")
        
        while self._running:
            try:
                if self.limiter.get_count() > 0:
                    # Send tegrastats data
                    data, meta = self.parser.get_snapshot()
                    if data:
                        # Add timestamp
                        data['timestamp'] = datetime.utcnow().isoformat() + 'Z'
                        data['telemetry_meta'] = meta
                        
                        # Emit to all connected clients
                        self.socketio.emit('tegrastats_update', data)
                        logger.debug(f"向 {self.limiter.get_count()} 个客户端发送tegrastats数据更新")
                    
                    # Send model status data
                    model_status = self.model_monitor.get_all_status()
                    self.socketio.emit('model_status_update', model_status)
                    logger.debug(f"向 {self.limiter.get_count()} 个客户端发送模型状态更新")
                
                time.sleep(self.config.update_interval)
                
            except Exception as e:
                logger.error(f"数据更新线程错误: {e}")
                time.sleep(1)
        
        logger.info("数据更新线程停止")
    
    def start(self) -> None:
        """Start the server components."""
        previous = self._shutdown_deferred
        self._shutdown_deferred = True
        try:
            if self._shutdown_requested:
                return
            # Start tegrastats parser
            self.parser.start()
            if self._shutdown_requested:
                return
            
            # Start model monitor
            self.model_monitor.start()
            if self._shutdown_requested:
                return
            
            # Start data update thread
            self._running = True
            self._update_thread = threading.Thread(target=self._update_data_thread, daemon=True)
            self._update_thread.start()
            
            logger.info("服务器组件已启动")
            
        except Exception as e:
            logger.error(f"启动服务器组件失败: {e}")
            try:
                self.stop()
            except Exception:
                logger.exception('Startup failed; component cleanup also failed')
            raise
        finally:
            self._shutdown_deferred = previous

    def request_shutdown(self) -> bool:
        """Signal-safe intent only; return whether the blocking run may unwind."""
        self._shutdown_requested = True
        return not self._shutdown_deferred
    
    def stop(self) -> None:
        """Stop the server components."""
        previous = self._shutdown_deferred
        self._shutdown_deferred = True
        try:
            self._stop_components()
        finally:
            self._shutdown_deferred = previous

    def _stop_components(self) -> None:
        logger.info("正在停止服务器...")
        
        # Stop data update thread
        self._running = False
        errors = []
        try:
            if self._update_thread and self._update_thread.is_alive():
                self._update_thread.join(timeout=2)
        except Exception as error:
            errors.append(error)
            logger.exception('Update thread cleanup failed')
        for name, stop in (('parser', self.parser.stop), ('model monitor', self.model_monitor.stop)):
            try:
                stop()
            except Exception as error:
                errors.append(error)
                logger.exception('%s cleanup failed', name)
        if errors:
            raise errors[0]
        
        logger.info("服务器已关闭")
    
    def run(self, **kwargs) -> None:
        """
        Run the server.
        
        Args:
            **kwargs: Additional arguments for SocketIO.run()
        """
        try:
            self.start()
            if self._shutdown_requested:
                return
            # Configure logging
            logging.basicConfig(
                level=getattr(logging, self.config.log_level),
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            
            logger.info(f"启动Tegrastats API服务器")
            logger.info(f"监听地址: {self.config.host}:{self.config.port}")
            logger.info(f"最大连接数: {self.config.max_connections}")
            logger.info(f"数据更新频率: {self.config.update_interval}秒")
            
            # Run server
            run_kwargs = {
                'host': self.config.host,
                'port': self.config.port,
                'debug': self.config.debug,
                **kwargs
            }
            
            # Add allow_unsafe_werkzeug if enabled in config
            if self.config.allow_unsafe_werkzeug:
                run_kwargs['allow_unsafe_werkzeug'] = True
                logger.warning("使用不安全的Werkzeug模式 (仅用于开发环境)")
            
            self.socketio.run(self.app, **run_kwargs)
            
        except KeyboardInterrupt:
            logger.info("收到中断信号")
        except Exception as e:
            logger.error(f"服务器运行错误: {e}")
            raise
        finally:
            failed = _has_primary_failure(sys.exc_info()[1])
            try:
                self.stop()
            except Exception:
                if not failed:
                    raise
                logger.exception('Run failed; cleanup also failed')
    
    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        try:
            self.stop()
        except Exception:
            if not _has_primary_failure(exc_val):
                raise
            logger.exception('Context failed; cleanup also failed')
