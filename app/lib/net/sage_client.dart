import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../protocol/messages.dart';

class ConnectionTestResult {
  final bool ok;
  final String step; // "health" | "ws" | "ok"
  final String message;

  const ConnectionTestResult({
    required this.ok,
    required this.step,
    required this.message,
  });
}

enum DisconnectReason { normal, networkError, authFailed, versionMismatch }

class SageNetClient {
  static const List<int> backoffSeconds = [1, 2, 4, 8, 10];

  final void Function(ServerMessage message) onMessage;
  final void Function(bool reconnecting) onConnecting;
  final void Function(DisconnectReason reason, String? error) onDisconnected;

  WebSocket? _ws;
  StreamSubscription<dynamic>? _sub;
  Timer? _retryTimer;
  int _retryAttempt = 0;
  bool _disposed = false;
  bool _manualClose = false;
  String _address = '127.0.0.1:8765';
  String _token = '';

  SageNetClient({
    required this.onMessage,
    required this.onConnecting,
    required this.onDisconnected,
  });

  bool get isConnected => _ws != null && _ws!.readyState == WebSocket.open;

  void connect({required String address, required String token}) {
    _address = _normalizeAddress(address);
    _token = token.trim();
    _manualClose = false;
    _retryAttempt = 0;
    _retryTimer?.cancel();
    _closeSocket();
    _openSocket(isReconnect: false);
  }

  void disconnect() {
    _manualClose = true;
    _retryTimer?.cancel();
    _closeSocket();
    onDisconnected(DisconnectReason.normal, null);
  }

  void dispose() {
    _disposed = true;
    _manualClose = true;
    _retryTimer?.cancel();
    _closeSocket();
  }

  void sendRaw(String frame) {
    final ws = _ws;
    if (ws != null && ws.readyState == WebSocket.open) {
      ws.add(frame);
    }
  }

