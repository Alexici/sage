import 'package:flutter/material.dart';

import '../../protocol/messages.dart';
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
    final wakeActive = state.wakeEnabled;
    final wakeTriggered =
        state.activity == SageActivityState.listening && !latched;
    final canTalk =
        state.canTalk &&
        state.connection == ConnectionStatus.connected &&
        state.activeConfirm == null;

    String bottomHint;
    if (!state.canTalk) {
      bottomHint = 'Voice input is not enabled on the server';
    } else if (wakeTriggered) {
      bottomHint = 'Listening... (Click mic to finish early, Esc to cancel)';
    } else if (latched) {
      bottomHint = 'Push-to-Talk active · Click to stop';
    } else if (recording) {
      bottomHint = 'Listening... release to send';
    } else if (wakeActive) {
      bottomHint = 'Say "${state.wakePhrase}"  ·  Or Click / Hold mic to talk';
    } else {
      bottomHint = 'Click: Push-to-Talk  ·  Hold: Hold-to-Talk';
    }

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
                  color: (recording || wakeActive)
                      ? SageColors.accent
                      : SageColors.textMuted,
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

        // 3. Bottom Translucent Controls: Stop / Wake Word Toggle Pill / Round Mic Button
        Positioned(
          bottom: 24,
          left: 0,
          right: 0,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (state.busy || recording)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: FrostedCircleButton(
                    size: 38,
                    alpha: 145,
                    borderColor: SageColors.danger.withAlpha(180),
                    tooltip: 'Cancel / Stop (Esc)',
                    onTap: controller.stopTurn,
                    child: const Icon(
                      Icons.close_rounded,
                      size: 18,
                      color: SageColors.danger,
                    ),
                  ),
                ),

              // Frosted Wake Word Toggle Pill ("Hey Sage")
              Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: GestureDetector(
                  onTap: canTalk ? controller.toggleWakeWord : null,
                  child: MouseRegion(
                    cursor: canTalk
                        ? SystemMouseCursors.click
                        : SystemMouseCursors.basic,
                    child: FrostedBox(
                      borderRadius: 18,
                      alpha: wakeActive ? 175 : 120,
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 6,
                      ),
                      borderColor: wakeActive
                          ? SageColors.ok.withAlpha(180)
                          : SageColors.border,
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Container(
                            width: 7,
                            height: 7,
                            decoration: BoxDecoration(
                              color: wakeActive
                                  ? SageColors.ok
                                  : SageColors.textMuted,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            wakeActive
                                ? 'Wake word: "${state.wakePhrase}" (On)'
                                : 'Wake word: Off',
                            style: theme.monoLabel.copyWith(
                              fontSize: 11.5,
                              color: wakeActive
                                  ? SageColors.text
                                  : SageColors.textMuted,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),

              // Translucent Round Dual-Function Microphone Button
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
              Text(bottomHint, style: theme.label.copyWith(fontSize: 11.5)),
            ],
          ),
        ),
      ],
    );
  }
}
