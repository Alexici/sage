import 'package:flutter/material.dart';

import '../../protocol/messages.dart';
import '../../state/chat_state.dart';
import '../../theme/sage_theme.dart';
import 'frosted_box.dart';

class TelemetryDebugCard extends StatelessWidget {
  final LatencyMessage? latency;
  final SageTheme theme;

  const TelemetryDebugCard({
    super.key,
    required this.latency,
    required this.theme,
  });

  String _fmt(double? v) => v != null ? '${v.toStringAsFixed(2)} s' : '—';

  @override
  Widget build(BuildContext context) {
    final l = latency;
    final sound = l?.realStart ?? l?.firstChunkReady;
    return FrostedBox(
      padding: const EdgeInsets.all(12),
      borderRadius: 10,
      alpha: 150,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('TELEMETRY', style: theme.monoLabel.copyWith(fontSize: 10)),
          const SizedBox(height: 6),
          _kv('First token', _fmt(l?.firstToken)),
          _kv('First sound', _fmt(sound)),
          _kv(
            'Held',
            l?.held != null ? '${l!.held!.toStringAsFixed(1)} s' : '—',
          ),
          _kv(
            'Gaps',
            l?.gaps != null ? '${l!.gaps!.toStringAsFixed(1)} s' : '—',
          ),
        ],
      ),
    );
  }

  Widget _kv(String k, String v) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 1.5),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text('$k: ', style: theme.monoLabel),
          Text(v, style: theme.monoLabel.copyWith(color: SageColors.text)),
        ],
      ),
    );
  }
}

class SessionGuardCard extends StatelessWidget {
  final ChatState state;
  final SageTheme theme;

  const SessionGuardCard({super.key, required this.state, required this.theme});

  @override
  Widget build(BuildContext context) {
    return FrostedBox(
      padding: const EdgeInsets.all(12),
      borderRadius: 10,
      alpha: 150,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('SESSION GUARD', style: theme.monoLabel.copyWith(fontSize: 10)),
          const SizedBox(height: 6),
          Text(
            'Device: ${state.deviceName.isEmpty ? "local" : state.deviceName}',
            style: theme.monoLabel.copyWith(color: SageColors.text),
          ),
          const SizedBox(height: 2),
          Text(
            'Tools: ${state.enabledTools.length} active',
            style: theme.monoLabel.copyWith(color: SageColors.text),
          ),
          const SizedBox(height: 2),
          Text(
            'Mic: ${state.isLocal ? "local" : "remote"}',
            style: theme.monoLabel.copyWith(
              color: state.isLocal ? SageColors.ok : SageColors.textMuted,
            ),
          ),
        ],
      ),
    );
  }
}

class FrostedToastBanner extends StatelessWidget {
  final ToastNotice notice;
  final SageTheme theme;
  final VoidCallback onDismiss;

  const FrostedToastBanner({
    super.key,
    required this.notice,
    required this.theme,
    required this.onDismiss,
  });

  @override
  Widget build(BuildContext context) {
    final accent = notice.level == 'warn'
        ? SageColors.warn
        : (notice.level == 'danger' ? SageColors.danger : SageColors.accent);

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: GestureDetector(
        onTap: onDismiss,
        child: MouseRegion(
          cursor: SystemMouseCursors.click,
          child: FrostedBox(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
            borderRadius: 20,
            borderColor: accent.withAlpha(160),
            alpha: 175,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 7,
                  height: 7,
                  decoration: BoxDecoration(
                    color: accent,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Flexible(
                  child: Text(
                    notice.message,
                    style: theme.label.copyWith(color: SageColors.text),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
