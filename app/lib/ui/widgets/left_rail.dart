import 'package:flutter/material.dart';

import '../../state/chat_state.dart';
import '../../state/sage_controller.dart';
import '../../theme/sage_theme.dart';

class LeftRail extends StatelessWidget {
  final SageController controller;
  final bool inSettings;
  final ValueChanged<AppTab> onSelectTab;
  final VoidCallback onToggleSettings;

  const LeftRail({
    super.key,
    required this.controller,
    required this.inSettings,
    required this.onSelectTab,
    required this.onToggleSettings,
  });

  @override
  Widget build(BuildContext context) {
    final state = controller.state;
    final dotColor = switch (state.connection) {
      ConnectionStatus.connected => SageColors.ok,
      ConnectionStatus.connecting ||
      ConnectionStatus.reconnecting => SageColors.warn,
      _ => SageColors.textMuted,
    };

    final statusLabel = switch (state.connection) {
      ConnectionStatus.connected => 'Connected · ${controller.serverAddress}',
      ConnectionStatus.connecting => 'Connecting · ${controller.serverAddress}',
      ConnectionStatus.reconnecting =>
        'Reconnecting · ${controller.serverAddress}',
      ConnectionStatus.authFailed => 'Auth failed',
      ConnectionStatus.versionMismatch => 'Protocol mismatch',
      ConnectionStatus.offline => 'Offline · ${controller.serverAddress}',
    };

    return Container(
      width: 52,
      decoration: const BoxDecoration(
        color: Color(0xFF101216),
        border: Border(right: BorderSide(color: SageColors.border, width: 1)),
      ),
      padding: const EdgeInsets.symmetric(vertical: 14),
      child: Column(
        children: [
          Tooltip(
            message: statusLabel,
            child: Container(
              width: 10,
              height: 10,
              decoration: BoxDecoration(
                color: dotColor,
                shape: BoxShape.circle,
                boxShadow: state.connection == ConnectionStatus.connected
                    ? [
                        BoxShadow(
                          color: dotColor.withAlpha(110),
                          blurRadius: 8,
                          spreadRadius: 1,
                        ),
                      ]
                    : null,
              ),
            ),
          ),
          const SizedBox(height: 14),
          Container(width: 22, height: 1, color: SageColors.border),
          const SizedBox(height: 12),
          _RailIconButton(
            icon: Icons.graphic_eq_rounded,
            tooltip: 'Voice mode',
            active: !inSettings && controller.activeTab == AppTab.voice,
            onTap: () => onSelectTab(AppTab.voice),
          ),
          const SizedBox(height: 10),
          _RailIconButton(
            icon: Icons.chat_bubble_outline_rounded,
            tooltip: 'Text chat mode',
            active: !inSettings && controller.activeTab == AppTab.text,
            onTap: () => onSelectTab(AppTab.text),
          ),
          const Spacer(),
          _RailIconButton(
            icon: inSettings ? Icons.settings : Icons.settings_outlined,
            tooltip: 'Settings (Ctrl+,)',
            active: inSettings,
            onTap: onToggleSettings,
          ),
        ],
      ),
    );
  }
}

class _RailIconButton extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final bool active;
  final VoidCallback onTap;

  const _RailIconButton({
    required this.icon,
    required this.tooltip,
    required this.active,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: GestureDetector(
        onTap: onTap,
        child: MouseRegion(
          cursor: SystemMouseCursors.click,
          child: SizedBox(
            width: 52,
            height: 38,
            child: Stack(
              alignment: Alignment.center,
              children: [
                if (active)
                  Positioned(
                    left: 0,
                    child: Container(
                      width: 3,
                      height: 20,
                      decoration: BoxDecoration(
                        color: SageColors.accent,
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(
                    color: active
                        ? SageColors.accent.withAlpha(36)
                        : Colors.transparent,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(
                      color: active
                          ? SageColors.accent.withAlpha(110)
                          : Colors.transparent,
                    ),
                  ),
                  child: Icon(
                    icon,
                    size: 19,
                    color: active ? SageColors.accent : SageColors.textMuted,
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
