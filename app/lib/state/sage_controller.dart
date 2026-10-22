import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../net/sage_client.dart';
import '../protocol/messages.dart';
import 'chat_state.dart';

enum AppTab { voice, text }

class SageController extends ChangeNotifier {
  static const String _kPrefAddress = 'sage_server_address';
  static const String _kPrefToken = 'sage_device_token';
  static const String _kPrefShowTimings = 'sage_show_timings';
  static const String _kPrefShowSessionGuard = 'sage_show_session_guard';
  static const String _kPrefShowTools = 'sage_show_tools';
  static const String _kPrefTextScale = 'sage_text_scale';
  static const String _kPrefWakeWord = 'sage_wake_word_enabled';
  static const int _kPttClickThresholdMs = 250;

  ChatState _state = const ChatState();
  late final SageNetClient _net;

  AppTab _activeTab = AppTab.text;
  String _serverAddress = '127.0.0.1:8765';
  String _deviceToken = '';
  bool _showTimings = false;
  bool _showSessionGuard = false;
  bool _showToolDetails = true;
  bool _wakeWordPreferred = false;
  int _textSizePercent = 100; // 90 to 130
  bool _shouldOpenSettings = false;

  bool _isRecordingVoice = false;
  bool _isPttLatched = false;
  DateTime? _micPressTime;
  bool _frameScheduled = false;

  SageController({bool autoConnect = true}) {
    _net = SageNetClient(
      onMessage: handleServerMessage,
      onConnecting: (reconnecting) {
        _state = _state.copyWith(
          connection: reconnecting
              ? ConnectionStatus.reconnecting
              : ConnectionStatus.connecting,
          activity: SageActivityState.offline,
        );
        _notifyImmediate();
      },
      onDisconnected: (reason, error) {
        if (reason == DisconnectReason.authFailed) {
          _state = _state.copyWith(
            connection: ConnectionStatus.authFailed,
            activity: SageActivityState.offline,
            connectionError: error,
          );
          _shouldOpenSettings = true;
        } else if (reason == DisconnectReason.versionMismatch) {
          _state = _state.copyWith(
            connection: ConnectionStatus.versionMismatch,
            activity: SageActivityState.offline,
            connectionError: error,
          );
        } else if (reason == DisconnectReason.networkError) {
          _state = _state.copyWith(
            connection: ConnectionStatus.reconnecting,
            activity: SageActivityState.offline,
          );
        } else {
          _state = _state.copyWith(
            connection: ConnectionStatus.offline,
            activity: SageActivityState.offline,
          );
        }
        _isRecordingVoice = false;
        _isPttLatched = false;
        _micPressTime = null;
        _notifyImmediate();
      },
    );
    if (autoConnect) {
      _loadSettingsAndConnect();
    }
  }

  ChatState get state => _state;
  AppTab get activeTab => _activeTab;
  String get serverAddress => _serverAddress;
  String get deviceToken => _deviceToken;
  bool get showTimings => _showTimings;
  bool get showSessionGuard => _showSessionGuard;
  bool get showToolDetails => _showToolDetails;
  bool get wakeWordEnabled => _state.wakeEnabled;
  int get textSizePercent => _textSizePercent;
  double get textScale => (_textSizePercent.clamp(90, 130)) / 100.0;
  bool get isHoldingTalk =>
      _isRecordingVoice || _state.activity == SageActivityState.listening;
  bool get isPttLatched => _isPttLatched;

  void setActiveTab(AppTab tab) {
    if (_activeTab == tab) return;
    _activeTab = tab;
    _notifyImmediate();
  }

  bool consumeShouldOpenSettings() {
    final v = _shouldOpenSettings;
    _shouldOpenSettings = false;
    return v;
  }

  Future<void> _loadSettingsAndConnect() async {
    final prefs = await SharedPreferences.getInstance();
    _serverAddress = prefs.getString(_kPrefAddress) ?? '127.0.0.1:8765';
    _deviceToken = prefs.getString(_kPrefToken) ?? '';
    _showTimings = prefs.getBool(_kPrefShowTimings) ?? false;
    _showSessionGuard = prefs.getBool(_kPrefShowSessionGuard) ?? false;
    _showToolDetails = prefs.getBool(_kPrefShowTools) ?? true;
    _wakeWordPreferred = prefs.getBool(_kPrefWakeWord) ?? false;
    _textSizePercent = (prefs.getInt(_kPrefTextScale) ?? 100).clamp(90, 130);
    notifyListeners();
    reconnect();
  }

  Future<void> updateSettings({
    String? address,
    String? token,
    bool? showTimings,
    bool? showSessionGuard,
    bool? showToolDetails,
    int? textSizePercent,
    bool reconnectNow = false,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    if (address != null) {
      _serverAddress = address.trim().isEmpty
          ? '127.0.0.1:8765'
          : address.trim();
      await prefs.setString(_kPrefAddress, _serverAddress);
    }
    if (token != null) {
      _deviceToken = token.trim();
      await prefs.setString(_kPrefToken, _deviceToken);
    }
    if (showTimings != null) {
      _showTimings = showTimings;
      await prefs.setBool(_kPrefShowTimings, _showTimings);
    }
    if (showSessionGuard != null) {
      _showSessionGuard = showSessionGuard;
      await prefs.setBool(_kPrefShowSessionGuard, _showSessionGuard);
    }
    if (showToolDetails != null) {
      _showToolDetails = showToolDetails;
      await prefs.setBool(_kPrefShowTools, _showToolDetails);
    }
    if (textSizePercent != null) {
      _textSizePercent = textSizePercent.clamp(90, 130);
      await prefs.setInt(_kPrefTextScale, _textSizePercent);
    }
    _notifyImmediate();
    if (reconnectNow) {
      reconnect();
    }
  }

  Future<void> toggleWakeWord() async {
    if (!_state.canTalk || _state.connection != ConnectionStatus.connected) {
      return;
    }
    final next = !_state.wakeEnabled;
    _wakeWordPreferred = next;
    _state = _state.copyWith(wakeEnabled: next);
    _net.sendRaw(ClientFrames.wakeConfig(enabled: next));
    _notifyImmediate();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_kPrefWakeWord, next);
  }

