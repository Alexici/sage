import 'dart:convert';

const int kSupportedProtocol = 1;

enum SageActivityState {
  idle,
  listening,
  transcribing,
  thinking,
  usingTool,
  speaking,
  awaitingConfirm,
  offline;

  static SageActivityState fromWire(String? raw) {
    switch (raw) {
      case 'idle':
        return SageActivityState.idle;
      case 'listening':
        return SageActivityState.listening;
      case 'transcribing':
        return SageActivityState.transcribing;
      case 'thinking':
        return SageActivityState.thinking;
      case 'using_tool':
        return SageActivityState.usingTool;
      case 'speaking':
        return SageActivityState.speaking;
      case 'awaiting_confirm':
        return SageActivityState.awaitingConfirm;
      default:
        return SageActivityState.idle;
    }
  }
}

class HistoryTurn {
  final String role;
  final String text;
  final String? source;

  const HistoryTurn({required this.role, required this.text, this.source});

  factory HistoryTurn.fromJson(Map<String, dynamic> json) {
    return HistoryTurn(
      role: (json['role'] as String?) ?? 'assistant',
      text: (json['text'] as String?) ?? '',
      source: json['source'] as String?,
    );
  }
}

sealed class ServerMessage {
  final String type;
  final int seq;
  final double ts;

  const ServerMessage({
    required this.type,
    required this.seq,
    required this.ts,
  });

  static ServerMessage parse(String rawJson) {
    final decoded = jsonDecode(rawJson);
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('Server frame must be a JSON object');
    }
    return fromJson(decoded);
  }

  static ServerMessage fromJson(Map<String, dynamic> json) {
    final type = (json['type'] as String?) ?? '';
    final seq = (json['seq'] as num?)?.toInt() ?? 0;
    final ts = (json['ts'] as num?)?.toDouble() ?? 0.0;

    switch (type) {
      case 'hello':
        final rawHistory = (json['history'] as List<dynamic>?) ?? const [];
        final rawTools = (json['tools'] as List<dynamic>?) ?? const [];
        final pendingRaw = json['pending_confirm'];
        ConfirmMessage? pendingConfirm;
        if (pendingRaw is Map<String, dynamic>) {
          pendingConfirm = ConfirmMessage.fromJson(
            pendingRaw,
            seq: seq,
            ts: ts,
            useTimeoutLeft: true,
          );
        }
        final isLocal = (json['local'] as bool?) ?? false;
        final canTalk = (json['can_talk'] as bool?) ?? isLocal;
        final rawAudio = json['audio'];
        return HelloMessage(
          seq: seq,
          ts: ts,
          protocol: (json['protocol'] as num?)?.toInt() ?? 1,
          name: (json['name'] as String?) ?? 'Sage',
          user: (json['user'] as String?) ?? 'Alex',
          device: (json['device'] as String?) ?? 'local',
          local: isLocal,
          canTalk: canTalk,
          wakeEnabled: (json['wake'] as bool?) ?? false,
          wakePhrase: (json['wake_phrase'] as String?) ?? 'Hey Sage',
          audio: rawAudio is Map<String, dynamic> ? rawAudio : null,
          busy: (json['busy'] as bool?) ?? false,
          state: SageActivityState.fromWire(json['state'] as String?),
          tools: rawTools.map((e) => e.toString()).toList(growable: false),
          history: rawHistory
              .whereType<Map<String, dynamic>>()
              .map(HistoryTurn.fromJson)
              .toList(growable: false),
          pendingConfirm: pendingConfirm,
        );

      case 'state':
        return StateMessage(
          seq: seq,
          ts: ts,
          value: SageActivityState.fromWire(json['value'] as String?),
        );

      case 'wake':
        final hasSource = json['source'] is String;
        return WakeStateMessage(
          seq: seq,
          ts: ts,
          enabled: (json['enabled'] as bool?) ?? (hasSource ? true : false),
          phrase: (json['phase'] as String?) ?? (json['model'] as String?),
        );

      case 'user':
        return UserMessage(
          seq: seq,
          ts: ts,
          text: (json['text'] as String?) ?? '',
          source: (json['source'] as String?) ?? 'text',
        );

      case 'token':
        return TokenMessage(
          seq: seq,
          ts: ts,
          text: (json['text'] as String?) ?? '',
        );

      case 'reply_end':
        return ReplyEndMessage(seq: seq, ts: ts);

      case 'tool_start':
        return ToolStartMessage(
          seq: seq,
          ts: ts,
          name: (json['name'] as String?) ?? '',
          arguments: _stringifyArgs(json['arguments']),
        );

      case 'tool':
        return ToolMessage(
          seq: seq,
          ts: ts,
          name: (json['name'] as String?) ?? '',
          arguments: _stringifyArgs(json['arguments']),
          chars: (json['chars'] as num?)?.toInt() ?? 0,
          seconds: (json['seconds'] as num?)?.toDouble() ?? 0.0,
          tier: (json['tier'] as String?) ?? 'auto',
          outcome: (json['outcome'] as String?) ?? 'ran',
        );

      case 'confirm':
        return ConfirmMessage.fromJson(json, seq: seq, ts: ts);

      case 'confirm_done':
        return ConfirmDoneMessage(
          seq: seq,
          ts: ts,
          id: (json['id'] as String?) ?? '',
          approved: (json['approved'] as bool?) ?? false,
          reason: (json['reason'] as String?) ?? 'answered',
        );

      case 'stopped':
        return StoppedMessage(seq: seq, ts: ts);

      case 'error':
        return ErrorMessage(
          seq: seq,
          ts: ts,
          message: (json['message'] as String?) ?? 'Unknown error',
          code: json['code'] as String?,
        );

      case 'notice':
        return NoticeMessage(
          seq: seq,
          ts: ts,
          level: (json['level'] as String?) ?? 'info',
          message: (json['message'] as String?) ?? '',
        );

      case 'audio_start':
        return AudioStartMessage(
          seq: seq,
          ts: ts,
          format: (json['format'] as String?) ?? 'pcm_s16le',
          rate: (json['rate'] as num?)?.toInt() ?? 24000,
          channels: (json['channels'] as num?)?.toInt() ?? 1,
        );

      case 'audio_stop':
        return AudioStopMessage(seq: seq, ts: ts);

      case 'latency':
        final rawTools = (json['tools'] as List<dynamic>?) ?? const [];
        return LatencyMessage(
          seq: seq,
          ts: ts,
          firstToken: (json['first_token'] as num?)?.toDouble(),
          firstChunkReady: (json['first_chunk_ready'] as num?)?.toDouble(),
          realStart: (json['real_start'] as num?)?.toDouble(),
          firstChunk: json['first_chunk'] as String?,
          held: (json['held'] as num?)?.toDouble(),
          gaps: (json['gaps'] as num?)?.toDouble(),
          filler: json['filler'] as String?,
          tools: rawTools.map((e) => e.toString()).toList(growable: false),
        );

      case 'reset':
        return ResetMessage(seq: seq, ts: ts);

      default:
        return UnknownServerMessage(type: type, seq: seq, ts: ts, raw: json);
    }
  }

  static String _stringifyArgs(dynamic raw) {
    if (raw == null) return '{}';
    if (raw is String) return raw;
    try {
      return jsonEncode(raw);
    } catch (_) {
      return raw.toString();
    }
  }
}

