import 'package:flutter/material.dart';

import '../../state/chat_state.dart';
import '../../state/sage_controller.dart';
import '../../theme/sage_theme.dart';
import '../avatar_view.dart';
import '../widgets/frosted_box.dart';
import '../widgets/telemetry_overlays.dart';

class VoiceTab extends StatelessWidget {
  final SageController controller;
  final SageTheme theme;

  const VoiceTab({super.key, required this.controller, required this.theme});

  @override
  Widget build(BuildContext context) {
    final state = controller.state;
    final recording = controller.isHoldingTalk;
    final latched = controller.isPttLatched;
    final canTalk =
        state.isLocal &&
        state.connection == ConnectionStatus.connected &&
        state.activeConfirm == null;

    return Stack(
      children: [
        // 1. Centered Neon Sound-Wave Sphere + State Label
        Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              AvatarView(state: state.effectiveCircleState, size: 250),
              const SizedBox(height: 18),
              Text(
                state.stateLabel.toUpperCase(),
                style: theme.monoLabel.copyWith(
                  letterSpacing: 2.2,
                  color: recording ? SageColors.accent : SageColors.textMuted,
                ),
              ),
            ],
          ),
        ),

        // 2. Corner Debug Panels (Only shown if enabled in Settings)
        if (controller.showTimings)
          Positioned(
            top: 20,
            left: 20,
            child: TelemetryDebugCard(latency: state.lastLatency, theme: theme),
          ),
        if (controller.showSessionGuard)
          Positioned(
            top: 20,
            right: 20,
            child: SessionGuardCard(state: state, theme: theme),
          ),

        // 3. Bottom Translucent Round Dual-Function Microphone Button
        Positioned(
          bottom: 28,
          left: 0,
          right: 0,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (state.busy && !recording)
                Padding(
                  padding: const EdgeInsets.only(bottom: 14),
                  child: FrostedCircleButton(
                    size: 40,
                    alpha: 145,
                    borderColor: SageColors.danger.withAlpha(180),
                    tooltip: 'Stop reply (Esc)',
                    onTap: controller.stopTurn,
                    child: const Icon(
                      Icons.stop_rounded,
                      size: 20,
                      color: SageColors.danger,
                    ),
                  ),
                ),
              Listener(
                onPointerDown: canTalk
                    ? (_) => controller.onMicPointerDown()
                    : null,
                onPointerUp: canTalk
                    ? (_) => controller.onMicPointerUp()
                    : null,
                onPointerCancel: canTalk
                    ? (_) => controller.stopTalking()
                    : null,
                child: MouseRegion(
                  cursor: canTalk
                      ? SystemMouseCursors.click
                      : SystemMouseCursors.basic,
                  child: SizedBox(
                    width: 68,
                    height: 68,
                    child: FrostedBox(
                      borderRadius: 34,
                      alpha: recording ? 195 : 125,
                      tintColor: recording
                          ? SageColors.accent
                          : SageColors.surface2,
                      borderColor: recording
                          ? SageColors.accent
                          : (canTalk
                                ? SageColors.accent.withAlpha(150)
                                : SageColors.border),
                      child: Center(
                        child: Icon(
                          recording ? Icons.mic : Icons.mic_none_rounded,
                          size: 28,
                          color: recording
                              ? SageColors.bg
                              : (canTalk
                                    ? SageColors.text
                                    : SageColors.textMuted),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 10),
              Text(
                !state.isLocal
                    ? 'Voice input requires local host mic'
                    : (latched
                          ? 'Push-to-Talk active · Click to stop'
                          : (recording
                                ? 'Listening... release to send'
                                : 'Click: Push-to-Talk  ·  Hold: Hold-to-Talk')),
                style: theme.label.copyWith(fontSize: 11.5),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
