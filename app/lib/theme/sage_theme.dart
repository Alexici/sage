import 'package:flutter/material.dart';

class SageColors {
  static const Color bg = Color(0xFF0D0E11);
  static const Color surface = Color(0xFF14161A);
  static const Color surface2 = Color(0xFF1B1E24);
  static const Color border = Color(0xFF262A31);
  static const Color text = Color(0xFFE8E9EC);
  static const Color textMuted = Color(0xFF8B909A);
  static const Color accent = Color(0xFF9D8CFF);
  static const Color ok = Color(0xFF6FCF97);
  static const Color warn = Color(0xFFE8B65C);
  static const Color danger = Color(0xFFF2766B);
}

class SageTheme {
  final double textScale;

  const SageTheme({this.textScale = 1.0});

  static const double radius = 12.0;
  static const double chipRadius = 8.0;

  TextStyle get body => TextStyle(
    fontFamily: 'Inter',
    fontSize: 15.0 * textScale,
    height: 1.5,
    color: SageColors.text,
  );

  TextStyle get bodyItalicMuted => TextStyle(
    fontFamily: 'Inter',
    fontSize: 13.5 * textScale,
    height: 1.5,
    fontStyle: FontStyle.italic,
    color: SageColors.textMuted,
  );

  TextStyle get code => TextStyle(
    fontFamily: 'JetBrainsMono',
    fontSize: 13.0 * textScale,
    height: 1.45,
    color: SageColors.text,
  );

  TextStyle get monoLabel => TextStyle(
    fontFamily: 'JetBrainsMono',
    fontSize: 12.0 * textScale,
    height: 1.35,
    color: SageColors.textMuted,
  );

  TextStyle get label => TextStyle(
    fontFamily: 'Inter',
    fontSize: 12.0 * textScale,
    height: 1.35,
    color: SageColors.textMuted,
  );

  TextStyle get title => TextStyle(
    fontFamily: 'Inter',
    fontSize: 17.0 * textScale,
    height: 1.35,
    fontWeight: FontWeight.w600,
    color: SageColors.text,
  );

  ThemeData toMaterialTheme() {
    return ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: SageColors.bg,
      canvasColor: SageColors.surface,
      fontFamily: 'Inter',
      colorScheme: const ColorScheme.dark(
        surface: SageColors.surface,
        primary: SageColors.accent,
        error: SageColors.danger,
      ),
      dividerColor: SageColors.border,
      splashFactory: NoSplash.splashFactory,
      highlightColor: Colors.transparent,
    );
  }
}
