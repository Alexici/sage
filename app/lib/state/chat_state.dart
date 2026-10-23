import '../protocol/messages.dart';

enum ConnectionStatus {
  connecting,
  connected,
  reconnecting,
  authFailed,
  versionMismatch,
  offline,
}

sealed class TranscriptEntry {
  const TranscriptEntry();
}

class UserTurnEntry extends TranscriptEntry {
  final String text;
  final String source; // "text" | "voice"

  const UserTurnEntry({required this.text, required this.source});
}

enum InlineNoticeKind { muted, warn, danger }

class InlineNoticeEntry extends TranscriptEntry {
  final String text;
  final InlineNoticeKind kind;

  const InlineNoticeEntry({required this.text, required this.kind});
}

sealed class AssistantPart {
  const AssistantPart();
}

class AssistantTextPart extends AssistantPart {
  final String text;
  final bool isStreaming;

  const AssistantTextPart({required this.text, this.isStreaming = false});

  AssistantTextPart copyWith({String? text, bool? isStreaming}) {
    return AssistantTextPart(
      text: text ?? this.text,
      isStreaming: isStreaming ?? this.isStreaming,
    );
  }
}

class AssistantToolPart extends AssistantPart {
  final String name;
  final String arguments;
  final bool isRunning;
  final int? chars;
  final double? seconds;
  final String? tier;
  final String? outcome; // ran | error | denied | blocked | unknown

  const AssistantToolPart({
    required this.name,
    required this.arguments,
    this.isRunning = true,
    this.chars,
    this.seconds,
    this.tier,
    this.outcome,
  });
}

class AssistantStatusPart extends AssistantPart {
  final String text;
  final InlineNoticeKind kind;

  const AssistantStatusPart({
    required this.text,
    this.kind = InlineNoticeKind.muted,
  });
}

class AssistantTurnEntry extends TranscriptEntry {
  final List<AssistantPart> parts;
  final LatencyMessage? latency;
  final bool isStreaming;

  const AssistantTurnEntry({
    required this.parts,
    this.latency,
    this.isStreaming = false,
  });

  AssistantTurnEntry copyWith({
    List<AssistantPart>? parts,
    LatencyMessage? latency,
    bool? isStreaming,
  }) {
    return AssistantTurnEntry(
      parts: parts ?? this.parts,
      latency: latency ?? this.latency,
      isStreaming: isStreaming ?? this.isStreaming,
    );
  }
}

class ToastNotice {
  final String id;
  final String level; // "info" | "warn" | "danger"
  final String message;

  const ToastNotice({
    required this.id,
    required this.level,
    required this.message,
  });
}

class ChatState {
  final ConnectionStatus connection;
  final String assistantName;
  final String userName;
  final String deviceName;
  final bool isLocal;
  final bool canTalk;
  final bool wakeEnabled;
  final String wakePhrase;
  final bool followUpActive;
  final bool busy;
  final SageActivityState activity;
  final String? activeToolName;
  final List<String> enabledTools;
  final List<TranscriptEntry> entries;
  final List<ConfirmMessage> confirmQueue;
  final LatencyMessage? lastLatency;
  final List<ToastNotice> notices;
  final String? connectionError;
  final bool retainTypedInputOnBusy;

  const ChatState({
    this.connection = ConnectionStatus.offline,
    this.assistantName = 'Sage',
    this.userName = 'Alex',
    this.deviceName = '',
    this.isLocal = false,
    bool? canTalk,
    this.wakeEnabled = false,
    this.wakePhrase = 'Hey Sage',
    this.followUpActive = false,
    this.busy = false,
    this.activity = SageActivityState.offline,
    this.activeToolName,
    this.enabledTools = const [],
    this.entries = const [],
    this.confirmQueue = const [],
    this.lastLatency,
    this.notices = const [],
    this.connectionError,
    this.retainTypedInputOnBusy = false,
  }) : canTalk = canTalk ?? isLocal;

  ConfirmMessage? get activeConfirm =>
      confirmQueue.isNotEmpty ? confirmQueue.first : null;

  SageActivityState get effectiveCircleState {
    if (connection != ConnectionStatus.connected) {
      return SageActivityState.offline;
    }
    if (activity == SageActivityState.awaitingConfirm ||
        activeConfirm != null) {
      return SageActivityState.awaitingConfirm;
    }
    if (activeToolName != null) {
      return SageActivityState.usingTool;
    }
    if (activity == SageActivityState.offline) {
      return busy ? SageActivityState.thinking : SageActivityState.idle;
    }
    return activity;
  }

