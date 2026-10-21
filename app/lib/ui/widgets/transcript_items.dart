import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../protocol/content_parser.dart';
import '../../protocol/messages.dart';
import '../../state/chat_state.dart';
import '../../theme/sage_theme.dart';
import 'frosted_box.dart';

class UserMessageBubble extends StatelessWidget {
  final UserTurnEntry entry;
  final SageTheme theme;

  const UserMessageBubble({
    super.key,
    required this.entry,
    required this.theme,
  });

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerRight,
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxWidth: MediaQuery.sizeOf(context).width * 0.78,
        ),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          decoration: BoxDecoration(
            color: SageColors.surface2,
            borderRadius: BorderRadius.circular(SageTheme.radius),
            border: Border.all(color: SageColors.border),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (entry.source == 'voice') ...[
                const Padding(
                  padding: EdgeInsets.only(top: 3, right: 6),
                  child: Icon(Icons.mic, size: 14, color: SageColors.accent),
                ),
              ],
              Flexible(child: SelectableText(entry.text, style: theme.body)),
            ],
          ),
        ),
      ),
    );
  }
}

class AssistantTurnView extends StatelessWidget {
  final AssistantTurnEntry entry;
  final String assistantName;
  final bool showTimings;
  final bool showToolDetails;
  final SageTheme theme;

  const AssistantTurnView({
    super.key,
    required this.entry,
    required this.assistantName,
    required this.showTimings,
    required this.showToolDetails,
    required this.theme,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(assistantName, style: theme.label),
        const SizedBox(height: 4),
        for (var i = 0; i < entry.parts.length; i++)
          _buildPart(entry.parts[i], isLastPart: i == entry.parts.length - 1),
        if (entry.latency != null && showTimings) ...[
          const SizedBox(height: 6),
          TimingLineView(latency: entry.latency!, theme: theme),
        ],
      ],
    );
  }

  Widget _buildPart(AssistantPart part, {required bool isLastPart}) {
    switch (part) {
      case AssistantTextPart():
        final showCaret = entry.isStreaming && part.isStreaming && isLastPart;
        return Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: _SegmentContentView(
            rawText: part.text,
            isStreaming: showCaret,
            theme: theme,
          ),
        );
      case AssistantToolPart():
        if (!showToolDetails) return const SizedBox.shrink();
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: ToolRowView(tool: part, theme: theme),
        );
      case AssistantStatusPart():
        final color = switch (part.kind) {
          InlineNoticeKind.danger => SageColors.danger,
          InlineNoticeKind.warn => SageColors.warn,
          InlineNoticeKind.muted => SageColors.textMuted,
        };
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 2),
          child: Text(part.text, style: theme.label.copyWith(color: color)),
        );
    }
  }
}

class InlineErrorNotice extends StatelessWidget {
  final InlineNoticeEntry entry;
  final SageTheme theme;

  const InlineErrorNotice({
    super.key,
    required this.entry,
    required this.theme,
  });

  @override
  Widget build(BuildContext context) {
    final color = switch (entry.kind) {
      InlineNoticeKind.danger => SageColors.danger,
      InlineNoticeKind.warn => SageColors.warn,
      InlineNoticeKind.muted => SageColors.textMuted,
    };

    if (entry.kind == InlineNoticeKind.muted) {
      return Text(entry.text, style: theme.label.copyWith(color: color));
    }

    return FrostedBox(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      borderRadius: SageTheme.chipRadius,
      borderColor: color.withAlpha(170),
      alpha: 155,
      child: Text(entry.text, style: theme.body.copyWith(color: color)),
    );
  }
}

class _SegmentContentView extends StatelessWidget {
  final String rawText;
  final bool isStreaming;
  final SageTheme theme;

  const _SegmentContentView({
    required this.rawText,
    required this.isStreaming,
    required this.theme,
  });