  Future<void> _openSocket({required bool isReconnect}) async {
    if (_disposed || _manualClose) return;
    onConnecting(isReconnect);

    final wsUri = Uri.parse('ws://$_address/ws');
    try {
      final ws = await WebSocket.connect(
        wsUri.toString(),
        headers: {if (_token.isNotEmpty) 'Authorization': 'Bearer $_token'},
      ).timeout(const Duration(seconds: 6));

      if (_disposed || _manualClose) {
        await ws.close();
        return;
      }

      ws.pingInterval = const Duration(seconds: 20);
      _ws = ws;
      _retryAttempt = 0;

      _sub = ws.listen(
        (dynamic data) {
          if (data is! String) return; // v1 uses UTF-8 JSON text frames only
          try {
            final msg = ServerMessage.parse(data);
            if (msg is HelloMessage && msg.protocol > kSupportedProtocol) {
              _manualClose = true;
              _closeSocket();
              onMessage(msg);
              onDisconnected(
                DisconnectReason.versionMismatch,
                'The server is newer than this app.',
              );
              return;
            }
            onMessage(msg);
          } catch (_) {
            // Ignore malformed or unknown frames
          }
        },
        onError: (_) => _scheduleReconnect(),
        onDone: () => _scheduleReconnect(),
        cancelOnError: true,
      );
    } on WebSocketException catch (e) {
      final msg = e.message;
      if (msg.contains('401') || msg.contains('403')) {
        _manualClose = true;
        onDisconnected(
          DisconnectReason.authFailed,
          'This device is not authorised. Check the token.',
        );
        return;
      }
      _scheduleReconnect();
    } catch (_) {
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    _closeSocket();
    if (_disposed || _manualClose) return;

    final idx = _retryAttempt < backoffSeconds.length
        ? _retryAttempt
        : backoffSeconds.length - 1;
    final delaySec = backoffSeconds[idx];
    _retryAttempt += 1;

    onDisconnected(DisconnectReason.networkError, null);
    _retryTimer?.cancel();
    _retryTimer = Timer(Duration(seconds: delaySec), () {
      _openSocket(isReconnect: true);
    });
  }

  void _closeSocket() {
    _sub?.cancel();
    _sub = null;
    try {
      _ws?.close();
    } catch (_) {}
    _ws = null;
  }

  static String _normalizeAddress(String raw) {
    var trimmed = raw.trim();
    trimmed = trimmed.replaceFirst(RegExp(r'^(https?|wss?)://'), '');
    if (trimmed.endsWith('/')) {
      trimmed = trimmed.substring(0, trimmed.length - 1);
    }
    return trimmed.isEmpty ? '127.0.0.1:8765' : trimmed;
  }

  /// Runs Step 1 (`GET /health`) then Step 2 (`ws://HOST/ws` + `hello`) and reports which step failed.
  static Future<ConnectionTestResult> testConnection({
    required String address,
    required String token,
  }) async {
    final hostPort = _normalizeAddress(address);
    final healthUri = Uri.parse('http://$hostPort/health');
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 4);

    // Step 1: GET /health
    try {
      final req = await client.getUrl(healthUri);
      final resp = await req.close().timeout(const Duration(seconds: 4));
      final body = await resp.transform(utf8.decoder).join();
      if (resp.statusCode != 200) {
        return ConnectionTestResult(
          ok: false,
          step: 'health',
          message: 'Health check failed: HTTP ${resp.statusCode}.',
        );
      }
      final json = jsonDecode(body);
      if (json is! Map<String, dynamic> || json['ok'] != true) {
        return const ConnectionTestResult(
          ok: false,
          step: 'health',
          message: 'Health check returned unexpected payload.',
        );
      }
      final proto = (json['protocol'] as num?)?.toInt() ?? 1;
      if (proto > kSupportedProtocol) {
        return ConnectionTestResult(
          ok: false,
          step: 'health',
          message: 'The server is newer than this app (protocol $proto).',
        );
      }
    } catch (e) {
      return ConnectionTestResult(
        ok: false,
        step: 'health',
        message: 'Health step (/health) unreachable: ${_cleanErr(e)}',
      );
    } finally {
      client.close(force: true);
    }

    // Step 2: WebSocket upgrade + hello frame
    final wsUri = Uri.parse('ws://$hostPort/ws');
    WebSocket? ws;
    try {
      ws = await WebSocket.connect(
        wsUri.toString(),
        headers: {
          if (token.trim().isNotEmpty)
            'Authorization': 'Bearer ${token.trim()}',
        },
      ).timeout(const Duration(seconds: 4));

      final firstFrame = await ws.first.timeout(const Duration(seconds: 4));
      if (firstFrame is String) {
        final msg = ServerMessage.parse(firstFrame);
        if (msg is HelloMessage) {
          return ConnectionTestResult(
            ok: true,
            step: 'ok',
            message:
                'Connected to ${msg.name} as "${msg.device}" (protocol v${msg.protocol}).',
          );
        }
      }
      return const ConnectionTestResult(
        ok: true,
        step: 'ok',
        message: 'Health and WebSocket connected.',
      );
    } on WebSocketException catch (e) {
      if (e.message.contains('401') || e.message.contains('403')) {
        return const ConnectionTestResult(
          ok: false,
          step: 'ws',
          message: 'WebSocket step failed: This device is not authorised. Check the token.',
        );
      }
      return ConnectionTestResult(
        ok: false,
        step: 'ws',
        message: 'WebSocket step (/ws) failed: ${_cleanErr(e)}',
      );
    } catch (e) {
      return ConnectionTestResult(
        ok: false,
        step: 'ws',
        message: 'WebSocket step (/ws) failed: ${_cleanErr(e)}',
      );
    } finally {
      try {
        await ws?.close();
      } catch (_) {}
    }
  }

  static String _cleanErr(Object e) {
    return e.toString().replaceAll(RegExp(r'\s+'), ' ').trim();
  }
}
