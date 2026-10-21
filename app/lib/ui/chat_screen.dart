import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../state/chat_state.dart';
import '../state/sage_controller.dart';
import '../theme/sage_theme.dart';
import 'confirm_dialog.dart';
import 'settings_screen.dart';
import 'tabs/text_tab.dart';
import 'tabs/voice_tab.dart';
import 'widgets/frosted_box.dart';
import 'widgets/left_rail.dart';
import 'widgets/telemetry_overlays.dart';

class ChatScreen extends StatefulWidget {
  final SageController controller;

  const ChatScreen({super.key, required this.controller});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _inputCtrl = TextEditingController();
  final FocusNode _inputFocus = FocusNode(debugLabel: 'ChatInput');
  final FocusNode _windowFocus = FocusNode(debugLabel: 'WindowRoot');
  final ScrollController _scrollCtrl = ScrollController();

  bool _stickToBottom = true;
  bool _inSettings = false;
  String? _settingsAuthError;
  String _lastSubmittedText = '';

  @override
  void initState() {
    super.initState();
    _scrollCtrl.addListener(_onScroll);
    widget.controller.addListener(_onControllerChanged);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onControllerChanged);
    _scrollCtrl.dispose();
    _inputCtrl.dispose();
    _inputFocus.dispose();
    _windowFocus.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scrollCtrl.hasClients) return;
    final pos = _scrollCtrl.position;
    final nearBottom = (pos.maxScrollExtent - pos.pixels) <= 48.0;
    if (nearBottom != _stickToBottom) {
      setState(() => _stickToBottom = nearBottom);
    }
  }

  void _onControllerChanged() {
    if (!mounted) return;
    if (widget.controller.consumeShouldOpenSettings()) {
      setState(() {
        _inSettings = true;
        _settingsAuthError = widget.controller.state.connectionError;
      });
      return;
    }

    if (widget.controller.state.retainTypedInputOnBusy &&
        _inputCtrl.text.isEmpty &&
        _lastSubmittedText.isNotEmpty) {
      _inputCtrl.text = _lastSubmittedText;
      _inputCtrl.selection = TextSelection.fromPosition(
        TextPosition(offset: _inputCtrl.text.length),
      );
    }

    if (_stickToBottom) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scrollCtrl.hasClients) {
          _scrollCtrl.jumpTo(_scrollCtrl.position.maxScrollExtent);
        }
      });
    }
    setState(() {});
  }

  void _jumpToLatest() {
    setState(() => _stickToBottom = true);
    if (_scrollCtrl.hasClients) {
      _scrollCtrl.animateTo(
        _scrollCtrl.position.maxScrollExtent,
        duration: const Duration(milliseconds: 160),
        curve: Curves.easeOut,
      );
    }
  }

  void _sendCurrentText() {
    final text = _inputCtrl.text;
    if (text.trim().isEmpty) return;
    _lastSubmittedText = text;
    if (widget.controller.state.busy) {
      widget.controller.sendText(text);
      return;
    }
    if (widget.controller.sendText(text)) {
      _inputCtrl.clear();
      _jumpToLatest();
    }
  }

  Future<void> _confirmClearConversation() async {
    final t = SageTheme(textScale: widget.controller.textScale);
    final ok = await showDialog<bool>(
      context: context,
      barrierColor: Colors.black.withAlpha(120),
      builder: (ctx) => Dialog(
        backgroundColor: Colors.transparent,
        elevation: 0,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 360),
          child: FrostedBox(
            padding: const EdgeInsets.all(24),
            alpha: 185,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Clear the conversation?', style: t.title),
                const SizedBox(height: 20),
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    TextButton(
                      onPressed: () => Navigator.of(ctx).pop(false),
                      child: Text('Cancel', style: t.body),
                    ),
                    const SizedBox(width: 12),
                    ElevatedButton(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: SageColors.accent,
                        foregroundColor: SageColors.bg,
                      ),
                      onPressed: () => Navigator.of(ctx).pop(true),
                      child: const Text('Clear'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
    if (ok == true) {
      widget.controller.clearConversation();
    }
  }

  KeyEventResult _handleGlobalKeyEvent(FocusNode node, KeyEvent event) {
    final state = widget.controller.state;
    if (state.activeConfirm != null) {
      return KeyEventResult.ignored;
    }

    final isCtrl = HardwareKeyboard.instance.isControlPressed;
    if (event is KeyDownEvent) {
      if (isCtrl && event.logicalKey == LogicalKeyboardKey.keyL) {
        _confirmClearConversation();
        return KeyEventResult.handled;
      }
      if (isCtrl && event.logicalKey == LogicalKeyboardKey.comma) {
        setState(() {
          _inSettings = !_inSettings;
          _settingsAuthError = null;
        });
        return KeyEventResult.handled;
      }
      if (event.logicalKey == LogicalKeyboardKey.escape && state.busy) {
        widget.controller.stopTurn();
        return KeyEventResult.handled;
      }
      if (event.logicalKey == LogicalKeyboardKey.space &&
          !_inputFocus.hasFocus &&
          !_inSettings &&
          state.isLocal) {
        widget.controller.startTalking();
        return KeyEventResult.handled;
      }
    } else if (event is KeyUpEvent) {
      if (event.logicalKey == LogicalKeyboardKey.space &&
          widget.controller.isHoldingTalk &&
          !widget.controller.isPttLatched) {
        widget.controller.stopTalking();
        return KeyEventResult.handled;
      }
    }
    return KeyEventResult.ignored;
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final state = c.state;
    final t = SageTheme(textScale: c.textScale);

    return Focus(
      focusNode: _windowFocus,
      autofocus: true,
      onKeyEvent: _handleGlobalKeyEvent,
      child: Scaffold(
        backgroundColor: SageColors.bg,
        body: Row(
          children: [
            // 1. Far-Left Vertical Rail (Status Dot, Voice Tab, Text Tab, Gear Settings)
            LeftRail(
              controller: c,
              inSettings: _inSettings,
              onSelectTab: (tab) {
                setState(() {
                  _inSettings = false;
                  _settingsAuthError = null;
                });
                c.setActiveTab(tab);
              },
              onToggleSettings: () => setState(() {
                _inSettings = !_inSettings;
                _settingsAuthError = null;
              }),
            ),

            // 2. Main Active View
            Expanded(
              child: Stack(
                children: [
                  if (_inSettings)
                    SettingsScreen(
                      controller: c,
                      initialAuthError: _settingsAuthError,
                      onClose: () => setState(() {
                        _inSettings = false;
                        _settingsAuthError = null;
                      }),
                    )
                  else if (c.activeTab == AppTab.voice)
                    VoiceTab(controller: c, theme: t)
                  else
                    TextTab(
                      controller: c,
                      inputController: _inputCtrl,
                      inputFocus: _inputFocus,
                      scrollController: _scrollCtrl,
                      stickToBottom: _stickToBottom,
                      onJumpToLatest: _jumpToLatest,
                      onSend: _sendCurrentText,
                      onClear: _confirmClearConversation,
                      theme: t,
                    ),

                  // Protocol mismatch banner
                  if (state.connection == ConnectionStatus.versionMismatch &&
                      state.connectionError != null)
                    Positioned(
                      top: 16,
                      left: 24,
                      right: 24,
                      child: Center(
                        child: FrostedBox(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 16,
                            vertical: 10,
                          ),
                          borderColor: SageColors.danger,
                          child: Text(
                            state.connectionError!,
                            style: t.body.copyWith(color: SageColors.danger),
                          ),
                        ),
                      ),
                    ),

                  // Floating Frosted Toast Notices
                  if (state.notices.isNotEmpty)
                    Positioned(
                      top: 16,
                      left: 0,
                      right: 0,
                      child: Center(
                        child: ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 440),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: state.notices
                                .map(
                                  (n) => FrostedToastBanner(
                                    notice: n,
                                    theme: t,
                                    onDismiss: () => c.dismissNotice(n.id),
                                  ),
                                )
                                .toList(),
                          ),
                        ),
                      ),
                    ),

                  // Modal Confirmation Pop-up ONLY on Voice Tab
                  // (Text Tab renders confirmations inline inside the chat stream)
                  if (!_inSettings &&
                      c.activeTab == AppTab.voice &&
                      state.activeConfirm != null)
                    Positioned.fill(
                      child: Container(
                        color: Colors.black.withAlpha(110),
                        child: Center(
                          child: ConfirmDialog(
                            key: ValueKey(
                              'voice_confirm_${state.activeConfirm!.id}',
                            ),
                            confirm: state.activeConfirm!,
                            theme: t,
                            onDecide: (ok) =>
                                c.answerConfirm(state.activeConfirm!.id, ok),
                            onTimeout: () =>
                                c.expireConfirmLocally(state.activeConfirm!.id),
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