class HelloMessage extends ServerMessage {
  final int protocol;
  final String name;
  final String user;
  final String device;
  final bool local;
  final bool canTalk;
  final bool wakeEnabled;
  final String wakePhrase;
  final Map<String, dynamic>? audio;
  final bool busy;
  final SageActivityState state;
  final List<String> tools;
  final List<HistoryTurn> history;
  final ConfirmMessage? pendingConfirm;

  const HelloMessage({
    required super.seq,
    required super.ts,
    required this.protocol,
    required this.name,
    required this.user,
    required this.device,
    required this.local,
    bool? canTalk,
    this.wakeEnabled = false,
    this.wakePhrase = 'Hey Sage',
    this.audio,
    required this.busy,
    required this.state,
    required this.tools,
    required this.history,
    this.pendingConfirm,
  }) : canTalk = canTalk ?? local,
       super(type: 'hello');
}

class StateMessage extends ServerMessage {
  final SageActivityState value;

  const StateMessage({
    required super.seq,
    required super.ts,
    required this.value,
  }) : super(type: 'state');
}

class WakeStateMessage extends ServerMessage {
  final bool enabled;
  final String? phrase;

  const WakeStateMessage({
    required super.seq,
    required super.ts,
    required this.enabled,
    this.phrase,
  }) : super(type: 'wake');
}

class UserMessage extends ServerMessage {
  final String text;
  final String source;

  const UserMessage({
    required super.seq,
    required super.ts,
    required this.text,
    required this.source,
  }) : super(type: 'user');
}

class TokenMessage extends ServerMessage {
  final String text;

  const TokenMessage({
    required super.seq,
    required super.ts,
    required this.text,
  }) : super(type: 'token');
}

class ReplyEndMessage extends ServerMessage {
  const ReplyEndMessage({required super.seq, required super.ts})
    : super(type: 'reply_end');
}

class ToolStartMessage extends ServerMessage {
  final String name;
  final String arguments;

  const ToolStartMessage({
    required super.seq,
    required super.ts,
    required this.name,
    required this.arguments,
  }) : super(type: 'tool_start');
}

