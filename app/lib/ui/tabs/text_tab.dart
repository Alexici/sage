import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../state/chat_state.dart';
import '../../state/sage_controller.dart';
import '../../theme/sage_theme.dart';
import '../avatar_view.dart';
import '../confirm_dialog.dart';
import '../widgets/frosted_box.dart';
import '../widgets/telemetry_overlays.dart';
import '../widgets/transcript_items.dart';

class TextTab extends StatelessWidget {
  final SageController controller;
  final TextEditingController inputController;
  final FocusNode inputFocus;
  final ScrollController scrollController;
  final bool stickToBottom;
  final VoidCallback onJumpToLatest;
  final VoidCallback onSend;
  final VoidCallback onClear;
  final SageTheme theme;

  const TextTab({
    super.key,
    required this.controller,
    required this.inputController,
    required this.inputFocus,
    required this.scrollController,
    required this.stickToBottom,
    required this.onJumpToLatest,
    required this.onSend,
    required this.onClear,
    required this.theme,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final showSlice = constraints.maxWidth >= 850.0;
        return Row(
          children: [
            if (showSlice)
              _AvatarVerticalSlice(controller: controller, theme: theme),
            Expanded(child: _buildChatPane(context)),
          ],
        );
      },
    );
  }

  Widget _buildChatPane(BuildContext context) {
    final state = controller.state;
    final activeConfirm = state.activeConfirm;
    final itemCount = state.entries.length + (activeConfirm != null ? 1 : 0);

    return Stack(
      children: [
        // 1. Conversation Stream (with extra bottom padding so floating input bar floats over it)
        if (itemCount == 0)
          Center(
            child: Text(
              'Say something. Or type.',
              style: theme.body.copyWith(color: SageColors.textMuted),
            ),
          )
        else
          Align(
            alignment: Alignment.topCenter,
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 760),
              child: ListView.builder(
                controller: scrollController,
                padding: const EdgeInsets.fromLTRB(24, 28, 24, 110),
                itemCount: itemCount,
                itemBuilder: (context, index) {
                  // Inline tool confirmation at the bottom of the chat stream
                  if (index == state.entries.length && activeConfirm != null) {
                    return Padding(
                      padding: const EdgeInsets.only(top: 4, bottom: 16),
                      child: ConfirmDialog(
                        key: ValueKey('inline_confirm_${activeConfirm.id}'),
                        confirm: activeConfirm,
                        theme: theme,
                        inline: true,
                        onDecide: (ok) =>
                            controller.answerConfirm(activeConfirm.id, ok),
                        onTimeout: () =>
                            controller.expireConfirmLocally(activeConfirm.id),
                      ),
                    );
                  }

                  final entry = state.entries[index];
                  switch (entry) {
                    case UserTurnEntry():
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 16),
                        child: UserMessageBubble(entry: entry, theme: theme),
                      );
                    case AssistantTurnEntry():
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 16),
                        child: AssistantTurnView(
                          entry: entry,
                          assistantName: state.assistantName,
                          showTimings: controller.showTimings,
                          showToolDetails: controller.showToolDetails,
                          theme: theme,
                        ),
                      );
                    case InlineNoticeEntry():
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 12),
                        child: InlineErrorNotice(entry: entry, theme: theme),
                      );
                  }
                },
              ),
            ),
          ),

        // 2. Floating Translucent Frosted Trash Button (Top-Right Corner)
        Positioned(
          top: 16,
          right: 20,
          child: FrostedCircleButton(
            size: 38,
            alpha: 135,
            tooltip: 'Clear conversation (Ctrl+L)',
            onTap: onClear,
            child: const Icon(
              Icons.delete_outline_rounded,
              size: 18,
              color: SageColors.textMuted,
            ),
          ),
        ),

        // 3. Jump to Latest Chip
        if (!stickToBottom && itemCount > 0)
          Positioned(
            bottom: 84,
            left: 0,
            right: 0,
            child: Center(
              child: GestureDetector(
                onTap: onJumpToLatest,
                child: MouseRegion(
                  cursor: SystemMouseCursors.click,
                  child: FrostedBox(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 6,
                    ),
                    borderRadius: 16,
                    alpha: 170,
                    child: Text('Jump to latest', style: theme.label),
                  ),
                ),
              ),
            ),
          ),

        // 4. Floating Translucent Frosted Glass Input Container at Bottom
        Positioned(
          left: 24,
          right: 24,
          bottom: 18,
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 720),
              child: _FloatingFrostedInputBar(
                controller: controller,
                textController: inputController,
                focusNode: inputFocus,
                theme: theme,
                onSend: onSend,
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _AvatarVerticalSlice extends StatelessWidget {
  final SageController controller;
  final SageTheme theme;

  const _AvatarVerticalSlice({required this.controller, required this.theme});

  @override
  Widget build(BuildContext context) {
    final state = controller.state;
    final hasCards = controller.showTimings || controller.showSessionGuard;

    return Container(
      width: 220,
      decoration: const BoxDecoration(
        color: SageColors.surface,
        border: Border(right: BorderSide(color: SageColors.border, width: 1)),
      ),
      padding: const EdgeInsets.all(18),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Spacer(),
          AvatarView(
            state: state.effectiveCircleState,
            size: hasCards ? 148 : 176,
          ),
          const SizedBox(height: 14),
          Text(
            state.stateLabel.toUpperCase(),
            style: theme.monoLabel.copyWith(letterSpacing: 1.8),
          ),
          const Spacer(),
          if (controller.showTimings) ...[
            SizedBox(
              width: double.infinity,
              child: TelemetryDebugCard(
                latency: state.lastLatency,
                theme: theme,
              ),
            ),
            const SizedBox(height: 10),
          ],
          if (controller.showSessionGuard)
            SizedBox(
              width: double.infinity,
              child: SessionGuardCard(state: state, theme: theme),
            ),
        ],
      ),
    );
  }
}