  String get stateLabel {
    final s = effectiveCircleState;
    switch (s) {
      case SageActivityState.idle:
        if (wakeEnabled) {
          return followUpActive
              ? 'Follow-up active'
              : 'Waiting for "$wakePhrase"';
        }
        return 'idle';
      case SageActivityState.listening:
        return 'listening';
      case SageActivityState.transcribing:
        return 'transcribing';
      case SageActivityState.thinking:
        return activeToolName != null ? 'using $activeToolName' : 'thinking';
      case SageActivityState.usingTool:
        return 'using ${activeToolName ?? 'tool'}';
      case SageActivityState.speaking:
        return 'speaking';
      case SageActivityState.awaitingConfirm:
        return 'waiting for you';
      case SageActivityState.offline:
        return 'offline';
    }
  }

  String? get lastTimingSummary {
    final lat = lastLatency;
    if (lat == null) return null;
    final sound = lat.realStart ?? lat.firstChunkReady ?? lat.firstToken;
    if (sound == null) return null;
    return 'first sound ${sound.toStringAsFixed(1)} s';
  }

  ChatState copyWith({
    ConnectionStatus? connection,
    String? assistantName,
    String? userName,
    String? deviceName,
    bool? isLocal,
    bool? canTalk,
    bool? wakeEnabled,
    String? wakePhrase,
    bool? followUpActive,
    bool? busy,
    SageActivityState? activity,
    String? activeToolName,
    bool clearActiveTool = false,
    List<String>? enabledTools,
    List<TranscriptEntry>? entries,
    List<ConfirmMessage>? confirmQueue,
    LatencyMessage? lastLatency,
    bool clearLastLatency = false,
    List<ToastNotice>? notices,
    String? connectionError,
    bool clearConnectionError = false,
    bool? retainTypedInputOnBusy,
  }) {
    return ChatState(
      connection: connection ?? this.connection,
      assistantName: assistantName ?? this.assistantName,
      userName: userName ?? this.userName,
      deviceName: deviceName ?? this.deviceName,
      isLocal: isLocal ?? this.isLocal,
      canTalk: canTalk ?? this.canTalk,
      wakeEnabled: wakeEnabled ?? this.wakeEnabled,
      wakePhrase: wakePhrase ?? this.wakePhrase,
      followUpActive: followUpActive ?? this.followUpActive,
      busy: busy ?? this.busy,
      activity: activity ?? this.activity,
      activeToolName: clearActiveTool
          ? null
          : (activeToolName ?? this.activeToolName),
      enabledTools: enabledTools ?? this.enabledTools,
      entries: entries ?? this.entries,
      confirmQueue: confirmQueue ?? this.confirmQueue,
      lastLatency: clearLastLatency ? null : (lastLatency ?? this.lastLatency),
      notices: notices ?? this.notices,
      connectionError: clearConnectionError
          ? null
          : (connectionError ?? this.connectionError),
      retainTypedInputOnBusy:
          retainTypedInputOnBusy ?? this.retainTypedInputOnBusy,
    );
  }
}