class ToolMessage extends ServerMessage {
  final String name;
  final String arguments;
  final int chars;
  final double seconds;
  final String tier;
  final String outcome;

  const ToolMessage({
    required super.seq,
    required super.ts,
    required this.name,
    required this.arguments,
    required this.chars,
    required this.seconds,
    required this.tier,
    required this.outcome,
  }) : super(type: 'tool');
}

class ConfirmMessage extends ServerMessage {
  final String id;
  final String tool;
  final String summary;
  final Map<String, dynamic> arguments;
  final double timeout;

  const ConfirmMessage({
    required super.seq,
    required super.ts,
    required this.id,
    required this.tool,
    required this.summary,
    required this.arguments,
    required this.timeout,
  }) : super(type: 'confirm');

  factory ConfirmMessage.fromJson(
    Map<String, dynamic> json, {
    required int seq,
    required double ts,
    bool useTimeoutLeft = false,
  }) {
    Map<String, dynamic> parsedArgs = const {};
    final rawArgs = json['arguments'];
    if (rawArgs is Map<String, dynamic>) {
      parsedArgs = rawArgs;
    } else if (rawArgs is String && rawArgs.trim().isNotEmpty) {
      try {
        final decoded = jsonDecode(rawArgs);
        if (decoded is Map<String, dynamic>) parsedArgs = decoded;
      } catch (_) {}
    }
    final timeoutVal = useTimeoutLeft
        ? (json['timeout_left'] ?? json['timeout'] ?? 120.0)
        : (json['timeout'] ?? json['timeout_left'] ?? 120.0);
    return ConfirmMessage(
      seq: seq,
      ts: ts,
      id: (json['id'] as String?) ?? '',
      tool: (json['tool'] as String?) ?? '',
      summary: (json['summary'] as String?) ?? '',
      arguments: parsedArgs,
      timeout: (timeoutVal as num?)?.toDouble() ?? 120.0,
    );
  }
}

class ConfirmDoneMessage extends ServerMessage {
  final String id;
  final bool approved;
  final String reason;

  const ConfirmDoneMessage({
    required super.seq,
    required super.ts,
    required this.id,
    required this.approved,
    required this.reason,
  }) : super(type: 'confirm_done');
}

class StoppedMessage extends ServerMessage {
  const StoppedMessage({required super.seq, required super.ts})
    : super(type: 'stopped');
}

class ErrorMessage extends ServerMessage {
  final String message;
  final String? code;

  const ErrorMessage({
    required super.seq,
    required super.ts,
    required this.message,
    this.code,
  }) : super(type: 'error');
}

class NoticeMessage extends ServerMessage {
  final String level;
  final String message;

  const NoticeMessage({
    required super.seq,
    required super.ts,
    required this.level,
    required this.message,
  }) : super(type: 'notice');
}

class AudioStartMessage extends ServerMessage {
  final String format;
  final int rate;
  final int channels;

  const AudioStartMessage({
    required super.seq,
    required super.ts,
    required this.format,
    required this.rate,
    required this.channels,
  }) : super(type: 'audio_start');
}

class AudioStopMessage extends ServerMessage {
  const AudioStopMessage({required super.seq, required super.ts})
    : super(type: 'audio_stop');
}

class LatencyMessage extends ServerMessage {
  final double? firstToken;
  final double? firstChunkReady;
  final double? realStart;
  final String? firstChunk;
  final double? held;
  final double? gaps;
  final String? filler;
  final List<String> tools;

  const LatencyMessage({
    required super.seq,
    required super.ts,
    this.firstToken,
    this.firstChunkReady,
    this.realStart,
    this.firstChunk,
    this.held,
    this.gaps,
    this.filler,
    this.tools = const [],
  }) : super(type: 'latency');
}

class ResetMessage extends ServerMessage {
  const ResetMessage({required super.seq, required super.ts})
    : super(type: 'reset');
}

class UnknownServerMessage extends ServerMessage {
  final Map<String, dynamic> raw;

  const UnknownServerMessage({
    required super.type,
    required super.seq,
    required super.ts,
    required this.raw,
  });
}

class ClientFrames {
  static String send(String text) => jsonEncode({'type': 'send', 'text': text});
  static String cancel() => jsonEncode({'type': 'cancel'});
  static String reset() => jsonEncode({'type': 'reset'});
  static String answer(String id, bool approved) =>
      jsonEncode({'type': 'answer', 'id': id, 'approved': approved});
  static String talkStart({String? source}) =>
      jsonEncode({'type': 'talk_start', 'source': ?source});
  static String talkStop() => jsonEncode({'type': 'talk_stop'});
  static String audioConfig({required bool playback}) =>
      jsonEncode({'type': 'audio_config', 'playback': playback});
  static String wakeConfig({required bool enabled}) =>
      jsonEncode({'type': 'wake_config', 'enabled': enabled});
}