class _FloatingFrostedInputBar extends StatelessWidget {
  final SageController controller;
  final TextEditingController textController;
  final FocusNode focusNode;
  final SageTheme theme;
  final VoidCallback onSend;

  const _FloatingFrostedInputBar({
    required this.controller,
    required this.textController,
    required this.focusNode,
    required this.theme,
    required this.onSend,
  });

  @override
  Widget build(BuildContext context) {
    final state = controller.state;
    final hasConfirm = state.activeConfirm != null;
    final connected = state.connection == ConnectionStatus.connected;
    final enabled = connected && !hasConfirm;

    String placeholder = 'Message Sage...';
    if (hasConfirm) {
      placeholder = 'Waiting for your answer';
    } else if (!connected) {
      placeholder = state.connection == ConnectionStatus.reconnecting
          ? 'Reconnecting...'
          : 'Offline';
    }

    return FrostedBox(
      borderRadius: 26,
      alpha: 145, // Translucent frosted glass
      blurSigma: 18.0,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            child: Focus(
              onKeyEvent: (node, event) {
                if (event is KeyDownEvent &&
                    (event.logicalKey == LogicalKeyboardKey.enter ||
                        event.logicalKey == LogicalKeyboardKey.numpadEnter)) {
                  if (HardwareKeyboard.instance.isShiftPressed) {
                    return KeyEventResult.ignored; // insert newline
                  }
                  onSend();
                  return KeyEventResult.handled;
                }
                return KeyEventResult.ignored;
              },
              child: TextField(
                controller: textController,
                focusNode: focusNode,
                enabled: enabled,
                minLines: 1,
                maxLines: 5,
                maxLength: 4000,
                buildCounter: (
                  context, {
                  required currentLength,
                  required isFocused,
                  required maxLength,
                }) => null,
                style: theme.body,
                cursorColor: SageColors.accent,
                decoration: InputDecoration(
                  hintText: placeholder,
                  hintStyle: theme.body.copyWith(color: SageColors.textMuted),
                  isDense: true,
                  border: InputBorder.none,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 6,
                    vertical: 8,
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          // Round action button on the right with Enter arrow symbol (or Stop square when busy)
          GestureDetector(
            onTap: state.busy ? controller.stopTurn : (enabled ? onSend : null),
            child: MouseRegion(
              cursor: (state.busy || enabled)
                  ? SystemMouseCursors.click
                  : SystemMouseCursors.basic,
              child: Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: state.busy
                      ? SageColors.danger
                      : (enabled
                            ? SageColors.accent
                            : SageColors.surface2.withAlpha(160)),
                  shape: BoxShape.circle,
                ),
                alignment: Alignment.center,
                child: Icon(
                  state.busy
                      ? Icons.stop_rounded
                      : Icons.keyboard_return_rounded,
                  size: 18,
                  color: (state.busy || enabled)
                      ? SageColors.bg
                      : SageColors.textMuted,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