/// Pure reducer: applies one ServerMessage to ChatState.
ChatState apply(ChatState state, ServerMessage msg) {
  switch (msg) {
    case HelloMessage():
      if (msg.protocol > kSupportedProtocol) {
        return state.copyWith(
          connection: ConnectionStatus.versionMismatch,
          activity: SageActivityState.offline,
          connectionError: 'The server is newer than this app.',
        );
      }
      final rebuilt = <TranscriptEntry>[];
      for (final h in msg.history) {
        if (h.role == 'user') {
          rebuilt.add(UserTurnEntry(text: h.text, source: h.source ?? 'text'));
        } else {
          rebuilt.add(
            AssistantTurnEntry(
              parts: [AssistantTextPart(text: h.text, isStreaming: false)],
              isStreaming: false,
            ),
          );
        }
      }
      if (msg.busy) {
        rebuilt.add(
          const AssistantTurnEntry(
            parts: [
              AssistantStatusPart(
                text: 'reply in progress',
                kind: InlineNoticeKind.muted,
              ),
            ],
            isStreaming: true,
          ),
        );
      }
      final queue = <ConfirmMessage>[
        if (msg.pendingConfirm != null) msg.pendingConfirm!,
      ];
      return ChatState(
        connection: ConnectionStatus.connected,
        assistantName: msg.name,
        userName: msg.user,
        deviceName: msg.device,
        isLocal: msg.local,
        canTalk: msg.canTalk,
        wakeEnabled: msg.wakeEnabled,
        wakePhrase: msg.wakePhrase,
        followUpActive: false,
        busy: msg.busy,
        activity: msg.state,
        enabledTools: msg.tools,
        entries: rebuilt,
        confirmQueue: queue,
        lastLatency: state.lastLatency,
      );

    case StateMessage():
      final isIdle = msg.value == SageActivityState.idle;
      final isListening = msg.value == SageActivityState.listening;
      final entries = isIdle
          ? _finishStreamingTurn(state.entries)
          : state.entries;
      return state.copyWith(
        activity: msg.value,
        busy: !isIdle && !isListening,
        followUpActive: isIdle ? state.followUpActive : false,
        clearActiveTool: isIdle || msg.value == SageActivityState.speaking,
        entries: entries,
      );

    case WakeStateMessage():
      return state.copyWith(
        wakeEnabled: msg.enabled,
        wakePhrase: msg.phrase ?? state.wakePhrase,
        followUpActive: msg.enabled ? state.followUpActive : false,
      );

    case UserMessage():
      final closed = _finishStreamingTurn(state.entries);
      return state.copyWith(
        busy: true,
        followUpActive: false,
        activity: state.activity == SageActivityState.offline
            ? SageActivityState.thinking
            : state.activity,
        retainTypedInputOnBusy: false,
        entries: [
          ...closed,
          UserTurnEntry(text: msg.text, source: msg.source),
        ],
      );

    case TokenMessage():
      final updated = List<TranscriptEntry>.of(state.entries);
      if (updated.isNotEmpty && updated.last is AssistantTurnEntry) {
        final turn = updated.last as AssistantTurnEntry;
        final parts = List<AssistantPart>.of(turn.parts);
        if (parts.isNotEmpty && parts.last is AssistantTextPart) {
          final lastText = parts.last as AssistantTextPart;
          parts[parts.length - 1] = lastText.copyWith(
            text: lastText.text + msg.text,
            isStreaming: true,
          );
        } else {
          parts.add(AssistantTextPart(text: msg.text, isStreaming: true));
        }
        updated[updated.length - 1] = turn.copyWith(
          parts: parts,
          isStreaming: true,
        );
      } else {
        updated.add(
          AssistantTurnEntry(
            parts: [AssistantTextPart(text: msg.text, isStreaming: true)],
            isStreaming: true,
          ),
        );
      }
      return state.copyWith(
        busy: true,
        followUpActive: false,
        entries: updated,
      );

    case ReplyEndMessage():
      return state.copyWith(entries: _finishStreamingTurn(state.entries));

    case ToolStartMessage():
      final updated = _appendPartToCurrentAssistantTurn(
        state.entries,
        AssistantToolPart(
          name: msg.name,
          arguments: msg.arguments,
          isRunning: true,
        ),
      );
      return state.copyWith(
        busy: true,
        activity: SageActivityState.usingTool,
        activeToolName: msg.name,
        entries: updated,
      );

    case ToolMessage():
      final updated = _completeRunningToolPart(
        state.entries,
        AssistantToolPart(
          name: msg.name,
          arguments: msg.arguments,
          isRunning: false,
          chars: msg.chars,
          seconds: msg.seconds,
          tier: msg.tier,
          outcome: msg.outcome,
        ),
      );
      return state.copyWith(
        clearActiveTool: true,
        activity: state.activity == SageActivityState.usingTool
            ? SageActivityState.thinking
            : state.activity,
        entries: updated,
      );

    case ConfirmMessage():
      final exists = state.confirmQueue.any((c) => c.id == msg.id);
      return state.copyWith(
        activity: SageActivityState.awaitingConfirm,
        confirmQueue: exists
            ? state.confirmQueue
            : [...state.confirmQueue, msg],
      );

    case ConfirmDoneMessage():
      final remaining = state.confirmQueue
          .where((c) => c.id != msg.id)
          .toList();
      final String statusText;
      if (msg.reason == 'timeout') {
        statusText = 'timed out';
      } else if (msg.approved) {
        statusText = 'approved';
      } else {
        statusText = 'denied';
      }
      final updated = _appendPartToCurrentAssistantTurn(
        state.entries,
        AssistantStatusPart(text: statusText, kind: InlineNoticeKind.muted),
      );
      return state.copyWith(
        confirmQueue: remaining,
        entries: updated,
        activity: remaining.isNotEmpty
            ? SageActivityState.awaitingConfirm
            : SageActivityState.thinking,
      );

    case StoppedMessage():
      final closed = _finishStreamingTurn(state.entries);
      return state.copyWith(
        busy: false,
        followUpActive: false,
        clearActiveTool: true,
        confirmQueue: const [],
        entries: [
          ...closed,
          const InlineNoticeEntry(
            text: 'stopped',
            kind: InlineNoticeKind.muted,
          ),
        ],
      );

    case ErrorMessage():
      if (msg.code == 'busy') {
        return state.copyWith(
          retainTypedInputOnBusy: true,
          notices: _pushNotice(
            state.notices,
            ToastNotice(
              id: 'busy_${msg.seq}',
              level: 'warn',
              message: msg.message,
            ),
          ),
        );
      }
      final closed = _finishStreamingTurn(state.entries);
      return state.copyWith(
        busy: false,
        followUpActive: false,
        clearActiveTool: true,
        entries: [
          ...closed,
          InlineNoticeEntry(text: msg.message, kind: InlineNoticeKind.danger),
        ],
      );

    case NoticeMessage():
      return state.copyWith(
        notices: _pushNotice(
          state.notices,
          ToastNotice(
            id: 'notice_${msg.seq}',
            level: msg.level,
            message: msg.message,
          ),
        ),
      );

    case AudioStartMessage():
    case AudioStopMessage():
      return state;

    case LatencyMessage():
      final updated = List<TranscriptEntry>.of(state.entries);
      for (var i = updated.length - 1; i >= 0; i--) {
        final item = updated[i];
        if (item is AssistantTurnEntry) {
          updated[i] = item.copyWith(latency: msg, isStreaming: false);
          break;
        }
      }
      return state.copyWith(entries: updated, lastLatency: msg);

    case ResetMessage():
      return state.copyWith(
        entries: const [],
        confirmQueue: const [],
        followUpActive: false,
        clearLastLatency: true,
        clearActiveTool: true,
        busy: false,
      );

    case UnknownServerMessage():
      return state;
  }
}

