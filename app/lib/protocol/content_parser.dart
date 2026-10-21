import 'dart:convert';

enum InlineSpanKind { plain, voiceTag }

class InlinePart {
  final InlineSpanKind kind;
  final String text;

  const InlinePart.plain(this.text) : kind = InlineSpanKind.plain;
  const InlinePart.voiceTag(this.text) : kind = InlineSpanKind.voiceTag;

  @override
  bool operator ==(Object other) =>
      other is InlinePart && other.kind == kind && other.text == text;

  @override
  int get hashCode => Object.hash(kind, text);
}

sealed class ParsedBlock {
  const ParsedBlock();
}

class ProseBlock extends ParsedBlock {
  final List<InlinePart> spans;
  const ProseBlock(this.spans);
}

class CodeBlock extends ParsedBlock {
  final String language;
  final String code;
  final bool isClosed;

  const CodeBlock({
    required this.language,
    required this.code,
    required this.isClosed,
  });
}

final RegExp _voiceTagRe = RegExp(r'\[(chuckle|laugh|sigh)\]');

const Map<String, String> _tagReplacements = {
  'chuckle': '(chuckles)',
  'laugh': '(laughs)',
  'sigh': '(sighs)',
};

/// Parses a segment's text into ProseBlocks and CodeBlocks.
/// Handles ``` fences and [chuckle]/[laugh]/[sigh] tags split across streaming tokens.
List<ParsedBlock> parseSegmentContent(String raw, {bool isStreaming = false}) {
  if (raw.isEmpty) return const [];

  var working = raw;
  if (isStreaming) {
    // Hold back 1-2 trailing backticks at the very end of a streaming buffer
    // in case the next token completes a ``` fence.
    if (working.endsWith('``') && !working.endsWith('```')) {
      working = working.substring(0, working.length - 2);
    } else if (working.endsWith('`') && !working.endsWith('``')) {
      working = working.substring(0, working.length - 1);
    }
  }

  final blocks = <ParsedBlock>[];
  var cursor = 0;
  var inCode = false;
  var fenceOpenStart = 0;

  while (cursor <= working.length) {
    final idx = working.indexOf('```', cursor);
    if (idx < 0) {
      if (inCode) {
        final parsed = _extractCodeLanguageAndBody(
          working.substring(fenceOpenStart),
          isClosed: false,
        );
        blocks.add(parsed);
      } else {
        final proseText = working.substring(cursor);
        final spans = parseVoiceTags(proseText, isStreaming: isStreaming);
        if (spans.isNotEmpty) {
          blocks.add(ProseBlock(spans));
        }
      }
      break;
    }

    if (!inCode) {
      final proseText = working.substring(cursor, idx);
      final spans = parseVoiceTags(proseText, isStreaming: false);
      if (spans.isNotEmpty) {
        blocks.add(ProseBlock(spans));
      }
      inCode = true;
      fenceOpenStart = idx + 3;
      cursor = fenceOpenStart;
    } else {
      final codeContent = working.substring(fenceOpenStart, idx);
      blocks.add(_extractCodeLanguageAndBody(codeContent, isClosed: true));
      inCode = false;
      cursor = idx + 3;
    }
  }

  return blocks;
}

CodeBlock _extractCodeLanguageAndBody(
  String rawCode, {
  required bool isClosed,
}) {
  var language = '';
  var body = rawCode;
  final nl = rawCode.indexOf('\n');
  if (nl >= 0) {
    final firstLine = rawCode.substring(0, nl).trim();
    if (RegExp(r'^[a-zA-Z0-9_+-]*$').hasMatch(firstLine)) {
      language = firstLine;
      body = rawCode.substring(nl + 1);
    }
  } else if (!isClosed &&
      RegExp(r'^[a-zA-Z0-9_+-]+$').hasMatch(rawCode.trim())) {
    language = rawCode.trim();
    body = '';
  }
  if (isClosed && body.endsWith('\n')) {
    body = body.substring(0, body.length - 1);
  }
  return CodeBlock(language: language, code: body, isClosed: isClosed);
}

List<InlinePart> parseVoiceTags(String input, {bool isStreaming = false}) {
  if (input.isEmpty) return const [];
  var text = input;

  if (isStreaming) {
    final lastBracket = text.lastIndexOf('[');
    if (lastBracket >= 0 && !text.substring(lastBracket).contains(']')) {
      final tail = text.substring(lastBracket).toLowerCase();
      const candidates = ['[chuckle]', '[laugh]', '[sigh]'];
      if (candidates.any((c) => c.startsWith(tail))) {
        text = text.substring(0, lastBracket);
      }
    }
  }

  final parts = <InlinePart>[];
  var lastEnd = 0;
  for (final match in _voiceTagRe.allMatches(text)) {
    if (match.start > lastEnd) {
      parts.add(InlinePart.plain(text.substring(lastEnd, match.start)));
    }
    final tag = match.group(1)!;
    parts.add(InlinePart.voiceTag(_tagReplacements[tag] ?? '($tag)'));
    lastEnd = match.end;
  }
  if (lastEnd < text.length) {
    parts.add(InlinePart.plain(text.substring(lastEnd)));
  }
  return parts;
}

/// Formats `web_search("weather in Cluj")` one-line tool call summary.
String formatToolCallInline(
  String name,
  String rawArguments, {
  int maxLen = 56,
}) {
  final trimmed = rawArguments.trim();
  if (trimmed.isEmpty || trimmed == '{}') return '$name()';
  try {
    final decoded = jsonDecode(trimmed);
    if (decoded is Map<String, dynamic>) {
      if (decoded.isEmpty) return '$name()';
      if (decoded.length == 1) {
        final v = decoded.values.first;
        if (v is String) {
          return '$name("${_clipOneLine(v, maxLen)}")';
        }
        return '$name(${_clipOneLine(jsonEncode(v), maxLen)})';
      }
      const primaryKeys = ['query', 'path', 'command', 'task'];
      for (final k in primaryKeys) {
        if (decoded[k] is String) {
          final mainVal = _clipOneLine(decoded[k] as String, maxLen - 6);
          return '$name("$mainVal", …)';
        }
      }
      final pairs = decoded.entries
          .map((e) => '${e.key}:${jsonEncode(e.value)}')
          .join(', ');
      return '$name(${_clipOneLine(pairs, maxLen)})';
    }
  } catch (_) {}
  return '$name(${_clipOneLine(trimmed, maxLen)})';
}

String prettyPrintJson(dynamic raw) {
  try {
    final obj = raw is String ? jsonDecode(raw) : raw;
    return const JsonEncoder.withIndent('  ').convert(obj);
  } catch (_) {
    return raw.toString();
  }
}

String _clipOneLine(String s, int maxLen) {
  final single = s.replaceAll(RegExp(r'\s+'), ' ').trim();
  if (single.length <= maxLen) return single;
  return '${single.substring(0, maxLen - 1)}…';
}