  @override
  Widget build(BuildContext context) {
    final blocks = parseSegmentContent(rawText, isStreaming: isStreaming);
    if (blocks.isEmpty && isStreaming) {
      return const _BlinkingCaret();
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < blocks.length; i++)
          _buildBlock(blocks[i], isLastBlock: i == blocks.length - 1),
      ],
    );
  }

  Widget _buildBlock(ParsedBlock block, {required bool isLastBlock}) {
    switch (block) {
      case ProseBlock():
        return RichText(
          text: TextSpan(
            children: [
              for (final span in block.spans)
                if (span.kind == InlineSpanKind.voiceTag)
                  TextSpan(text: span.text, style: theme.bodyItalicMuted)
                else
                  TextSpan(text: span.text, style: theme.body),
              if (isStreaming && isLastBlock)
                const WidgetSpan(
                  alignment: PlaceholderAlignment.middle,
                  child: Padding(
                    padding: EdgeInsets.only(left: 2),
                    child: _BlinkingCaret(),
                  ),
                ),
            ],
          ),
        );

      case CodeBlock():
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: CodeBlockCard(block: block, theme: theme),
        );
    }
  }
}

class _BlinkingCaret extends StatefulWidget {
  const _BlinkingCaret();

  @override
  State<_BlinkingCaret> createState() => _BlinkingCaretState();
}

class _BlinkingCaretState extends State<_BlinkingCaret>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 550),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: _ctrl,
      child: Container(width: 2, height: 15, color: SageColors.accent),
    );
  }
}

class CodeBlockCard extends StatefulWidget {
  final CodeBlock block;
  final SageTheme theme;

  const CodeBlockCard({super.key, required this.block, required this.theme});

  @override
  State<CodeBlockCard> createState() => _CodeBlockCardState();
}

class _CodeBlockCardState extends State<CodeBlockCard> {
  bool _copied = false;

  void _copy() {
    Clipboard.setData(ClipboardData(text: widget.block.code));
    setState(() => _copied = true);
    Future.delayed(const Duration(seconds: 1), () {
      if (mounted) setState(() => _copied = false);
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = widget.theme;
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        color: SageColors.surface2,
        borderRadius: BorderRadius.circular(SageTheme.radius),
        border: Border.all(color: SageColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: const BoxDecoration(
              border: Border(bottom: BorderSide(color: SageColors.border)),
            ),
            child: Row(
              children: [
                Text(
                  widget.block.language.isEmpty
                      ? (widget.block.isClosed ? 'code' : 'code (streaming...)')
                      : widget.block.language,
                  style: t.monoLabel,
                ),
                const Spacer(),
                GestureDetector(
                  onTap: _copy,
                  child: MouseRegion(
                    cursor: SystemMouseCursors.click,
                    child: Text(
                      _copied ? 'Copied' : 'Copy',
                      style: t.monoLabel.copyWith(
                        color: _copied ? SageColors.ok : SageColors.textMuted,
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.all(12),
            child: SelectableText(widget.block.code, style: t.code),
          ),
        ],
      ),
    );
  }
}

class ToolRowView extends StatefulWidget {
  final AssistantToolPart tool;
  final SageTheme theme;

  const ToolRowView({super.key, required this.tool, required this.theme});

  @override
  State<ToolRowView> createState() => _ToolRowViewState();
}

class _ToolRowViewState extends State<ToolRowView> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final tool = widget.tool;
    final t = widget.theme;
    final outcome = tool.outcome ?? 'ran';

    Color? chipColor;
    if (outcome == 'denied') {
      chipColor = SageColors.warn;
    } else if (outcome == 'blocked' ||
        outcome == 'error' ||
        outcome == 'unknown') {
      chipColor = SageColors.danger;
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        GestureDetector(
          onTap: () => setState(() => _expanded = !_expanded),
          child: MouseRegion(
            cursor: SystemMouseCursors.click,
            child: Row(
              children: [
                const Icon(
                  Icons.terminal,
                  size: 13,
                  color: SageColors.textMuted,
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    formatToolCallInline(tool.name, tool.arguments),
                    style: t.monoLabel,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: 8),
                if (tool.isRunning)
                  const SizedBox(
                    width: 11,
                    height: 11,
                    child: CircularProgressIndicator(
                      strokeWidth: 1.5,
                      valueColor: AlwaysStoppedAnimation<Color>(
                        SageColors.textMuted,
                      ),
                    ),
                  )
                else ...[
                  Text(
                    '${tool.chars ?? 0} chars, ${(tool.seconds ?? 0.0).toStringAsFixed(1)} s',
                    style: t.monoLabel,
                  ),
                  if (chipColor != null) ...[
                    const SizedBox(width: 6),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 6,
                        vertical: 1,
                      ),
                      decoration: BoxDecoration(
                        color: SageColors.surface2,
                        borderRadius: BorderRadius.circular(
                          SageTheme.chipRadius,
                        ),
                        border: Border.all(color: chipColor),
                      ),
                      child: Text(
                        outcome,
                        style: t.monoLabel.copyWith(color: chipColor),
                      ),
                    ),
                  ],
                ],
              ],
            ),
          ),
        ),
        if (_expanded) ...[
          const SizedBox(height: 6),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: SageColors.surface2,
              borderRadius: BorderRadius.circular(SageTheme.chipRadius),
              border: Border.all(color: SageColors.border),
            ),
            child: SelectableText(
              prettyPrintJson(tool.arguments),
              style: t.code,
            ),
          ),
        ],
      ],
    );
  }
}