/// Deduplicates identical toast messages and caps the stack at 2 so toasts never flood the window.
List<ToastNotice> _pushNotice(List<ToastNotice> current, ToastNotice next) {
  final filtered = current.where((n) => n.message != next.message).toList();
  filtered.add(next);
  if (filtered.length > 2) {
    return filtered.sublist(filtered.length - 2);
  }
  return filtered;
}

List<TranscriptEntry> _finishStreamingTurn(List<TranscriptEntry> entries) {
  if (entries.isEmpty) return entries;
  final updated = List<TranscriptEntry>.of(entries);
  final last = updated.last;
  if (last is AssistantTurnEntry) {
    final parts = last.parts
        .map((p) {
          if (p is AssistantTextPart && p.isStreaming) {
            return p.copyWith(isStreaming: false);
          }
          if (p is AssistantToolPart && p.isRunning) {
            return AssistantToolPart(
              name: p.name,
              arguments: p.arguments,
              isRunning: false,
              outcome: p.outcome ?? 'unknown',
            );
          }
          return p;
        })
        .toList(growable: false);
    updated[updated.length - 1] = last.copyWith(
      parts: parts,
      isStreaming: false,
    );
  }
  return updated;
}

List<TranscriptEntry> _appendPartToCurrentAssistantTurn(
  List<TranscriptEntry> entries,
  AssistantPart newPart,
) {
  final updated = List<TranscriptEntry>.of(entries);
  if (updated.isNotEmpty && updated.last is AssistantTurnEntry) {
    final turn = updated.last as AssistantTurnEntry;
    final parts = turn.parts.map((p) {
      if (p is AssistantTextPart && p.isStreaming) {
        return p.copyWith(isStreaming: false);
      }
      return p;
    }).toList();
    parts.add(newPart);
    updated[updated.length - 1] = turn.copyWith(
      parts: parts,
      isStreaming: true,
    );
  } else {
    updated.add(AssistantTurnEntry(parts: [newPart], isStreaming: true));
  }
  return updated;
}

List<TranscriptEntry> _completeRunningToolPart(
  List<TranscriptEntry> entries,
  AssistantToolPart finishedTool,
) {
  final updated = List<TranscriptEntry>.of(entries);
  if (updated.isNotEmpty && updated.last is AssistantTurnEntry) {
    final turn = updated.last as AssistantTurnEntry;
    final parts = List<AssistantPart>.of(turn.parts);
    var replaced = false;
    for (var i = parts.length - 1; i >= 0; i--) {
      final p = parts[i];
      if (p is AssistantToolPart &&
          p.isRunning &&
          p.name == finishedTool.name) {
        parts[i] = finishedTool;
        replaced = true;
        break;
      }
    }
    if (!replaced) {
      parts.add(finishedTool);
    }
    updated[updated.length - 1] = turn.copyWith(parts: parts);
  } else {
    updated.add(AssistantTurnEntry(parts: [finishedTool], isStreaming: true));
  }
  return updated;
}
