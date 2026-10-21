import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../protocol/content_parser.dart';
import '../protocol/messages.dart';
import '../theme/sage_theme.dart';
import 'widgets/frosted_box.dart';

class ConfirmDialog extends StatefulWidget {
  final ConfirmMessage confirm;
  final SageTheme theme;
  final void Function(bool approved) onDecide;
  final VoidCallback onTimeout;
  final bool inline;

  const ConfirmDialog({
    super.key,
    required this.confirm,
    required this.theme,
    required this.onDecide,
    required this.onTimeout,
    this.inline = false,
  });

  @override
  State<ConfirmDialog> createState() => _ConfirmDialogState();
}

class _ConfirmDialogState extends State<ConfirmDialog> {
  static const int _tickMs = 50;

  final FocusNode _denyFocus = FocusNode(debugLabel: 'DenyButton');
  Timer? _ticker;
  int _elapsedMs = 0;
  late final int _totalMs;
  double _fractionLeft = 1.0;
  bool _showArgs = false;
  bool _settled = false;
  int _pointerDownCount = 0;

  @override
  void initState() {
    super.initState();
    _totalMs = (widget.confirm.timeout * 1000).round().clamp(50, 3600000);
    _ticker = Timer.periodic(const Duration(milliseconds: _tickMs), (t) {
      if (!mounted || _settled) return;
      _elapsedMs += _tickMs;
      final rem = 1.0 - (_elapsedMs / _totalMs);
      if (rem <= 0.0) {
        _settled = true;
        t.cancel();
        widget.onTimeout();
      } else {
        setState(() {
          _fractionLeft = rem.clamp(0.0, 1.0);
        });
      }
    });

    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _denyFocus.requestFocus();
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    _denyFocus.dispose();
    super.dispose();
  }

  void _decide(bool approved) {
    if (_settled) return;
    _settled = true;
    _ticker?.cancel();
    widget.onDecide(approved);
  }

  void _handleAllowPointerTap() {
    if (_settled) return;
    if (_pointerDownCount > 1) {
      return; // Guard against accidental rapid double-click
    }
    _decide(true);
  }

  KeyEventResult _onKeyEvent(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent) return KeyEventResult.ignored;
    final key = event.logicalKey;
    if (key == LogicalKeyboardKey.escape) {
      _decide(false);
      return KeyEventResult.handled;
    }
    // Safety rule: Enter and Space must NEVER trigger Allow.
    if (key == LogicalKeyboardKey.enter ||
        key == LogicalKeyboardKey.numpadEnter ||
        key == LogicalKeyboardKey.space) {
      if (_denyFocus.hasFocus) {
        _decide(false);
      }
      return KeyEventResult.handled;
    }
    return KeyEventResult.ignored;
  }

  Widget _buildContent(BuildContext context) {
    final t = widget.theme;
    final isCommand = widget.confirm.tool == 'run_command';
    final title = isCommand
        ? 'Sage wants to run a command'
        : 'Sage wants to do something';

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Container(
              width: 8,
              height: 8,
              decoration: const BoxDecoration(
                color: SageColors.warn,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(child: Text(title, style: t.title)),
          ],
        ),
        const SizedBox(height: 12),
        Text(widget.confirm.summary, style: t.body),
        const SizedBox(height: 10),
        InkWell(
          canRequestFocus: false,
          borderRadius: BorderRadius.circular(SageTheme.chipRadius),
          onTap: () => setState(() => _showArgs = !_showArgs),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 4.0),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  _showArgs
                      ? Icons.keyboard_arrow_down
                      : Icons.keyboard_arrow_right,
                  size: 16,
                  color: SageColors.textMuted,
                ),
                const SizedBox(width: 4),
                Text('Arguments (${widget.confirm.tool})', style: t.monoLabel),
              ],
            ),
          ),
        ),
        if (_showArgs) ...[
          const SizedBox(height: 8),
          Container(
            constraints: const BoxConstraints(maxHeight: 160),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: SageColors.surface2.withAlpha(200),
              borderRadius: BorderRadius.circular(SageTheme.chipRadius),
              border: Border.all(color: SageColors.border),
            ),
            child: SingleChildScrollView(
              child: SelectableText(
                prettyPrintJson(widget.confirm.arguments),
                style: t.code,
              ),
            ),
          ),
        ],
        const SizedBox(height: 14),
        ClipRRect(
          borderRadius: BorderRadius.circular(2),
          child: LinearProgressIndicator(
            key: const Key('confirm_countdown_bar'),
            value: _fractionLeft,
            minHeight: 3,
            backgroundColor: SageColors.surface2,
            valueColor: const AlwaysStoppedAnimation<Color>(SageColors.warn),
          ),
        ),
        const SizedBox(height: 16),
        Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            Focus(
              focusNode: _denyFocus,
              child: Builder(
                builder: (ctx) {
                  final focused = Focus.of(ctx).hasFocus;
                  return GestureDetector(
                    onTap: () => _decide(false),
                    child: MouseRegion(
                      cursor: SystemMouseCursors.click,
                      child: Container(
                        key: const Key('confirm_deny_button'),
                        padding: const EdgeInsets.symmetric(
                          horizontal: 16,
                          vertical: 9,
                        ),
                        decoration: BoxDecoration(
                          color: Colors.transparent,
                          borderRadius: BorderRadius.circular(
                            SageTheme.chipRadius,
                          ),
                          border: Border.all(
                            color: focused
                                ? SageColors.accent
                                : SageColors.border,
                            width: 1,
                          ),
                        ),
                        child: Text('Deny', style: t.body),
                      ),
                    ),
                  );
                },
              ),
            ),
            const SizedBox(width: 12),
            Listener(
              onPointerDown: (_) => _pointerDownCount += 1,
              child: GestureDetector(
                onTap: _handleAllowPointerTap,
                child: MouseRegion(
                  cursor: SystemMouseCursors.click,
                  child: Container(
                    key: const Key('confirm_allow_button'),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 9,
                    ),
                    decoration: BoxDecoration(
                      color: SageColors.accent,
                      borderRadius: BorderRadius.circular(SageTheme.chipRadius),
                    ),
                    child: Text(
                      'Allow',
                      style: t.body.copyWith(
                        color: SageColors.bg,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    if (widget.inline) {
      return Focus(
        autofocus: true,
        onKeyEvent: _onKeyEvent,
        child: FrostedBox(
          padding: const EdgeInsets.all(18.0),
          borderRadius: SageTheme.radius,
          borderColor: SageColors.warn.withAlpha(180),
          alpha: 175,
          child: _buildContent(context),
        ),
      );
    }

    return PopScope(
      canPop: false,
      child: Focus(
        autofocus: true,
        onKeyEvent: _onKeyEvent,
        child: Dialog(
          backgroundColor: Colors.transparent,
          elevation: 0,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: FrostedBox(
              padding: const EdgeInsets.all(24.0),
              borderRadius: SageTheme.radius,
              borderColor: SageColors.warn.withAlpha(180),
              alpha: 185,
              child: _buildContent(context),
            ),
          ),
        ),
      ),
    );
  }
}