class TimingLineView extends StatefulWidget {
  final LatencyMessage latency;
  final SageTheme theme;

  const TimingLineView({super.key, required this.latency, required this.theme});

  @override
  State<TimingLineView> createState() => _TimingLineViewState();
}

class _TimingLineViewState extends State<TimingLineView> {
  bool _expanded = false;

  String _fmtSec(double? v) => v != null ? '${v.toStringAsFixed(2)} s' : 'n/a';

  @override
  Widget build(BuildContext context) {
    final l = widget.latency;
    final t = widget.theme;
    final speechVal = l.realStart ?? l.firstChunkReady;
    final speechStr = speechVal != null
        ? '${speechVal.toStringAsFixed(1)} s'
        : 'n/a';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        GestureDetector(
          onTap: () => setState(() => _expanded = !_expanded),
          child: MouseRegion(
            cursor: SystemMouseCursors.click,
            child: Text(
              'first token ${_fmtSec(l.firstToken)}, speech $speechStr',
              style: t.monoLabel,
            ),
          ),
        ),
        if (_expanded) ...[
          const SizedBox(height: 4),
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: SageColors.surface2,
              borderRadius: BorderRadius.circular(SageTheme.chipRadius),
              border: Border.all(color: SageColors.border),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'first token: ${_fmtSec(l.firstToken)}',
                  style: t.monoLabel,
                ),
                Text(
                  'first audio ready: ${_fmtSec(l.firstChunkReady)}',
                  style: t.monoLabel,
                ),
                Text(
                  'speech started: ${_fmtSec(l.realStart)}',
                  style: t.monoLabel,
                ),
                Text(
                  'first chunk: ${l.firstChunk ?? "n/a"}',
                  style: t.monoLabel,
                ),
                Text(
                  'held: ${(l.held ?? 0.0).toStringAsFixed(1)} s',
                  style: t.monoLabel,
                ),
                Text(
                  'gaps: ${(l.gaps ?? 0.0).toStringAsFixed(1)} s',
                  style: t.monoLabel,
                ),
                Text('filler: ${l.filler ?? "no"}', style: t.monoLabel),
                if (l.tools.isNotEmpty)
                  Text('tools: ${l.tools.join(", ")}', style: t.monoLabel),
              ],
            ),
          ),
        ],
      ],
    );
  }
}