  void reconnect() {
    _state = _state.copyWith(clearConnectionError: true);
    _net.connect(address: _serverAddress, token: _deviceToken);
  }

  void handleServerMessage(ServerMessage msg) {
    _state = apply(_state, msg);
    if (msg is HelloMessage &&
        _wakeWordPreferred &&
        msg.canTalk &&
        !msg.wakeEnabled) {
      _state = _state.copyWith(wakeEnabled: true);
      _net.sendRaw(ClientFrames.wakeConfig(enabled: true));
    }
    if (msg is StateMessage && msg.value != SageActivityState.listening) {
      _isRecordingVoice = false;
      _isPttLatched = false;
      _micPressTime = null;
    }
    if (msg is TokenMessage) {
      _scheduleCoalescedNotify();
    } else {
      _notifyImmediate();
    }
  }

  void _scheduleCoalescedNotify() {
    if (_frameScheduled) return;
    final binding = SchedulerBinding.instance;
    if (binding.schedulerPhase == SchedulerPhase.idle ||
        binding.schedulerPhase == SchedulerPhase.postFrameCallbacks) {
      _frameScheduled = true;
      binding.scheduleFrameCallback((_) {
        _frameScheduled = false;
        if (!hasListeners) return;
        notifyListeners();
      });
      binding.scheduleFrame();
    } else {
      notifyListeners();
    }
  }

  void _notifyImmediate() {
    _frameScheduled = false;
    notifyListeners();
  }

  bool sendText(String rawText) {
    final trimmed = rawText.trim();
    if (trimmed.isEmpty) return false;
    if (_state.connection != ConnectionStatus.connected) return false;
    if (_state.activeConfirm != null) return false;
    final clipped = trimmed.length > 4000
        ? trimmed.substring(0, 4000)
        : trimmed;
    _net.sendRaw(ClientFrames.send(clipped));
    return true;
  }

  void stopTurn() {
    if (_state.connection != ConnectionStatus.connected) return;
    _isRecordingVoice = false;
    _isPttLatched = false;
    _micPressTime = null;
    _net.sendRaw(ClientFrames.cancel());
  }

  void clearConversation() {
    if (_state.connection != ConnectionStatus.connected) return;
    _net.sendRaw(ClientFrames.reset());
  }

  void answerConfirm(String id, bool approved) {
    if (_state.connection != ConnectionStatus.connected) return;
    _net.sendRaw(ClientFrames.answer(id, approved));
  }

  void expireConfirmLocally(String id) {
    final remaining = _state.confirmQueue.where((c) => c.id != id).toList();
    _state = _state.copyWith(
      confirmQueue: remaining,
      activity: remaining.isNotEmpty
          ? SageActivityState.awaitingConfirm
          : SageActivityState.thinking,
    );
    _notifyImmediate();
  }

  /// Dual-function Mic button pointer down:
  /// - If already listening (via latched PTT or Wake Word), clicking stops recording and transcribes immediately.
  /// - Otherwise starts recording and timestamps the press to distinguish Click vs Hold.
  void onMicPointerDown() {
    if (!_state.canTalk ||
        _state.connection != ConnectionStatus.connected ||
        _state.activeConfirm != null) {
      return;
    }
    if (_isPttLatched || _state.activity == SageActivityState.listening) {
      _isPttLatched = false;
      _isRecordingVoice = false;
      _micPressTime = null;
      _net.sendRaw(ClientFrames.talkStop());
      _notifyImmediate();
      return;
    }
    if (_isRecordingVoice) return;
    _micPressTime = DateTime.now();
    _isRecordingVoice = true;
    _isPttLatched = false;
    _net.sendRaw(ClientFrames.talkStart());
    _notifyImmediate();
  }

  /// Dual-function Mic button pointer up:
  /// - Held < 250ms -> latches Push-to-Talk (stays recording until next click).
  /// - Held >= 250ms -> finishes Hold-to-Talk (sends talk_stop immediately).
  void onMicPointerUp() {
    final pressedAt = _micPressTime;
    if (pressedAt == null) return;
    _micPressTime = null;
    final heldMs = DateTime.now().difference(pressedAt).inMilliseconds;
    if (heldMs < _kPttClickThresholdMs) {
      _isPttLatched = true;
      _notifyImmediate();
    } else {
      stopTalking();
    }
  }

  void startTalking() {
    if (!_state.canTalk ||
        _state.connection != ConnectionStatus.connected ||
        _state.activeConfirm != null ||
        _isRecordingVoice ||
        _state.activity == SageActivityState.listening) {
      return;
    }
    _isRecordingVoice = true;
    _isPttLatched = false;
    _net.sendRaw(ClientFrames.talkStart());
    _notifyImmediate();
  }

  void stopTalking() {
    if (!_isRecordingVoice && _state.activity != SageActivityState.listening) {
      return;
    }
    _isRecordingVoice = false;
    _isPttLatched = false;
    _micPressTime = null;
    if (_state.connection == ConnectionStatus.connected) {
      _net.sendRaw(ClientFrames.talkStop());
    }
    _notifyImmediate();
  }

  void dismissNotice(String id) {
    _state = _state.copyWith(
      notices: _state.notices.where((n) => n.id != id).toList(),
    );
    _notifyImmediate();
  }

  @override
  void dispose() {
    _net.dispose();
    super.dispose();
  }
}
